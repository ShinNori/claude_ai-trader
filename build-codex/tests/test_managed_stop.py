"""Independent public-API checks for the opt-in managed STOP policy."""
import hashlib
import json
import socket
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aitrader.managed_stop import (
    apply_managed_control,
    initialize_managed_mock,
    inspect_managed_stop,
    managed_stop_policy,
)
from aitrader.runner import RunError, initialize_mock
from aitrader_ops.models import JST
from aitrader_ops.notify import Notifier


AT = datetime(2026, 9, 11, 6, 0, tzinfo=JST)
SETTINGS = {'line': {'allowed_user_id': 'synthetic-managed-stop', 'monthly_budget': 10}}


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('managed STOP attempted external I/O or delivery')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(Notifier, 'flush', forbidden)


def _initialize(tmp_path, name='managed', settings=SETTINGS):
    home = tmp_path/name
    result = initialize_managed_mock(home, 1_000_000, [], AT, settings=settings)
    return home, result


def _hashes(home):
    return {p.relative_to(home).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in home.rglob('*') if p.is_file()}


def _assert_unknown(result, reason):
    assert result['status'] == 'UNKNOWN'
    assert result['known'] is False
    assert result['effective_stop'] is True
    assert result['persistent_stopped'] is None
    assert result['reasons'] == [reason]
    assert result['read_only'] is True


def test_initialize_has_fixed_policy_and_clear_readonly_state(tmp_path):
    home, initialized = _initialize(tmp_path)
    policy = managed_stop_policy(home)
    assert initialized['mode'] == 'mock'
    assert initialized['version'] == 2
    assert initialized['stop_policy'] == 'managed-v1'
    assert policy['stop_policy'] == 'managed-v1'
    assert policy['state_path'] == home/'notification.sqlite'
    assert policy['stop_files'][0] == home/'STOP'
    assert not any('initializ' in p.name for p in home.iterdir())
    before = _hashes(home)
    first = inspect_managed_stop(home, now=AT)
    second = inspect_managed_stop(home, now=AT)
    assert first == second
    assert first['status'] == 'CLEAR'
    assert first['known'] is True
    assert first['effective_stop'] is False
    assert first['persistent_stopped'] is False
    assert _hashes(home) == before


def test_stop_requires_reconciliation_before_resume(tmp_path):
    home, _ = _initialize(tmp_path)
    stopped_at = AT + timedelta(minutes=1)
    stopped = apply_managed_control(home, action='STOP', now=stopped_at,
                                    settings=SETTINGS, event_id='stop')
    assert stopped['result'] == 'STOPPED'
    assert stopped['real_sent'] is False
    assert stopped['stop_status']['status'] == 'STOPPED'
    denied = apply_managed_control(home, action='RESUME',
                                   now=stopped_at + timedelta(minutes=1),
                                   settings=SETTINGS, event_id='no-reconcile')
    assert denied['result'] == 'RECONCILIATION_REQUIRED'
    assert denied['stop_status']['status'] == 'STOPPED'
    assert inspect_managed_stop(home, now=stopped_at + timedelta(minutes=1))[
        'persistent_stopped'] is True


def test_reconciled_resume_keeps_stop_file_effective(tmp_path):
    home, _ = _initialize(tmp_path)
    stopped_at = AT + timedelta(minutes=1)
    resumed_at = stopped_at + timedelta(minutes=2)
    apply_managed_control(home, action='STOP', now=stopped_at, settings=SETTINGS)
    stop_file = home/'STOP'
    stop_file.write_text('operator stop remains authoritative', encoding='utf-8')
    resumed = apply_managed_control(
        home, action='RESUME', now=resumed_at, settings=SETTINGS,
        reconciled_at=stopped_at + timedelta(minutes=1))
    assert resumed['result'] == 'RESUMED'
    assert stop_file.exists()
    status = resumed['stop_status']
    assert status['status'] == 'STOPPED'
    assert status['persistent_stopped'] is False
    assert status['effective_stop'] is True
    assert status['reasons'] == ['STOP_FILE']


def test_late_control_does_not_roll_back_resume(tmp_path):
    home, _ = _initialize(tmp_path)
    stop_at = AT + timedelta(minutes=1)
    resume_at = AT + timedelta(minutes=3)
    apply_managed_control(home, action='STOP', now=stop_at, settings=SETTINGS,
                          event_at=stop_at)
    apply_managed_control(home, action='RESUME', now=resume_at, settings=SETTINGS,
                          reconciled_at=AT + timedelta(minutes=2), event_at=resume_at)
    late = apply_managed_control(home, action='STOP', now=AT + timedelta(minutes=4),
                                 settings=SETTINGS,
                                 event_at=AT + timedelta(minutes=2))
    assert late['result'] == 'IGNORED'
    assert late['stop_status']['status'] == 'CLEAR'
    assert late['stop_status']['persistent_stopped'] is False


def test_future_and_reversed_times_do_not_advance_clock(tmp_path):
    home, _ = _initialize(tmp_path)
    clock = managed_stop_policy(home)['clock_path']
    before = clock.read_bytes()
    with pytest.raises(RunError, match='未来'):
        apply_managed_control(home, action='STOP', now=AT + timedelta(minutes=1),
                              event_at=AT + timedelta(minutes=2), settings=SETTINGS)
    assert clock.read_bytes() == before
    with pytest.raises(RunError, match='clock|前'):
        apply_managed_control(home, action='STOP', now=AT - timedelta(seconds=1),
                              settings=SETTINGS)
    assert clock.read_bytes() == before


@pytest.mark.parametrize('missing,reason', [
    ('managed-stop-clock.json', 'MANAGED_POLICY_INVALID'),
    ('notification.sqlite', 'STATE_DB_MISSING'),
])
def test_missing_managed_state_is_safe_and_not_recreated(tmp_path, missing, reason):
    home, _ = _initialize(tmp_path)
    target = home/missing
    target.unlink()
    result = inspect_managed_stop(home, now=AT)
    _assert_unknown(result, reason)
    assert not target.exists()


def test_past_observation_is_safe_unknown(tmp_path):
    home, _ = _initialize(tmp_path)
    result = inspect_managed_stop(home, now=AT - timedelta(seconds=1))
    _assert_unknown(result, 'MANAGED_CLOCK_ROLLBACK')


def test_unknown_database_blocks_control_without_recreating_it(tmp_path):
    home, _ = _initialize(tmp_path)
    state = home/'notification.sqlite'
    state.write_bytes(b'corrupt managed state')
    before = state.read_bytes()
    with pytest.raises(RunError, match='確認できません'):
        apply_managed_control(home, action='STOP', now=AT + timedelta(minutes=1),
                              settings=SETTINGS)
    assert state.read_bytes() == before


def test_settings_are_fixed_at_initialization(tmp_path):
    home, _ = _initialize(tmp_path)
    changed = {'line': {'allowed_user_id': 'different', 'monthly_budget': 10}}
    with pytest.raises(RunError, match='settings'):
        apply_managed_control(home, action='STOP', now=AT + timedelta(minutes=1),
                              settings=changed)
    assert inspect_managed_stop(home, now=AT)['status'] == 'CLEAR'


def test_existing_home_and_dropbox_target_are_untouched(tmp_path):
    home = tmp_path/'existing'
    home.mkdir()
    marker = home/'keep.txt'
    marker.write_text('preserve', encoding='utf-8')
    with pytest.raises(ValueError):
        initialize_managed_mock(home, 1_000_000, [], AT, settings=SETTINGS)
    assert list(home.iterdir()) == [marker]

    forbidden = Path(__file__).resolve().parents[2]/'__managed_stop_forbidden__'
    assert not forbidden.exists()
    with pytest.raises(ValueError, match='Dropbox'):
        initialize_managed_mock(forbidden, 1_000_000, [], AT, settings=SETTINGS)
    assert not forbidden.exists()


def test_failed_initialization_leaves_flag_and_cannot_be_reused(tmp_path, monkeypatch):
    from aitrader import runner

    home = tmp_path/'failed-init'
    monkeypatch.setattr(runner, 'initialize_mock',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('init failed')))
    with pytest.raises(OSError, match='init failed'):
        initialize_managed_mock(home, 1_000_000, [], AT, settings=SETTINGS)
    flags = [p for p in home.iterdir() if 'initializ' in p.name]
    assert len(flags) == 1
    with pytest.raises((RunError, ValueError)):
        managed_stop_policy(home)
    with pytest.raises(ValueError):
        initialize_managed_mock(home, 1_000_000, [], AT, settings=SETTINGS)


def test_failed_control_preserves_advanced_clock_and_releases_lock(tmp_path,
                                                                  monkeypatch):
    home, _ = _initialize(tmp_path)
    now = AT + timedelta(minutes=1)
    with monkeypatch.context() as patch:
        patch.setattr(Notifier, 'stop',
                      lambda *a, **k: (_ for _ in ()).throw(RuntimeError('stop failed')))
        with pytest.raises(RuntimeError, match='stop failed'):
            apply_managed_control(home, action='STOP', now=now, settings=SETTINGS)
    clock = json.loads(managed_stop_policy(home)['clock_path'].read_text(encoding='utf-8'))
    assert clock['observed_at'] == now.isoformat()
    recovered = apply_managed_control(home, action='STOP',
                                      now=now + timedelta(minutes=1), settings=SETTINGS)
    assert recovered['result'] == 'STOPPED'


def test_runner_lock_contention_blocks_then_release_allows_control(tmp_path):
    home, _ = _initialize(tmp_path)
    lock = sqlite3.connect(home/'runner-lock.sqlite', isolation_level=None)
    lock.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(sqlite3.OperationalError):
            apply_managed_control(home, action='STOP', now=AT + timedelta(minutes=1),
                                  settings=SETTINGS)
    finally:
        lock.rollback()
        lock.close()
    result = apply_managed_control(home, action='STOP',
                                   now=AT + timedelta(minutes=1), settings=SETTINGS)
    assert result['result'] == 'STOPPED'


def test_exact_legacy_marker_has_no_managed_fallback(tmp_path):
    home = tmp_path/'legacy'
    initialize_mock(home, 1_000_000, [], AT)
    assert managed_stop_policy(home) is None
    marker = home/'mock-runner.json'
    marker.write_text(json.dumps({'mode': 'mock', 'version': 2,
                                  'stop_policy': 'broken'}), encoding='utf-8')
    with pytest.raises(RunError):
        managed_stop_policy(home)


@pytest.mark.parametrize('version', [True, 1.0])
def test_legacy_version_requires_exact_integer_type(tmp_path, version):
    home = tmp_path/f'legacy-invalid-{type(version).__name__}'
    initialize_mock(home, 1_000_000, [], AT)
    (home/'mock-runner.json').write_text(
        json.dumps({'mode': 'mock', 'version': version}), encoding='utf-8')
    with pytest.raises(RunError, match='markerが不正'):
        managed_stop_policy(home)


def test_missing_ledger_blocks_control_without_recreating_it(tmp_path):
    home, _ = _initialize(tmp_path)
    ledger = home/'ledger.sqlite'
    ledger.unlink()
    with pytest.raises(RunError, match='台帳'):
        apply_managed_control(home, action='STOP', now=AT + timedelta(minutes=1),
                              settings=SETTINGS)
    assert not ledger.exists()
    assert inspect_managed_stop(home, now=AT)['status'] == 'CLEAR'


def test_clock_before_initialization_is_invalid_and_blocks_control(tmp_path):
    home, _ = _initialize(tmp_path)
    clock = managed_stop_policy(home)['clock_path']
    broken = {'mode': 'mock', 'version': 1,
              'observed_at': (AT - timedelta(seconds=1)).isoformat()}
    clock.write_text(json.dumps(broken), encoding='utf-8')
    _assert_unknown(inspect_managed_stop(home, now=AT), 'MANAGED_CLOCK_INVALID')
    before = clock.read_bytes()
    with pytest.raises(RunError, match='初期化時刻より前'):
        apply_managed_control(home, action='STOP', now=AT + timedelta(minutes=1),
                              settings=SETTINGS)
    assert clock.read_bytes() == before


def test_initializing_flag_permission_error_is_not_treated_as_absent(tmp_path,
                                                                    monkeypatch):
    home, _ = _initialize(tmp_path)
    flag = home/'.managed-stop-initializing'
    original = Path.lstat
    injected = False

    def deny(path):
        nonlocal injected
        if path == flag:
            injected = True
            raise PermissionError('flag denied')
        return original(path)

    monkeypatch.setattr(Path, 'lstat', deny)
    with pytest.raises(RunError, match='flag'):
        managed_stop_policy(home)
    assert injected is True


@pytest.mark.parametrize('bad_hash', ['g' * 64, 'A' * 64, '0' * 63])
def test_settings_hash_must_be_lowercase_hex64(tmp_path, bad_hash):
    home, _ = _initialize(tmp_path)
    marker = home/'mock-runner.json'
    value = json.loads(marker.read_text(encoding='utf-8'))
    value['settings_hash'] = bad_hash
    marker.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(RunError, match='markerが不正'):
        managed_stop_policy(home)


def test_policy_change_while_waiting_for_lock_is_rejected(tmp_path, monkeypatch):
    from aitrader import managed_stop

    home, _ = _initialize(tmp_path)
    lock_path = home/'runner-lock.sqlite'
    holder = sqlite3.connect(lock_path, isolation_level=None)
    holder.execute('BEGIN IMMEDIATE')
    real_connect = sqlite3.connect
    waiting = threading.Event()
    errors = []

    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args, **kwargs):
            if sql == 'BEGIN IMMEDIATE':
                waiting.set()
            return self.connection.execute(sql, *args, **kwargs)

        def close(self):
            self.connection.close()

    def connect(path, *args, **kwargs):
        connection = real_connect(path, *args, **kwargs)
        return ConnectionProxy(connection) if Path(path) == lock_path else connection

    monkeypatch.setattr(managed_stop.sqlite3, 'connect', connect)

    def worker():
        try:
            apply_managed_control(home, action='STOP',
                                  now=AT + timedelta(minutes=1), settings=SETTINGS)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert waiting.wait(timeout=2)
    marker = home/'mock-runner.json'
    value = json.loads(marker.read_text(encoding='utf-8'))
    value['settings_hash'] = '0' * 64
    marker.write_text(json.dumps(value), encoding='utf-8')
    holder.rollback()
    holder.close()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], RunError)
    assert 'ロック取得中に変化' in str(errors[0])
