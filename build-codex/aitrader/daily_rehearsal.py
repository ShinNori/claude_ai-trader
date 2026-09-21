"""Deterministic offline rehearsal of the 06:30--07:15 daily path."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from aitrader_ops.ledger import Ledger
from aitrader_ops.models import JST

from .api import load_synthetic
from .db import connect, require_known_data_mode, _runtime_path_guard
from .daily_receipt import seal_daily_rehearsal
from .managed_stop import apply_managed_control, initialize_managed_mock
from .mock_demo import _record
from .notification_plan import prepare_notifications
from .notification_queue import enqueue_prepared_notifications
from .packet_cli import generate
from .review_runner import run_reviewed_mock
from .runner import RunError, Valuation, digest, encoded, initialize_mock, write_json


SCENARIOS = (
    'normal', 'data_missing', 'holiday', 'judge_missing', 'deadline',
    'stop_before_review',
)
AS_OF = date(2026, 8, 28)
NORMAL_DAY = date(2026, 8, 31)
HOLIDAY_DAY = date(2026, 8, 30)


def _at(day, hour, minute, second=0):
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=JST)


def _stages(day):
    return {
        'acquire_validate': {'scheduled_at': _at(day, 6, 30).isoformat(), 'status': 'PENDING'},
        'generate': {'scheduled_at': _at(day, 6, 50).isoformat(), 'status': 'PENDING'},
        'review': {'scheduled_at': _at(day, 7, 0).isoformat(), 'status': 'PENDING'},
        'prepare': {'scheduled_at': _at(day, 7, 0).isoformat(), 'status': 'PENDING'},
        'enqueue': {'scheduled_at': _at(day, 7, 0).isoformat(), 'status': 'PENDING'},
        'deadline': {'scheduled_at': _at(day, 7, 15).isoformat(), 'status': 'REFERENCE'},
    }


def _skip_after(stages, stage):
    found = False
    for name in ('acquire_validate', 'generate', 'review', 'prepare', 'enqueue'):
        if found and stages[name]['status'] == 'PENDING':
            stages[name]['status'] = 'SKIPPED'
        if name == stage:
            found = True


def _daily_settings():
    return {
        'broker': {'link_template': 'https://www.rakuten-sec.co.jp/web/market/search/{code}'},
        'line': {'allowed_user_id': 'synthetic-pipeline-recipient', 'monthly_budget': 100},
    }


def run_daily_rehearsal(home, *, scenario='normal', seed=42, managed_stop=False):
    """Run one fresh, synthetic-only day and enqueue cards without sending."""
    if scenario not in SCENARIOS:
        raise RunError('scenarioはnormal/data_missing/holiday/judge_missing/deadline/stop_before_reviewで指定してください')
    if type(seed) is not int or seed < 0:
        raise RunError('seedは0以上の整数で指定してください')
    if type(managed_stop) is not bool:
        raise RunError('managed_stopはboolで指定してください')
    try:
        home = Path(home).absolute()
    except (TypeError, ValueError, OSError) as exc:
        raise RunError('homeを正しく指定してください') from exc
    if any('dropbox' in part.lower() for part in home.parts):
        raise RunError('実行先はDropbox外にしてください')
    try:
        home = _runtime_path_guard(home)
    except (TypeError, ValueError, OSError):
        raise RunError('homeを正しく指定してください') from None
    settings = _daily_settings()
    initialized_at = datetime(2026, 8, 28, 16, 30, tzinfo=JST)
    if managed_stop:
        initialize_managed_mock(home, 1_000_000, [], initialized_at, settings=settings)
    else:
        if home.exists():
            raise RunError('日次リハーサルには未作成の専用フォルダを指定してください')
        try:
            home.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RunError('日次リハーサルには未作成の専用フォルダを指定してください') from exc

    progress = dict(status='RUNNING', current_stage=None, checkpoint_seq=0,
                    mode='mock', source='synthetic', virtual_clock=True,
                    real_sent=False)

    def checkpoint(stage):
        progress.update(status='RUNNING', current_stage=stage)
        progress.pop('failed_stage', None)
        progress.pop('error_type', None)
        progress.pop('result_status', None)
        progress['checkpoint_seq'] += 1
        write_json(home/'daily_progress.json', progress)

    def completed(result_status):
        progress.update(status='COMPLETED', current_stage='finalize',
                        result_status=result_status)
        progress['checkpoint_seq'] += 1
        write_json(home/'daily_progress.json', progress)

    def finalize(summary):
        checkpoint('finalize')
        write_json(home/'daily_rehearsal.json', summary)
        seal_daily_rehearsal(home, expected_progress_seq=progress['checkpoint_seq'] + 1)
        completed(summary['status'])

    try:
        return _run_daily_rehearsal_acquired(
            home, scenario, seed, checkpoint, finalize, settings,
            managed_stop, initialized_at)
    except Exception as exc:
        try:
            progress.update(status='FAILED', failed_stage=progress['current_stage'],
                            error_type=type(exc).__name__,
                            partial_state_may_exist=True, auto_resume=False)
            progress.pop('result_status', None)
            progress['checkpoint_seq'] += 1
            write_json(home/'daily_progress.json', progress)
        except Exception:
            pass
        raise


def _run_daily_rehearsal_acquired(home, scenario, seed, checkpoint, finalize,
                                  settings, managed_stop, initialized_at):
    """Implementation after the caller has exclusively claimed a fresh home."""

    day = HOLIDAY_DAY if scenario == 'holiday' else NORMAL_DAY
    stages = _stages(day)
    summary = dict(mode='mock', source='synthetic', virtual_clock=True,
                   real_sent=False, real_sent_by_this_call=False,
                   delivery='NOT_SENT', scenario=scenario, seed=seed,
                   as_of=AS_OF.isoformat(), execution_day=day.isoformat(),
                   status='RUNNING', reservation=0, reserved=0, stages=stages)
    if managed_stop:
        summary.update(managed_stop=True, stop_policy='managed-v1')

    checkpoint('initialize')
    if not managed_stop:
        initialize_mock(home, 1_000_000, [], initialized_at)
    checkpoint('acquire_validate')
    load_synthetic(home, seed=seed)
    with connect(home) as con:
        if require_known_data_mode(con) != 'synthetic':
            raise RunError('日次リハーサルの市場データ出所が一致しません')

    fault_fixture = None
    if scenario == 'data_missing':
        # This home is the fault fixture.  The shared generator and its source
        # tables are never modified; only this freshly loaded DB is damaged.
        with connect(home) as con:
            con.execute('DELETE FROM prices_daily WHERE date=?', [AS_OF])
        fault_fixture = 'dedicated market DB: AS_OF prices removed'

    with connect(home) as con:
        session = con.execute('SELECT is_business_day FROM calendar WHERE date=?', [day]).fetchone()
        price_count = con.execute('SELECT count(*) FROM prices_daily WHERE date=?', [AS_OF]).fetchone()[0]
        names = dict(con.execute('SELECT code,name FROM listed ORDER BY code').fetchall())
    stages['acquire_validate']['observed_at'] = _at(day, 6, 30).isoformat()
    if session and session[0] is False:
        stages['acquire_validate'].update(status='NO_SESSION', session_confirmed=False)
        _skip_after(stages, 'acquire_validate')
        summary.update(status='NO_SESSION', result={
            'status': 'NO_SESSION', 'reason_codes': ['MARKET_HOLIDAY'],
            'mode': 'mock', 'delivery': 'NOT_SENT'})
        finalize(summary)
        return summary
    if not session or session[0] is not True or not price_count:
        reason = 'AS_OF_PRICE_MISSING' if session and session[0] is True and not price_count else 'MARKET_DATA_INCOMPLETE'
        stages['acquire_validate'].update(status='FAILED', reason=reason)
        _skip_after(stages, 'acquire_validate')
        summary.update(status='DATA_INCOMPLETE', result={
            'status': 'DATA_INCOMPLETE', 'reason_codes': [reason],
            'mode': 'mock', 'delivery': 'NOT_SENT'}, fault_fixture=fault_fixture)
        finalize(summary)
        return summary
    stages['acquire_validate'].update(status='COMPLETED', session_confirmed=True,
                                      as_of_price_rows=price_count)

    checkpoint('generate')
    events = {code: {'next_earnings_date': None, 'margin_regulated': False} for code in names}
    lots = {code: 100 for code in names}
    events_path, lots_path = home/'synthetic_events.json', home/'synthetic_lots.json'
    write_json(events_path, events)
    write_json(lots_path, lots)
    with connect(home) as con:
        con.executemany('INSERT OR REPLACE INTO provenance VALUES (?,?)', [
            ('events_source', 'synthetic_fixture'), ('lots_source', 'synthetic_fixture'),
            ('events_hash', digest(events)), ('lots_hash', digest(lots))])
    proposals = generate(home, 'margin_bucket_long', AS_OF, budget_per_name=250_000,
                         policy_version='review-v1', events_path=events_path,
                         lots_path=lots_path)
    raw_hash = digest(proposals)
    write_json(home/'generated_proposals.json', proposals)
    stages['generate'].update(status='COMPLETED', observed_at=_at(day, 6, 50).isoformat(),
                              proposal_count=len(proposals), raw_proposal_hash=raw_hash)

    checkpoint('review')
    if scenario == 'stop_before_review':
        if managed_stop:
            apply_managed_control(home, action='STOP', now=_at(day, 7, 0),
                                  settings=settings,
                                  event_at=_at(day, 7, 0),
                                  event_id='daily-rehearsal-stop')
        else:
            (home/'STOP').write_text('synthetic daily rehearsal STOP', encoding='utf-8')

    started = _at(day, 7, 0)
    completed_at = _at(day, 7, 15) if scenario == 'deadline' else _at(day, 7, 1)
    received = _at(day, 7, 14, 59) if scenario == 'deadline' else _at(day, 7, 0, 30)
    responses = {}
    for proposal in proposals:
        answer = dict(proposal_id=proposal['proposal_id'], packet_hash=proposal['packet_hash'],
                      decision='APPROVE', risks=[],
                      reason='完全合成の明示APPROVE応答・実LLM未使用', confidence=None)
        responses[proposal['proposal_id']] = {
            judge: {'record': _record(judge, answer), 'received_at': received}
            for judge in ('claude', 'codex')
            if not (scenario == 'judge_missing' and judge == 'claude')
        }
    run_id = 'daily-rehearsal-' + scenario
    result = run_reviewed_mock(home, run_id, day, proposals, responses, completed_at,
                               Valuation(1_000_000, 1_000_000), started_at=started,
                               market_context={})
    if digest(proposals) != raw_hash:
        raise RunError('生成したrawProposalが変更されています')
    stages['review'].update(status='COMPLETED', observed_at=completed_at.isoformat(),
                            result_status=result['status'], received_at=received.isoformat(),
                            recorded_stub_elapsed_seconds=1,
                            virtual_arrival_delay_seconds=int((received-started).total_seconds()),
                            explicit_deadline_simulation=scenario == 'deadline')

    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        available, reserved = ledger.available(), ledger.reserved()
    contexts = {p['proposal_id']: {
        'name': names[p['code']],
        'account_confirmed_at': datetime(2026, 8, 28, 16, 30, tzinfo=JST).isoformat(),
        'available_after': available, 'new_sent_today': 0, 'max_new_per_day': 2,
    } for p in proposals}
    checkpoint('prepare')
    plan = prepare_notifications(home, run_id, day, contexts=contexts, settings=settings,
                                 include_status=True)
    stages['prepare'].update(status='COMPLETED', observed_at=completed_at.isoformat(),
                             plan_count=len(plan['plans']))
    checkpoint('enqueue')
    queue = enqueue_prepared_notifications(home, run_id, day, now=completed_at,
                                           contexts=contexts, settings=settings,
                                           include_status=True)
    stages['enqueue'].update(status='COMPLETED', observed_at=completed_at.isoformat(),
                             entry_count=len(queue['entries']), sent=False)
    summary.update(status=result['status'], result=result, reservation=reserved,
                   reserved=reserved, available=available, generated_count=len(proposals),
                   raw_proposal_hash=raw_hash, plan=plan, queue=queue,
                   contexts=contexts, settings=settings, include_status=True)
    finalize(summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='完全合成の日次リハーサル（キュー登録まで・実送信なし）')
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--scenario', choices=SCENARIOS, default='normal')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--managed-stop', action='store_true',
                        help='新規homeを管理STOP policy付きmockとして初期化する')
    args = parser.parse_args(argv)
    try:
        summary = run_daily_rehearsal(args.home, scenario=args.scenario, seed=args.seed,
                                      managed_stop=args.managed_stop)
    except (RunError, ValueError) as exc:
        parser.exit(2, f'{exc}\n')
    print(encoded(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
