"""Independent integration checks for opt-in managed STOP in the mock runner."""
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
import socket
import subprocess

import pytest

from aitrader.db import connect, init
from aitrader.managed_stop import apply_managed_control, initialize_managed_mock
from aitrader.packet import Proposal, packet_hash
from aitrader.runner import (RunError, Valuation, initialize_mock, mock_verdicts,
                             run)
from aitrader_ops.ledger import Ledger
from aitrader_ops.models import JST, PositionIn
from aitrader_ops.notify import Notifier


DAY = date(2026, 9, 8)
AS_OF = date(2026, 9, 7)
NOW = datetime(2026, 9, 8, 7, 10, tzinfo=JST)
SETTINGS = {'line': {'allowed_user_id': 'managed-stop-fixture',
                     'monthly_budget': 10}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('managed STOP runner attempted external I/O or delivery')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(Notifier, 'flush', forbidden)


def proposal(proposal_id='buy-managed', *, side='BUY', code='7203'):
    value = Proposal(
        proposal_id=proposal_id, packet_hash='', code=code, side=side, qty=100,
        lot_size=100, limit_price=100.0, exec_condition='OPENING_LIMIT',
        account_type='CASH', strategy='managed_stop_boundary',
        strategy_version='v1', as_of=AS_OF, snapshot_id='managed-snapshot',
        policy_version='review-v1', expires_at=datetime(2026, 9, 8, 8, 59,
                                                        tzinfo=JST),
        reason='明示したmanaged STOP境界候補',
        events={'next_earnings_date': None, 'margin_regulated': False})
    return replace(value, packet_hash=packet_hash(value))


def market(home):
    init(home)
    with connect(home) as db:
        for offset in range(-3, 4):
            day = DAY+timedelta(days=offset)
            db.execute('INSERT INTO calendar VALUES(?,?)',
                       [day, day.weekday() < 5])
        db.executemany('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)', [
            ('7203', AS_OF, 100.0), ('6857', AS_OF, 100.0)])


@pytest.fixture
def managed_home(tmp_path):
    home = tmp_path/'managed'
    initialize_managed_mock(
        home, 1_000_000, [PositionIn('6857', 200, 100.0)],
        NOW-timedelta(days=1), settings=SETTINGS)
    market(home)
    return home


def execute(home, value=None, *, run_id='managed-run', now=NOW):
    value = value or proposal()
    return run(home, run_id, DAY, [value],
               mock_verdicts([value], run_id, NOW), now,
               Valuation(1_020_000, 1_020_000))


def reservation(home):
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        return ledger.reserved(), ledger.view().reserved_shares, ledger.seq()


def test_clear_managed_state_allows_buy_reservation(managed_home):
    result = execute(managed_home)

    candidate = result['candidates'][0]
    assert candidate['status'] == 'APPROVED'
    assert len(candidate['stop_observations']) == 2
    assert all(item['status'] == 'CLEAR' and item['known'] is True
               for item in candidate['stop_observations'])
    assert reservation(managed_home)[0] == 10_020


def test_persistent_stop_blocks_buy_across_new_runs_without_reservation(managed_home):
    apply_managed_control(managed_home, action='STOP', now=NOW,
                          settings=SETTINGS, event_id='stop-1')

    first = execute(managed_home, run_id='stopped-1')
    before = reservation(managed_home)
    second = execute(managed_home, proposal('buy-managed-2'), run_id='stopped-2')

    for result in (first, second):
        candidate = result['candidates'][0]
        assert candidate['status'] == 'REJECTED'
        assert 'STOP_NEW' in candidate['gate']['reason_codes']
        assert candidate['stop_observations'][0]['status'] == 'STOPPED'
    assert reservation(managed_home) == before
    assert before[0] == 0


@pytest.mark.parametrize('missing', ['notification.sqlite', 'managed-stop-clock.json'])
def test_missing_managed_state_is_unknown_and_never_recreated(managed_home, missing):
    target = managed_home/missing
    target.unlink()

    result = execute(managed_home)

    candidate = result['candidates'][0]
    assert candidate['status'] == 'REJECTED'
    assert {'STOP_NEW', 'STOP_STATE_UNKNOWN'}.issubset(
        candidate['gate']['reason_codes'])
    assert candidate['stop_observations'][0]['known'] is False
    assert candidate['stop_observations'][0]['effective_stop'] is True
    assert reservation(managed_home)[0] == 0
    assert not target.exists()


def test_resume_after_stop_allows_a_later_buy(managed_home):
    stop_at = NOW-timedelta(minutes=2)
    apply_managed_control(managed_home, action='STOP', now=stop_at,
                          settings=SETTINGS, event_id='stop-1')
    apply_managed_control(managed_home, action='RESUME', now=NOW,
                          reconciled_at=NOW, settings=SETTINGS,
                          event_id='resume-1')

    result = execute(managed_home)

    assert result['candidates'][0]['status'] == 'APPROVED'
    assert all(item['status'] == 'CLEAR'
               for item in result['candidates'][0]['stop_observations'])
    assert reservation(managed_home)[0] == 10_020


def test_stop_file_is_or_composed_and_blocks_buy(managed_home):
    (managed_home/'STOP').write_text('manual stop', encoding='utf-8')

    result = execute(managed_home)

    candidate = result['candidates'][0]
    assert candidate['status'] == 'REJECTED'
    assert 'STOP_NEW' in candidate['gate']['reason_codes']
    assert 'STOP_FILE' in candidate['stop_observations'][0]['reasons']
    assert reservation(managed_home)[0] == 0


def test_sell_is_not_blocked_by_stop_alone(managed_home):
    apply_managed_control(managed_home, action='STOP', now=NOW,
                          settings=SETTINGS, event_id='stop-sell')
    sell = proposal('sell-managed', side='SELL', code='6857')

    result = execute(managed_home, sell)

    candidate = result['candidates'][0]
    assert candidate['status'] == 'APPROVED'
    assert len(candidate['stop_observations']) == 1
    assert candidate['stop_observations'][0]['status'] == 'STOPPED'
    assert 'STOP_NEW' not in candidate['gate']['reason_codes']
    assert reservation(managed_home)[:2] == (0, {'6857': 100})


def test_second_observation_can_stop_buy_before_intent(managed_home, monkeypatch):
    from aitrader import managed_stop

    calls = []

    def changing_status(home, *, now):
        calls.append(now)
        stopped = len(calls) == 2
        return {'status': 'STOPPED' if stopped else 'CLEAR', 'known': True,
                'effective_stop': stopped, 'persistent_stopped': stopped,
                'reasons': ['PERSISTENT_STOP'] if stopped else [],
                'observed_at': now.isoformat(), 'control_event_at': None,
                'read_only': True}

    monkeypatch.setattr(managed_stop, 'inspect_managed_stop', changing_status)
    result = execute(managed_home)

    candidate = result['candidates'][0]
    assert len(calls) == 2 and len(candidate['stop_observations']) == 2
    assert candidate['status'] == 'REJECTED'
    assert 'STOP_NEW' in candidate['gate']['reason_codes']
    assert reservation(managed_home)[0] == 0


def test_completed_run_keeps_saved_result_after_stop(managed_home):
    first = execute(managed_home)
    before = reservation(managed_home)
    apply_managed_control(managed_home, action='STOP', now=NOW+timedelta(minutes=1),
                          settings=SETTINGS, event_id='stop-after-complete')

    repeated = execute(managed_home, now=NOW+timedelta(minutes=2))

    assert repeated == first
    assert reservation(managed_home) == before


def test_initialization_flag_rejects_runner_before_reservation(managed_home):
    (managed_home/'.managed-stop-initializing').write_text('incomplete', encoding='utf-8')

    with pytest.raises(RunError, match='初期化'):
        execute(managed_home)
    assert reservation(managed_home)[0] == 0


def test_legacy_v1_home_keeps_existing_stop_file_contract(tmp_path):
    home = tmp_path/'legacy'
    initialize_mock(home, 1_000_000, [], NOW-timedelta(days=1))
    market(home)

    clear = execute(home, run_id='legacy-clear')
    assert clear['candidates'][0]['status'] == 'APPROVED'
    assert 'stop_observations' not in clear['candidates'][0]

    other = tmp_path/'legacy-stopped'
    initialize_mock(other, 1_000_000, [], NOW-timedelta(days=1))
    market(other)
    (other/'STOP').touch()
    stopped = execute(other, run_id='legacy-stop')
    assert stopped['candidates'][0]['status'] == 'REJECTED'
    assert 'STOP_NEW' in stopped['candidates'][0]['gate']['reason_codes']
