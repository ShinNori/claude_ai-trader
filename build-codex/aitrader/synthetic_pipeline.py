"""Whole shared synthetic market through the real generator, without sending."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from aitrader_ops.ledger import Ledger
from aitrader_ops.models import JST
from .api import load_synthetic
from .db import connect, require_known_data_mode, _runtime_path_guard
from .mock_demo import _record
from .notification_plan import prepare_notifications
from .notification_queue import enqueue_prepared_notifications
from .packet_cli import generate
from .review_runner import run_reviewed_mock
from .runner import RunError, Valuation, digest, encoded, initialize_mock, write_json


AS_OF = date(2026, 8, 28)
DAY = date(2026, 8, 31)
NOW = datetime(2026, 8, 31, 7, 10, tzinfo=JST)
STARTED = NOW.replace(minute=9, second=59)


def run_synthetic_pipeline(home, *, seed=42):
    """Generate unmodified proposals from shared synthetic data and queue cards.

    All reviews and event/lot metadata are explicit synthetic fixtures. Gate
    rejection, partial approval and no candidates remain legitimate outcomes.
    """
    if type(seed) is not int or seed < 0:
        raise RunError('seedは0以上の整数で指定してください')
    try:
        home = _runtime_path_guard(home)
    except (TypeError, ValueError, OSError):
        raise RunError('homeを正しく指定してください') from None
    if home.exists():
        raise RunError('合成パイプラインには未作成の専用フォルダを指定してください')
    initialized_at = datetime(2026, 8, 28, 16, 30, tzinfo=JST)
    initialize_mock(home, 1_000_000, [], initialized_at)
    load_synthetic(home, seed=seed)
    with connect(home) as con:
        if require_known_data_mode(con) != 'synthetic':
            raise RunError('合成パイプラインの市場データ出所が一致しません')
        names = dict(con.execute('SELECT code,name FROM listed ORDER BY code').fetchall())
    events = {code: dict(next_earnings_date=None, margin_regulated=False) for code in names}
    lots = {code: 100 for code in names}
    events_path, lots_path = home/'synthetic_events.json', home/'synthetic_lots.json'
    write_json(events_path, events)
    write_json(lots_path, lots)
    metadata = dict(source='synthetic', seed=seed, events='synthetic fixture: no earnings or restrictions',
                    lots='synthetic fixture: 100 shares for every listed code',
                    events_hash=digest(events), lots_hash=digest(lots))
    write_json(home/'synthetic_metadata.json', metadata)
    with connect(home) as con:
        con.executemany('INSERT OR REPLACE INTO provenance VALUES (?,?)', [
            ('events_source', 'synthetic_fixture'), ('lots_source', 'synthetic_fixture'),
            ('events_hash', metadata['events_hash']), ('lots_hash', metadata['lots_hash'])])
    proposals = generate(home, 'margin_bucket_long', AS_OF, budget_per_name=250_000,
                         policy_version='review-v1', events_path=events_path, lots_path=lots_path)
    generated_hash = digest(proposals)
    write_json(home/'generated_proposals.json', proposals)
    responses = {}
    for proposal in proposals:
        answer = dict(proposal_id=proposal['proposal_id'], packet_hash=proposal['packet_hash'],
                      decision='APPROVE', risks=[], reason='完全合成の明示APPROVE応答・実LLM未使用',
                      confidence=None)
        responses[proposal['proposal_id']] = {
            judge: dict(record=_record(judge, answer), received_at=NOW)
            for judge in ('claude', 'codex')}
    run_id = 'synthetic-pipeline'
    result = run_reviewed_mock(home, run_id, DAY, proposals, responses, NOW,
                              Valuation(1_000_000, 1_000_000), started_at=STARTED,
                              market_context={})
    if digest(proposals) != generated_hash:
        raise RunError('生成した候補が変更されています')
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        available, reserved = ledger.available(), ledger.reserved()
    contexts = {p['proposal_id']: dict(name=names[p['code']],
                account_confirmed_at=initialized_at.isoformat(), available_after=available,
                new_sent_today=0, max_new_per_day=2) for p in proposals}
    settings = {'broker': {'link_template': 'https://www.rakuten-sec.co.jp/web/market/search/{code}'},
                'line': {'allowed_user_id': 'synthetic-pipeline-recipient', 'monthly_budget': 100}}
    plan = prepare_notifications(home, run_id, DAY, contexts=contexts, settings=settings,
                                 include_status=True)
    queue = enqueue_prepared_notifications(home, run_id, DAY, now=NOW, contexts=contexts,
                                           settings=settings, include_status=True)
    summary = dict(mode='mock', source='synthetic', seed=seed, run_id=run_id,
                   as_of=AS_OF.isoformat(), execution_day=DAY.isoformat(),
                   generator='packet_cli.generate', generated_count=len(proposals),
                   generated_hash=generated_hash, status=result['status'],
                   candidate_statuses=result['candidates'], reservation=reserved,
                   available=available, delivery='NOT_SENT', real_sent_by_this_call=False,
                   warning='共通合成市場・合成イベント・模擬審査。未送信。実注文に使用しないでください。',
                   metadata=metadata, result=result, plan=plan, queue=queue,
                   contexts=contexts, settings=settings, include_status=True)
    write_json(home/'synthetic_pipeline_summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='共通合成市場から実生成器を通す未送信デモ')
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args(argv)
    try:
        summary = run_synthetic_pipeline(args.home, seed=args.seed)
    except (RunError, ValueError) as exc:
        parser.exit(2, f'{exc}\n')
    print(encoded(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
