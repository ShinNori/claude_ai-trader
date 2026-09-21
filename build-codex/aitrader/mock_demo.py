"""Synthetic demo with optional explicit stub delivery and no network."""
from __future__ import annotations

import argparse
import json
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from aitrader_ops.ledger import Ledger
from aitrader_ops.models import JST, Proposal, compute_packet_hash
from .db import init, connect
from .notification_plan import prepare_notifications
from .notification_queue import enqueue_prepared_notifications
from .mock_delivery import deliver_prepared_mock
from .review_runner import run_reviewed_mock
from .runner import RunError, Valuation, encoded, initialize_mock, write_json, _runner_home_guard


SCENARIOS = ('approve', 'reject', 'missing', 'timeout')
DAY = date(2026, 9, 8)
AS_OF = date(2026, 9, 7)
NOW = datetime(2026, 9, 8, 7, 10, tzinfo=JST)
STARTED = NOW.replace(minute=9, second=59)


def _record(judge, answer, timeout=False):
    raw = json.dumps(answer, ensure_ascii=False)
    return dict(stdout=json.dumps(dict(type='result', subtype='success',
                    is_error=False, result=raw)) if judge == 'claude' else '{"type":"turn.completed"}\n',
                final_message=raw if judge == 'codex' else None,
                exit_code=0, elapsed_seconds=121 if timeout else 1)


def run_demo(home, scenario='approve', *, simulate_delivery=False):
    """Create a fresh mock home; opt in explicitly to simulated stub delivery.

    result, plan and queue preserve their original preparation-time observations.
    When requested, mock_delivery separately records the subsequent simulation.
    """
    if not isinstance(simulate_delivery, bool):
        raise RunError('simulate_deliveryはboolで指定してください')
    if scenario not in SCENARIOS:
        raise RunError('scenarioはapprove/reject/missing/timeoutで指定してください')
    home = _runner_home_guard(home)
    if home.exists():
        raise RunError('デモには未作成の専用フォルダを指定してください')
    initialize_mock(home, 1_000_000, [], datetime(2026, 9, 7, 16, 30, tzinfo=JST))
    init(home)
    with connect(home) as con:
        con.executemany('INSERT INTO calendar VALUES (?, ?)', [(AS_OF, True), (DAY, True)])
        con.execute('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)', ['DEMO', AS_OF, 1000.])
    p = Proposal(proposal_id='demo-buy', packet_hash='', code='DEMO', side='BUY', qty=100,
                 lot_size=100, limit_price=1000., exec_condition='OPENING_LIMIT',
                 account_type='CASH', strategy='margin_bucket_long', strategy_version='v1',
                 as_of=AS_OF, snapshot_id='synthetic-demo', policy_version='p1',
                 expires_at=NOW.replace(hour=8, minute=59), reason='完全合成デモ・実注文禁止',
                 events={'next_earnings_date': None, 'margin_regulated': False})
    p = replace(p, packet_hash=compute_packet_hash(p))
    responses = {p.proposal_id: {}}
    for judge in ('claude', 'codex'):
        if scenario == 'missing' and judge == 'claude':
            continue
        answer = dict(proposal_id=p.proposal_id, packet_hash=p.packet_hash,
                      decision='REJECT' if scenario == 'reject' and judge == 'codex' else 'APPROVE',
                      risks=[], reason='合成済み応答・実LLM未使用', confidence=None)
        responses[p.proposal_id][judge] = dict(
            record=_record(judge, answer, scenario == 'timeout' and judge == 'codex'),
            received_at=NOW)
    run_id = 'demo-' + scenario
    result = run_reviewed_mock(home, run_id, DAY, [p], responses, NOW,
                              Valuation(1_000_000, 1_000_000), started_at=STARTED,
                              market_context={})
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        available, reserved = ledger.available(), ledger.reserved()
    contexts = {p.proposal_id: dict(name='完全合成デモ銘柄', account_confirmed_at=NOW.isoformat(),
                 available_after=available, new_sent_today=0, max_new_per_day=2)}
    settings = {'broker': {'link_template': 'https://www.rakuten-sec.co.jp/web/market/search/{code}'},
                'line': {'allowed_user_id': 'synthetic-demo-recipient', 'monthly_budget': 10}}
    plan = prepare_notifications(home, run_id, DAY, contexts=contexts, settings=settings,
                                 include_status=True)
    queue = enqueue_prepared_notifications(home, run_id, DAY, now=NOW, contexts=contexts,
                                           settings=settings, include_status=True)
    summary = dict(mode='mock', scenario=scenario, run_id=run_id,
                   execution_day=DAY.isoformat(), delivery='NOT_SENT',
                   warning='完全合成・未送信。実際の注文には使用しないでください。',
                   result=result, plan=plan, queue=queue, reserved=reserved,
                   contexts=contexts, settings=settings, include_status=True)
    if simulate_delivery:
        delivery = deliver_prepared_mock(home, run_id, DAY, now=NOW, contexts=contexts,
                                         settings=settings, stub_results=['success'],
                                         include_status=True)
        summary.update(mock_delivery=delivery, delivery='SIMULATION_ONLY',
                       real_sent_by_this_call=False,
                       warning='完全合成・模擬配信済み・実送信なし。実際の注文には使用しないでください。',
                       preparation_history_fields=['result', 'plan', 'queue'])
    write_json(home/'demo_summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='完全合成デモ（既定は通知キュー登録まで・実送信なし）')
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--scenario', choices=SCENARIOS, default='approve')
    parser.add_argument('--simulate-delivery', action='store_true',
                        help='明示的にstubで模擬配信する（実送信なし）')
    args = parser.parse_args(argv)
    try:
        summary = run_demo(args.home, args.scenario, simulate_delivery=args.simulate_delivery)
    except (RunError, ValueError) as exc:
        parser.exit(2, f'{exc}\n')
    print(encoded(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
