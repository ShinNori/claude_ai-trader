"""Managed-v2 receipt and notification-plan integration boundaries."""
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
import json

import pytest

from aitrader.daily_receipt import inspect_daily_rehearsal, seal_daily_rehearsal
from aitrader.daily_rehearsal import run_daily_rehearsal
from aitrader.db import connect, init
from aitrader.managed_stop import initialize_managed_mock
from aitrader.notification_plan import prepare_notifications
from aitrader.packet import Proposal, packet_hash
from aitrader.runner import RunError, Valuation, mock_verdicts, run
from aitrader_ops.ledger import Ledger
from aitrader_ops.models import JST


DAY = date(2026, 9, 8)
AS_OF = date(2026, 9, 7)
NOW = datetime(2026, 9, 8, 7, 10, tzinfo=JST)
SETTINGS = {
    'broker': {'link_template': 'https://www.rakuten-sec.co.jp/web/market/search/{code}'},
    'line': {'allowed_user_id': 'managed-plan', 'monthly_budget': 10},
}


def proposal():
    value = Proposal(
        proposal_id='managed-buy', packet_hash='', code='7203', side='BUY',
        qty=100, lot_size=100, limit_price=100.0,
        exec_condition='OPENING_LIMIT', account_type='CASH',
        strategy='managed_plan_fixture', strategy_version='v1', as_of=AS_OF,
        snapshot_id='managed-plan-snapshot', policy_version='review-v1',
        expires_at=datetime(2026, 9, 8, 8, 59, tzinfo=JST),
        reason='managed plan boundary',
        events={'next_earnings_date': None, 'margin_regulated': False})
    return replace(value, packet_hash=packet_hash(value))


def managed_run(tmp_path):
    home = tmp_path/'managed-run'
    initialize_managed_mock(home, 1_000_000, [], NOW-timedelta(days=1),
                            settings=SETTINGS)
    init(home)
    with connect(home) as db:
        for offset in range(-3, 4):
            day = DAY+timedelta(days=offset)
            db.execute('INSERT INTO calendar VALUES(?,?)',
                       [day, day.weekday() < 5])
        db.execute('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)',
                   ['7203', AS_OF, 100.0])
    value = proposal()
    result = run(home, 'managed-plan-run', DAY, [value],
                 mock_verdicts([value], 'managed-plan-run', NOW), NOW,
                 Valuation(1_000_000, 1_000_000))
    assert result['candidates'][0]['status'] == 'APPROVED'
    contexts = {'managed-buy': {
        'name': '管理銘柄', 'account_confirmed_at': NOW.isoformat(),
        'available_after': 989_980, 'new_sent_today': 0,
        'max_new_per_day': 2}}
    return home, contexts


def test_real_managed_daily_receipt_is_verified_history_only(tmp_path):
    home = tmp_path/'managed-daily'
    summary = run_daily_rehearsal(home, scenario='normal', seed=42,
                                  managed_stop=True)

    result = inspect_daily_rehearsal(home)

    assert summary['managed_stop'] is True
    assert summary['stop_policy'] == 'managed-v1'
    assert result['status'] == 'VERIFIED_HISTORY'
    assert result['saved_summary'] == summary
    assert result['current_signal'] is False
    assert result['auto_resume'] is False and result['repaired'] is False


def test_seal_rejects_managed_clock_before_initialized_at(tmp_path):
    home = tmp_path/'managed-seal'
    initialized = NOW-timedelta(days=1)
    initialize_managed_mock(home, 1_000_000, [], initialized, settings=SETTINGS)
    (home/'managed-stop-clock.json').write_text(json.dumps({
        'mode': 'mock', 'version': 1,
        'observed_at': (initialized-timedelta(seconds=1)).isoformat()}),
        encoding='utf-8')
    (home/'daily_progress.json').write_text(json.dumps({
        'status': 'RUNNING', 'current_stage': 'finalize', 'checkpoint_seq': 3}),
        encoding='utf-8')
    (home/'daily_rehearsal.json').write_text(json.dumps({
        'mode': 'mock', 'source': 'synthetic', 'virtual_clock': True,
        'real_sent': False, 'delivery': 'NOT_SENT', 'status': 'NO_SIGNAL',
        'managed_stop': True, 'stop_policy': 'managed-v1'}), encoding='utf-8')

    with pytest.raises(RunError):
        seal_daily_rehearsal(home, expected_progress_seq=4)
    assert not (home/'daily_receipt.json').exists()


def test_managed_notification_plan_requires_original_settings(tmp_path):
    home, contexts = managed_run(tmp_path)

    plan = prepare_notifications(home, 'managed-plan-run', DAY,
                                 contexts=contexts, settings=SETTINGS)

    assert plan['delivery'] == 'NOT_SENT'
    changed = {'line': {'allowed_user_id': 'changed', 'monthly_budget': 10}}
    with pytest.raises(RunError, match='settings'):
        prepare_notifications(home, 'managed-plan-run', DAY,
                              contexts=contexts, settings=changed)


def test_plan_rejects_valid_but_changed_policy_after_runner_completed(tmp_path):
    home, contexts = managed_run(tmp_path)
    marker_path = home/'mock-runner.json'
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    marker['initialized_at'] = (NOW-timedelta(days=2)).isoformat()
    marker_path.write_text(json.dumps(marker), encoding='utf-8')

    with pytest.raises(RunError, match='実行時と通知時'):
        prepare_notifications(home, 'managed-plan-run', DAY,
                              contexts=contexts, settings=SETTINGS)
    assert not (home/'notification-plans.sqlite').exists()


def test_managed_initialization_flag_blocks_seal_and_inspect(tmp_path):
    home = tmp_path/'managed-flag'
    initialize_managed_mock(home, 1_000_000, [], NOW-timedelta(days=1),
                            settings=SETTINGS)
    (home/'.managed-stop-initializing').write_text('incomplete', encoding='utf-8')
    (home/'daily_progress.json').write_text(json.dumps({
        'status': 'RUNNING', 'current_stage': 'finalize', 'checkpoint_seq': 3}),
        encoding='utf-8')
    (home/'daily_rehearsal.json').write_text(json.dumps({
        'mode': 'mock', 'source': 'synthetic', 'virtual_clock': True,
        'real_sent': False, 'delivery': 'NOT_SENT', 'status': 'NO_SIGNAL',
        'managed_stop': True, 'stop_policy': 'managed-v1'}), encoding='utf-8')

    with pytest.raises(RunError):
        seal_daily_rehearsal(home, expected_progress_seq=4)
    inspected = inspect_daily_rehearsal(home)
    assert inspected['status'] == 'NEEDS_RECONCILIATION'
    assert 'saved_summary' not in inspected
