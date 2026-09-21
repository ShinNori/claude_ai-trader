"""CLI boundaries for offline history/validity binding inspection."""
import json
import os
from pathlib import Path

import pytest

import aitrader.history_validity_binding_cli as cli


ERROR = '履歴と適用期間を検査できません。入力形式とサイズを確認してください。\n'


def _result(status='VERIFIED_OFFLINE_BINDING'):
    return {
        'mode': 'history_validity_binding_fixture_v1',
        'source_origin': 'offline_fixture',
        'status': status,
        'selection_sha256': '0' * 64 if status == 'VERIFIED_OFFLINE_BINDING' else None,
        'reason_codes': [] if status == 'VERIFIED_OFFLINE_BINDING' else ['INVALID_BUNDLE'],
        'required_subject_count': 2,
        'matched_subject_count': 2 if status == 'VERIFIED_OFFLINE_BINDING' else 0,
        'extra_subject_count': 0,
        'corrected_after_as_of_count': 0,
        'period_evidence_timed': False,
        'ready_for_live': False,
        'current_signal': False,
        'read_only': True,
    }


def test_valid_example_reaches_real_binding_api(capsys):
    source = Path(__file__).parents[1] / 'examples/history_validity_binding_valid.json'
    assert cli.main(['--input', str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    result = json.loads(captured.out)
    assert result['status'] == 'VERIFIED_OFFLINE_BINDING'
    assert result['required_subject_count'] == result['matched_subject_count'] == 2
    assert result['period_evidence_timed'] is False
    assert result['ready_for_live'] is result['current_signal'] is False
    assert result['read_only'] is True


def test_success_and_data_incomplete_are_json_results(tmp_path, capsys, monkeypatch):
    source = tmp_path / 'binding.json'
    source.write_text('{}', encoding='utf-8')
    expected = _result()
    monkeypatch.setattr(cli, 'inspect_history_validity_binding', lambda value: expected)
    assert cli.main(['--input', str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    assert json.loads(captured.out) == expected

    expected = _result('DATA_INCOMPLETE')
    monkeypatch.setattr(cli, 'inspect_history_validity_binding', lambda value: expected)
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    assert json.loads(captured.out) == expected


def test_exact_one_mib_calls_api_but_one_byte_over_does_not(
        tmp_path, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli, 'inspect_history_validity_binding',
        lambda value: calls.append(value) or _result())
    for excess in (0, 1):
        source = tmp_path / f'binding-{excess}.json'
        source.write_bytes(b'{}' + b' ' * (1024 * 1024 + excess - 2))
        assert cli.main(['--input', str(source)]) == (2 if excess else 0)
        captured = capsys.readouterr()
        if excess:
            assert captured.out == ''
            assert captured.err == ERROR
        else:
            assert captured.err == ''
            assert json.loads(captured.out)['status'] == 'VERIFIED_OFFLINE_BINDING'
    assert calls == [{}]


@pytest.mark.parametrize('body', [
    b'{"entries":[],"entries":[]}',
    b'{"value":NaN}',
    b'{"value":Infinity}',
    b'{',
])
def test_unsafe_or_invalid_json_uses_fixed_error_without_api_call(
        tmp_path, capsys, monkeypatch, body):
    monkeypatch.setattr(
        cli, 'inspect_history_validity_binding',
        lambda value: pytest.fail('API must not be called'))
    source = tmp_path / 'private-name.json'
    source.write_bytes(body)
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ERROR
    assert str(source) not in captured.err


def test_link_input_uses_fixed_error_without_api_call(
        tmp_path, capsys, monkeypatch):
    target = tmp_path / 'target.json'
    target.write_text('{}', encoding='utf-8')
    source = tmp_path / 'binding-link.json'
    try:
        os.symlink(target, source)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f'symlink unavailable: {error}')
    monkeypatch.setattr(
        cli, 'inspect_history_validity_binding',
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

    source = tmp_path / 'binding.json'
    source.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(
        cli, 'inspect_history_validity_binding',
        lambda value: (_ for _ in ()).throw(ValueError('private detail')))
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ERROR
