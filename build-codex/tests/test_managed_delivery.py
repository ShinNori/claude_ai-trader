"""Independent integration checks for managed STOP at delivery boundaries."""
import copy
import json
import socket
import subprocess
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from test_notification_queue import CONFIG, CONTEXTS
from test_runner import DAY, NOW, proposal
from aitrader.db import connect, init
from aitrader.managed_stop import (
    apply_managed_control,
    initialize_managed_mock,
    inspect_managed_stop,
    managed_stop_policy,
)
from aitrader.mock_delivery import deliver_prepared_mock, reconcile_prepared_mock
from aitrader.notification_plan import prepare_notifications
from aitrader.notification_queue import enqueue_prepared_notifications
from aitrader.runner import RunError, Valuation, mock_verdicts, run
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('managed delivery attempted external I/O')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def _managed_run(tmp_path, name='managed-delivery'):
    home = tmp_path/name
    initialize_managed_mock(home, 1_000_000, [], NOW - timedelta(days=1),
                            settings=copy.deepcopy(CONFIG))
    init(home)
    with connect(home) as con:
        for offset in range(-10, 31):
            day = DAY + timedelta(days=offset)
            con.execute('INSERT INTO calendar VALUES(?,?)', [day, day.weekday() < 5])
        con.execute('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)',
                    ['6857', DAY - timedelta(days=1), 1000.])
    proposals = [proposal()]
    result = run(home, 'run1', DAY, proposals, mock_verdicts(proposals, 'run1', NOW),
                 NOW, Valuation(1_000_000, 1_000_000))
    assert result['candidates'][0]['status'] == 'APPROVED'
    prepare_notifications(home, 'run1', DAY, contexts=copy.deepcopy(CONTEXTS),
                          settings=copy.deepcopy(CONFIG))
    return home


def _enqueue(home, now=NOW, settings=CONFIG):
    return enqueue_prepared_notifications(
        home, 'run1', DAY, now=now, contexts=copy.deepcopy(CONTEXTS),
        settings=copy.deepcopy(settings))


def _deliver(home, now, results=('success',), settings=CONFIG):
    return deliver_prepared_mock(
        home, 'run1', DAY, now=now, contexts=copy.deepcopy(CONTEXTS),
        settings=copy.deepcopy(settings), stub_results=list(results))


def _entry(home):
    receipt = json.loads((home/'runs'/str(DAY)/'run1'/'notification_queue.json').read_text(
        encoding='utf-8'))
    key = receipt['entries'][0]['key']
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        with closing(Notifier(ledger=ledger, state_path=home/'notification.sqlite',
                              settings=CONFIG)) as notifier:
            return notifier.get_entry(key)


def _clock(home):
    path = managed_stop_policy(home)['clock_path']
    return json.loads(path.read_text(encoding='utf-8'))['observed_at']


def test_managed_v2_enqueues_and_delivers_without_open_wal_false_unknown(tmp_path):
    home = _managed_run(tmp_path)
    queued = _enqueue(home)
    assert queued['sent_by_this_call'] is False
    assert _clock(home) == NOW.isoformat()
    delivered_at = NOW + timedelta(minutes=1)
    report = _deliver(home, delivered_at)
    assert report['attempted_by_this_call'] is True
    assert report['simulated_sent_by_this_call'] is True
    assert report['real_sent_by_this_call'] is False
    assert _entry(home)['state'] == 'SENT'
    assert _clock(home) == delivered_at.isoformat()
    assert inspect_managed_stop(home, now=delivered_at)['known'] is True


def test_persistent_stop_blocks_new_before_any_attempt(tmp_path):
    home = _managed_run(tmp_path)
    _enqueue(home)
    stop_at = NOW + timedelta(minutes=1)
    apply_managed_control(home, action='STOP', now=stop_at, settings=CONFIG)
    report = _deliver(home, stop_at + timedelta(minutes=1))
    assert report['attempted_by_this_call'] is False
    assert report['results'][0]['state'] == 'STOPPED'
    assert _entry(home)['attempts'] == []


def test_stop_file_created_after_selection_is_rechecked_inside_flush(tmp_path,
                                                                    monkeypatch):
    home = _managed_run(tmp_path)
    _enqueue(home)
    original = Notifier.flush
    injected = False

    def race(self, *args, **kwargs):
        nonlocal injected
        injected = True
        (home/'STOP').write_text('arrived immediately before flush', encoding='utf-8')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Notifier, 'flush', race)
    report = _deliver(home, NOW + timedelta(minutes=1))
    assert injected is True
    assert report['attempted_by_this_call'] is False
    assert report['results'][0]['state'] == 'STOPPED'
    assert _entry(home)['attempts'] == []


def test_stop_file_read_error_at_flush_fails_closed(tmp_path, monkeypatch):
    home = _managed_run(tmp_path)
    _enqueue(home)
    original_flush = Notifier.flush
    original_lstat = Path.lstat
    stop_file = home/'STOP'
    injected = False

    def flush_with_denied_file(self, *args, **kwargs):
        def denied(path):
            nonlocal injected
            if path == stop_file:
                injected = True
                raise PermissionError('STOP unreadable')
            return original_lstat(path)
        monkeypatch.setattr(Path, 'lstat', denied)
        return original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Notifier, 'flush', flush_with_denied_file)
    report = _deliver(home, NOW + timedelta(minutes=1))
    assert injected is True
    assert report['attempted_by_this_call'] is False
    assert report['results'][0]['state'] == 'STOPPED'


def test_closed_database_unknown_rejects_before_clocks_or_attempts(tmp_path):
    home = _managed_run(tmp_path)
    _enqueue(home)
    before_clock = _clock(home)
    state = home/'notification.sqlite'
    state.write_bytes(b'corrupt closed notification database')
    before = state.read_bytes()
    with pytest.raises(RunError, match='確認できません'):
        _deliver(home, NOW + timedelta(minutes=1))
    assert state.read_bytes() == before
    assert _clock(home) == before_clock
    assert not (home/'runs'/str(DAY)/'run1'/'mock_delivery.json').exists()


def test_wrong_settings_rejected_before_queue_clock_advance(tmp_path):
    home = _managed_run(tmp_path)
    before = _clock(home)
    state_before = (home/'notification.sqlite').read_bytes()
    changed = copy.deepcopy(CONFIG)
    changed['line']['allowed_user_id'] = 'changed-recipient'
    with pytest.raises((RunError, ValueError)):
        _enqueue(home, now=NOW, settings=changed)
    assert _clock(home) == before
    assert (home/'notification.sqlite').read_bytes() == state_before
    assert not (home/'runs'/str(DAY)/'run1'/'notification_queue.json').exists()


def test_managed_clock_is_shared_floor_for_control_and_delivery(tmp_path):
    home = _managed_run(tmp_path)
    _enqueue(home)
    with pytest.raises(RunError, match='clock|前'):
        apply_managed_control(home, action='STOP', now=NOW - timedelta(seconds=1),
                              settings=CONFIG)
    control_at = NOW + timedelta(minutes=1)
    apply_managed_control(home, action='STOP', now=control_at, settings=CONFIG)
    with pytest.raises(RunError, match='clock|前|確認できません'):
        _deliver(home, NOW)
    assert _entry(home)['attempts'] == []
    assert _clock(home) == control_at.isoformat()


def test_reconciliation_advances_managed_clock_without_sending(tmp_path):
    home = _managed_run(tmp_path)
    _enqueue(home)
    delivered_at = NOW + timedelta(minutes=1)
    _deliver(home, delivered_at)
    reconciled_at = NOW + timedelta(minutes=2)
    report = reconcile_prepared_mock(
        home, 'run1', DAY, now=reconciled_at, contexts=copy.deepcopy(CONTEXTS),
        settings=copy.deepcopy(CONFIG))
    assert report['real_sent_by_this_call'] is False
    assert report['simulated_sent_by_this_call'] is False
    assert _clock(home) == reconciled_at.isoformat()


def test_missing_notification_database_is_not_recreated_by_enqueue(tmp_path):
    home = _managed_run(tmp_path)
    state = home/'notification.sqlite'
    state.unlink()
    before_clock = _clock(home)
    with pytest.raises(RunError, match='確認できません'):
        _enqueue(home)
    assert not state.exists()
    assert _clock(home) == before_clock
    assert not (home/'runs'/str(DAY)/'run1'/'notification_queue.json').exists()


def test_run_clock_failure_after_managed_clock_advance_never_flushes(
        tmp_path, monkeypatch):
    from aitrader import mock_delivery

    home = _managed_run(tmp_path)
    _enqueue(home)
    delivery_at = NOW + timedelta(minutes=1)
    flushed = False

    def forbidden_flush(*args, **kwargs):
        nonlocal flushed
        flushed = True
        pytest.fail('flush reached after run clock failure')

    monkeypatch.setattr(Notifier, 'flush', forbidden_flush)
    monkeypatch.setattr(mock_delivery, '_record_notification_time',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('run clock failed')))
    with pytest.raises(OSError, match='run clock failed'):
        _deliver(home, delivery_at)
    assert flushed is False
    assert _clock(home) == delivery_at.isoformat()
    assert _entry(home)['attempts'] == []
    assert not (home/'runs'/str(DAY)/'run1'/'mock_delivery.json').exists()
