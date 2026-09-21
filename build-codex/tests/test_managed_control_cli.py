"""Independent CLI checks for explicit managed mock STOP controls."""
import json
import socket
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aitrader.managed_stop import initialize_managed_mock, inspect_managed_stop
from aitrader_ops.models import JST
from aitrader_ops.notify import Notifier


AT = datetime(2026, 9, 11, 6, 0, tzinfo=JST)
SETTINGS = {'line': {'allowed_user_id': 'managed-cli-fixture', 'monthly_budget': 10}}
FIXED_ERROR = '管理STOP操作の入力または固定状態を確認できません。'


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('managed control CLI attempted external I/O or delivery')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(Notifier, 'flush', forbidden)


def _home(tmp_path, name='managed-cli'):
    home = tmp_path/name
    initialize_managed_mock(home, 1_000_000, [], AT, settings=SETTINGS)
    settings = tmp_path/f'{name}-settings.json'
    settings.write_text(json.dumps(SETTINGS), encoding='utf-8')
    return home, settings


def _args(home, settings, action, now, **optional):
    args = ['--home', str(home), '--action', action, '--now', now.isoformat(),
            '--settings', str(settings)]
    names = {'reconciled_at': '--reconciled-at', 'event_at': '--event-at',
             'event_id': '--event-id'}
    for key, value in optional.items():
        if value is not None:
            args.extend([names[key], value.isoformat() if isinstance(value, datetime) else value])
    return args


def _main(args, capsys):
    from aitrader.managed_control import main
    code = main(args)
    output = capsys.readouterr()
    return code, json.loads(output.out), output.err


def _error(args, capsys):
    from aitrader.managed_control import main
    with pytest.raises(SystemExit) as caught:
        main(args)
    output = capsys.readouterr()
    assert caught.value.code == 2
    assert output.out == ''
    assert FIXED_ERROR in output.err
    return output.err


def test_stop_cli_persists_without_flush(tmp_path, capsys):
    home, settings = _home(tmp_path)
    now = AT + timedelta(minutes=1)
    code, result, error = _main(_args(home, settings, 'STOP', now,
                                      event_id='operator-stop'), capsys)
    assert code == 0 and error == ''
    assert result['result'] == 'STOPPED'
    assert result['real_sent'] is False
    status = inspect_managed_stop(home, now=now)
    assert status['status'] == 'STOPPED'
    assert status['persistent_stopped'] is True


def test_resume_without_reconciliation_returns_two_and_keeps_stop(tmp_path, capsys):
    home, settings = _home(tmp_path)
    stopped_at = AT + timedelta(minutes=1)
    _main(_args(home, settings, 'STOP', stopped_at), capsys)
    code, result, error = _main(
        _args(home, settings, 'RESUME', stopped_at + timedelta(minutes=1)), capsys)
    assert code == 2 and error == ''
    assert result['result'] == 'RECONCILIATION_REQUIRED'
    assert result['stop_status']['persistent_stopped'] is True


def test_reconciled_resume_clears_persistent_stop(tmp_path, capsys):
    home, settings = _home(tmp_path)
    stopped_at = AT + timedelta(minutes=1)
    resumed_at = stopped_at + timedelta(minutes=2)
    _main(_args(home, settings, 'STOP', stopped_at), capsys)
    code, result, error = _main(_args(
        home, settings, 'RESUME', resumed_at,
        reconciled_at=stopped_at + timedelta(minutes=1)), capsys)
    assert code == 0 and error == ''
    assert result['result'] == 'RESUMED'
    assert result['stop_status']['status'] == 'CLEAR'
    assert result['stop_status']['persistent_stopped'] is False


def test_resume_never_removes_stop_file(tmp_path, capsys):
    home, settings = _home(tmp_path)
    stopped_at = AT + timedelta(minutes=1)
    _main(_args(home, settings, 'STOP', stopped_at), capsys)
    stop_file = home/'STOP'
    stop_file.write_text('manual stop remains', encoding='utf-8')
    code, result, _ = _main(_args(
        home, settings, 'RESUME', stopped_at + timedelta(minutes=2),
        reconciled_at=stopped_at + timedelta(minutes=1)), capsys)
    assert code == 0 and result['result'] == 'RESUMED'
    assert stop_file.read_text(encoding='utf-8') == 'manual stop remains'
    assert result['stop_status']['status'] == 'STOPPED'
    assert result['stop_status']['persistent_stopped'] is False


def test_future_event_and_reversed_clock_are_fixed_errors(tmp_path, capsys):
    home, settings = _home(tmp_path)
    before = (home/'managed-stop-clock.json').read_bytes()
    _error(_args(home, settings, 'STOP', AT + timedelta(minutes=1),
                 event_at=AT + timedelta(minutes=2)), capsys)
    assert (home/'managed-stop-clock.json').read_bytes() == before
    _error(_args(home, settings, 'STOP', AT - timedelta(seconds=1)), capsys)
    assert (home/'managed-stop-clock.json').read_bytes() == before


def test_missing_notification_database_is_not_created(tmp_path, capsys):
    home, settings = _home(tmp_path)
    state = home/'notification.sqlite'
    state.unlink()
    _error(_args(home, settings, 'STOP', AT + timedelta(minutes=1)), capsys)
    assert not state.exists()


def test_settings_mismatch_is_rejected_without_state_change(tmp_path, capsys):
    home, settings = _home(tmp_path)
    changed = {'line': {'allowed_user_id': 'other', 'monthly_budget': 10}}
    settings.write_text(json.dumps(changed), encoding='utf-8')
    before = (home/'notification.sqlite').read_bytes()
    _error(_args(home, settings, 'STOP', AT + timedelta(minutes=1)), capsys)
    assert (home/'notification.sqlite').read_bytes() == before
    assert inspect_managed_stop(home, now=AT)['status'] == 'CLEAR'


def test_duplicate_json_is_rejected_without_secret_leak(tmp_path, capsys):
    home, settings = _home(tmp_path)
    secret = 'SUPER-SECRET-CONTROL-VALUE'
    settings.write_text(
        '{"line":{"allowed_user_id":"%s"},"line":{"allowed_user_id":"x"}}' % secret,
        encoding='utf-8')
    error = _error(_args(home, settings, 'STOP', AT + timedelta(minutes=1)), capsys)
    assert secret not in error
    assert inspect_managed_stop(home, now=AT)['status'] == 'CLEAR'


@pytest.mark.parametrize('target', ['settings', 'mock-runner.json', 'managed-stop-clock.json'])
def test_deep_json_is_fixed_error_without_control_mutation(tmp_path, capsys, target):
    home, settings = _home(tmp_path)
    # A first control may create its coordination lock before reading the clock.
    (home/'runner-lock.sqlite').touch()
    path = settings if target == 'settings' else home/target
    path.write_text('{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}', encoding='utf-8')
    before = {p.name: p.read_bytes() for p in home.iterdir() if p.is_file()}
    error = _error(_args(home, settings, 'STOP', AT + timedelta(minutes=1)), capsys)
    assert 'Traceback' not in error and 'RecursionError' not in error
    after = {p.name: p.read_bytes() for p in home.iterdir() if p.is_file()}
    assert after == before


def test_linked_settings_path_is_rejected_before_read(tmp_path, capsys, monkeypatch):
    from aitrader import managed_control

    home, settings = _home(tmp_path)
    reads = []
    original = managed_control._is_link
    monkeypatch.setattr(managed_control, '_is_link',
                        lambda path: path == settings or original(path))
    original_read = Path.read_bytes

    def observe(path):
        if path == settings:
            reads.append(path)
            pytest.fail('unsafe settings file was read')
        return original_read(path)

    monkeypatch.setattr(Path, 'read_bytes', observe)
    _error(_args(home, settings, 'STOP', AT + timedelta(minutes=1)), capsys)
    assert reads == []


def test_late_control_is_a_visible_nonzero_result(tmp_path, capsys):
    home, settings = _home(tmp_path)
    stopped_at = AT + timedelta(minutes=1)
    resumed_at = AT + timedelta(minutes=3)
    _main(_args(home, settings, 'STOP', stopped_at, event_at=stopped_at), capsys)
    _main(_args(home, settings, 'RESUME', resumed_at,
                reconciled_at=AT + timedelta(minutes=2), event_at=resumed_at), capsys)
    code, result, error = _main(_args(
        home, settings, 'STOP', AT + timedelta(minutes=4),
        event_at=AT + timedelta(minutes=2)), capsys)
    assert code == 2 and error == ''
    assert result['result'] == 'IGNORED'
    assert result['stop_status']['status'] == 'CLEAR'
