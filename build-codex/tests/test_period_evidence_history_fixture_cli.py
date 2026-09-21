"""CLI boundaries for offline period history inspection."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import aitrader.period_evidence_history_fixture_cli as cli
import aitrader.packet_cli as reader


ERROR = '期間証拠履歴を検査できません。入力形式と系列の参照を確認してください。\n'


def _result(status='VERIFIED_OFFLINE_PERIOD_HISTORY'):
    return {
        'mode': 'period_evidence_history_fixture_v1',
        'source_origin': 'offline_fixture',
        'status': status,
        'selection_sha256': '0' * 64 if status == 'VERIFIED_OFFLINE_PERIOD_HISTORY' else None,
        'reason_codes': [] if status == 'VERIFIED_OFFLINE_PERIOD_HISTORY' else ['INVALID_BUNDLE'],
        'receipt_count': 3,
        'revision_count': 3,
        'noop_receipt_count': 0,
        'series_count': 2,
        'selected_series_count': 2,
        'future_revision_count': 1,
        'ready_for_live': False,
        'current_signal': False,
        'read_only': True,
    }


def test_valid_example_reaches_real_period_api(capsys):
    source = Path(__file__).parents[1] / 'examples/period_evidence_history_valid.json'
    assert cli.main(['--input', str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    result = json.loads(captured.out)
    assert result['status'] == 'VERIFIED_OFFLINE_PERIOD_HISTORY'
    assert result['receipt_count'] == result['revision_count'] == 3
    assert result['series_count'] == result['selected_series_count'] == 2
    assert result['future_revision_count'] == 1
    assert len(result) == 14
    assert result['selection_sha256'] == 'ba6486dd33d76cf9594f6e43795f45c70349bee87e40fa6f13e0abcb01d186ca'
    assert result['ready_for_live'] is result['current_signal'] is False
    assert result['read_only'] is True


def test_success_and_data_incomplete_are_json_results(tmp_path, capsys, monkeypatch):
    source = tmp_path / 'period.json'
    source.write_text('{}', encoding='utf-8')
    expected = _result()
    monkeypatch.setattr(cli, 'inspect_period_evidence_history', lambda value: expected)
    assert cli.main(['--input', str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    assert json.loads(captured.out) == expected

    expected = _result('DATA_INCOMPLETE')
    monkeypatch.setattr(cli, 'inspect_period_evidence_history', lambda value: expected)
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    assert json.loads(captured.out) == expected


def test_exact_one_mib_calls_api_but_one_byte_over_does_not(
        tmp_path, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli, 'inspect_period_evidence_history',
        lambda value: calls.append(value) or _result())
    for excess in (0, 1):
        source = tmp_path / f'period-{excess}.json'
        source.write_bytes(b'{}' + b' ' * (1024 * 1024 + excess - 2))
        assert cli.main(['--input', str(source)]) == (2 if excess else 0)
        captured = capsys.readouterr()
        if excess:
            assert captured.out == ''
            assert captured.err == ERROR
        else:
            assert captured.err == ''
            assert json.loads(captured.out)['status'] == 'VERIFIED_OFFLINE_PERIOD_HISTORY'
    assert calls == [{}]


@pytest.mark.parametrize('body', [
    b'{"entries":[],"entries":[]}',
    b'{"value":NaN}',
    b'{"value":Infinity}',
    b'{"value":-Infinity}',
    b'{"nested":[1e400]}',
    b'{"nested":{"x":1,"x":2}}',
    b'[]',
    b'{',
])
def test_unsafe_or_invalid_json_uses_fixed_error_without_api_call(
        tmp_path, capsys, monkeypatch, body):
    monkeypatch.setattr(
        cli, 'inspect_period_evidence_history',
        lambda value: pytest.fail('API must not be called'))
    source = tmp_path / 'private-name.json'
    source.write_bytes(body)
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ERROR


def test_real_module_cli_fixed_output():
    root = Path(__file__).parents[1]
    source = root / 'examples/period_evidence_history_valid.json'
    completed = subprocess.run(
        [sys.executable, '-B', '-m', 'aitrader.period_evidence_history_fixture_cli',
         '--input', str(source)], cwd=root, capture_output=True,
        encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1',
                               'PYTHONDONTWRITEBYTECODE': '1'}, timeout=30)
    assert completed.returncode == 0
    assert completed.stderr == ''
    assert len(completed.stdout.splitlines()) == 1
    expected = _result()
    expected['selection_sha256'] = (
        'ba6486dd33d76cf9594f6e43795f45c70349bee87e40fa6f13e0abcb01d186ca')
    assert json.loads(completed.stdout) == expected


def test_real_api_rejection_remains_json(tmp_path, capsys):
    source = tmp_path / 'invalid.json'
    source.write_text('{}', encoding='utf-8')
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    assert len(captured.out.splitlines()) == 1
    result = json.loads(captured.out)
    assert result['status'] == 'DATA_INCOMPLETE'
    assert result['reason_codes'] == ['INVALID_BUNDLE']
    assert len(result) == 14


def test_safe_reader_is_reused():
    assert cli._mapping is reader._mapping


def test_reparse_point_is_rejected_without_api_call(tmp_path, capsys, monkeypatch):
    source = tmp_path / 'reparse.json'
    source.write_text('{}', encoding='utf-8')
    original = Path.lstat

    def observed(path):
        info = original(path)
        if path == source:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, 'lstat', observed)
    monkeypatch.setattr(cli, 'inspect_period_evidence_history',
                        lambda value: pytest.fail('API must not be called'))
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ERROR


def test_read_change_is_rejected_without_api_call(tmp_path, capsys, monkeypatch):
    source = tmp_path / 'changed.json'
    source.write_text('{}', encoding='utf-8')
    original = reader.os.fstat
    calls = []

    def observed(fd):
        info = original(fd)
        calls.append(fd)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns')
        values = {field: getattr(info, field) for field in fields}
        if len(calls) == 2:
            values['st_mtime_ns'] += 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(reader.os, 'fstat', observed)
    monkeypatch.setattr(cli, 'inspect_period_evidence_history',
                        lambda value: pytest.fail('API must not be called'))
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ERROR
    assert str(source) not in captured.err


def test_link_input_uses_fixed_error_without_api_call(
        tmp_path, capsys, monkeypatch):
    target = tmp_path / 'target.json'
    target.write_text('{}', encoding='utf-8')
    source = tmp_path / 'period-link.json'
    try:
        os.symlink(target, source)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f'symlink unavailable: {error}')
    monkeypatch.setattr(
        cli, 'inspect_period_evidence_history',
        lambda value: pytest.fail('API must not be called'))
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ERROR


def test_argument_and_api_failures_use_fixed_error(tmp_path, capsys, monkeypatch):
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ERROR

    source = tmp_path / 'period.json'
    source.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(
        cli, 'inspect_period_evidence_history',
        lambda value: (_ for _ in ()).throw(ValueError('private detail')))
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ERROR

