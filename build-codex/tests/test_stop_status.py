"""Independent read-only checks for the durable STOP status projection."""
import hashlib
import json
import shutil
import socket
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aitrader_ops.models import JST
from aitrader_ops.notify import Notifier
from aitrader_ops.stop_status import inspect_stop_status


NOW = datetime(2026, 9, 11, 12, 0, tzinfo=JST)
SETTINGS = {'line': {'allowed_user_id': 'synthetic-stop-status', 'monthly_budget': 10}}


@pytest.fixture(autouse=True)
def prohibit_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('STOP status inspection attempted external I/O or delivery')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(Notifier, 'flush', forbidden)


def _new_state(path):
    notifier = Notifier(ledger=object(), state_path=path, settings=SETTINGS)
    notifier.close()
    return path


def _hashes(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.iterdir() if p.is_file()}


def _assert_unknown(result):
    assert result['status'] == 'UNKNOWN'
    assert result['known'] is False
    assert result['effective_stop'] is True
    assert result['persistent_stopped'] is None
    assert result['read_only'] is True
    assert result['reasons']


def test_empty_notifier_state_is_clear_and_opened_readonly(tmp_path, monkeypatch):
    state = _new_state(tmp_path/'notification.sqlite')
    before = _hashes(tmp_path)
    original = sqlite3.connect
    calls = []

    def capture(database, *args, **kwargs):
        calls.append((database, kwargs.copy()))
        return original(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', capture)
    first = inspect_stop_status(state, now=NOW)
    second = inspect_stop_status(state, now=NOW)
    assert first == second
    assert first == {
        'status': 'CLEAR', 'known': True, 'effective_stop': False,
        'persistent_stopped': False, 'reasons': [],
        'observed_at': NOW.isoformat(), 'control_event_at': None,
        'read_only': True,
    }
    assert len(calls) == 2
    assert all('?mode=ro&immutable=1' in str(database) for database, _ in calls)
    assert all(options.get('uri') is True for _, options in calls)
    assert _hashes(tmp_path) == before


def test_stop_resume_and_late_control_follow_event_time(tmp_path):
    state = tmp_path/'notification.sqlite'
    notifier = Notifier(ledger=object(), state_path=state, settings=SETTINGS)
    stop_at = NOW - timedelta(hours=3)
    resume_at = NOW - timedelta(hours=2)
    assert notifier.stop(now=stop_at, event_at=stop_at, event_id='stop') == 'STOPPED'
    notifier.close()
    stopped = inspect_stop_status(state, now=NOW)
    assert stopped['status'] == 'STOPPED'
    assert stopped['known'] is True and stopped['effective_stop'] is True
    assert stopped['persistent_stopped'] is True
    assert stopped['reasons'] == ['PERSISTENT_STOP']
    assert stopped['control_event_at'] == stop_at.isoformat()
    resumed = Notifier(ledger=object(), state_path=state, settings=SETTINGS)
    assert resumed.resume(now=resume_at, reconciled_at=stop_at + timedelta(minutes=30),
                          event_at=resume_at, event_id='resume') == 'RESUMED'
    assert resumed.stop(now=NOW - timedelta(hours=1),
                        event_at=stop_at + timedelta(minutes=45),
                        event_id='late-stop') == 'IGNORED'
    resumed.close()
    clear = inspect_stop_status(state, now=NOW)
    assert clear['status'] == 'CLEAR'
    assert clear['persistent_stopped'] is False
    assert clear['effective_stop'] is False
    assert clear['control_event_at'] == resume_at.isoformat()


def test_initial_resume_state_is_clear(tmp_path):
    state = tmp_path/'notification.sqlite'
    notifier = Notifier(ledger=object(), state_path=state, settings=SETTINGS)
    at = NOW - timedelta(minutes=5)
    assert notifier.resume(now=at, reconciled_at=at, event_at=at) == 'RESUMED'
    notifier.close()
    result = inspect_stop_status(state, now=NOW)
    assert result['status'] == 'CLEAR'
    assert result['persistent_stopped'] is False
    assert result['control_event_at'] == at.isoformat()


def test_stop_file_is_or_combined_with_clear_persistent_state(tmp_path):
    state = _new_state(tmp_path/'notification.sqlite')
    stop_file = tmp_path/'STOP'
    stop_file.write_text('manual stop', encoding='utf-8')
    result = inspect_stop_status(state, now=NOW, stop_files=[stop_file])
    assert result['status'] == 'STOPPED'
    assert result['known'] is True
    assert result['persistent_stopped'] is False
    assert result['effective_stop'] is True
    assert result['reasons'] == ['STOP_FILE']


def test_future_control_is_unknown_instead_of_assumed_clear(tmp_path):
    state = tmp_path/'notification.sqlite'
    notifier = Notifier(ledger=object(), state_path=state, settings=SETTINGS)
    future = NOW + timedelta(minutes=1)
    notifier.stop(now=future, event_at=future)
    notifier.close()
    result = inspect_stop_status(state, now=NOW)
    _assert_unknown(result)
    assert result['reasons'] == ['STATE_DB_INVALID']


@pytest.mark.parametrize('variant', [
    'missing', 'corrupt', 'sidecar', 'unsafe-db', 'invalid-type', 'contradiction',
])
def test_missing_corrupt_or_contradictory_state_is_unknown(tmp_path, monkeypatch,
                                                          variant):
    from aitrader_ops import stop_status

    state = tmp_path/'notification.sqlite'
    if variant != 'missing':
        _new_state(state)
    if variant == 'corrupt':
        state.write_bytes(b'not sqlite')
    elif variant == 'sidecar':
        Path(str(state) + '-wal').write_bytes(b'pending')
    elif variant == 'unsafe-db':
        monkeypatch.setattr(stop_status, '_is_link', lambda path: path == state)
    elif variant in ('invalid-type', 'contradiction'):
        notifier = Notifier(ledger=object(), state_path=state, settings=SETTINGS)
        at = NOW - timedelta(minutes=2)
        notifier.stop(now=at, event_at=at)
        notifier.close()
        with sqlite3.connect(state) as db:
            value = json.dumps(1 if variant == 'invalid-type' else False)
            db.execute("UPDATE kv SET value=? WHERE name='stopped'", [value])
    before = _hashes(tmp_path)
    result = inspect_stop_status(state, now=NOW)
    _assert_unknown(result)
    assert _hashes(tmp_path) == before


def test_database_read_failure_is_unknown_and_preserves_files(tmp_path, monkeypatch):
    from aitrader_ops import stop_status

    state = _new_state(tmp_path/'notification.sqlite')
    before = _hashes(tmp_path)
    monkeypatch.setattr(stop_status, '_read_persistent',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('denied')))
    result = inspect_stop_status(state, now=NOW)
    _assert_unknown(result)
    assert result['reasons'] == ['STATE_DB_INVALID']
    assert _hashes(tmp_path) == before


def test_stop_file_read_failure_is_unknown(tmp_path, monkeypatch):
    state = _new_state(tmp_path/'notification.sqlite')
    stop_file = tmp_path/'STOP'
    original_lstat = Path.lstat
    injected = False

    def fail_stop(path):
        nonlocal injected
        if path == stop_file:
            injected = True
            raise OSError('denied')
        return original_lstat(path)

    monkeypatch.setattr(Path, 'lstat', fail_stop)
    result = inspect_stop_status(state, now=NOW, stop_files=[stop_file])
    assert injected is True
    _assert_unknown(result)
    assert result['reasons'] == ['STOP_FILE_READ_FAILED']


@pytest.mark.parametrize('stop_files', ['STOP', [object()], None])
def test_invalid_stop_file_argument_types_are_rejected(tmp_path, stop_files):
    state = _new_state(tmp_path/'notification.sqlite')
    with pytest.raises((TypeError, ValueError)):
        inspect_stop_status(state, now=NOW, stop_files=stop_files)


@pytest.mark.parametrize('now', [datetime(2026, 9, 11, 12), None, 1])
def test_now_requires_an_aware_datetime(tmp_path, now):
    state = _new_state(tmp_path/'notification.sqlite')
    with pytest.raises((TypeError, ValueError)):
        inspect_stop_status(state, now=now)


def test_missing_parent_is_not_created(tmp_path):
    parent = tmp_path/'missing-parent'
    result = inspect_stop_status(parent/'notification.sqlite', now=NOW)
    _assert_unknown(result)
    assert result['reasons'] == ['STATE_DB_MISSING']
    assert not parent.exists()
