"""Process and failure-boundary contracts for both v2 history CLIs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import aitrader.evidence_history_fixture_v2_cli as history_cli
import aitrader.history_validity_binding_v2_cli as binding_cli


ROOT = Path(__file__).parents[1]
HISTORY_ERROR = '証拠履歴を検査できません。入力形式と訂正の参照を確認してください。\n'
BINDING_ERROR = '履歴と適用期間を検査できません。入力形式とサイズを確認してください。\n'
HISTORY_OK = {
    'current_signal': False,
    'future_revision_count': 1,
    'mode': 'evidence_history_fixture_v2',
    'noop_receipt_count': 0,
    'read_only': True,
    'ready_for_live': False,
    'reason_codes': [],
    'receipt_count': 2,
    'revision_count': 2,
    'selected_revision_count': 1,
    'selection_sha256': '6b9fb9c746fdf4e5164b75141c0f6df11fbb5c47152cd99605c146c92b9b9495',
    'source_origin': 'offline_fixture',
    'status': 'VERIFIED_OFFLINE_HISTORY',
}
BINDING_OK = {
    'corrected_after_as_of_count': 0,
    'current_signal': False,
    'extra_subject_count': 0,
    'matched_subject_count': 2,
    'mode': 'history_validity_binding_fixture_v2',
    'period_candidate_count': 2,
    'period_evidence_timed': True,
    'period_series_count': 2,
    'read_only': True,
    'ready_for_live': False,
    'reason_codes': [],
    'required_subject_count': 2,
    'selection_sha256': 'e6f112d03807f3f9c806106890a95900a11680e150a037557c913b427db22bec',
    'source_origin': 'offline_fixture',
    'status': 'VERIFIED_OFFLINE_BINDING',
}


def _history_value():
    value = json.loads(
        (ROOT / 'examples/evidence_history_valid.json').read_text(encoding='utf-8'))
    value['mode'] = 'evidence_history_fixture_v2'
    return value


def _binding_value():
    return json.loads((
        ROOT / 'examples/history_validity_binding_v2_valid.json'
    ).read_text(encoding='utf-8'))


CASES = [
    pytest.param(history_cli, 'aitrader.evidence_history_fixture_v2_cli',
                 _history_value, HISTORY_OK, HISTORY_ERROR,
                 'VERIFIED_OFFLINE_HISTORY', id='history'),
    pytest.param(binding_cli, 'aitrader.history_validity_binding_v2_cli',
                 _binding_value, BINDING_OK, BINDING_ERROR,
                 'VERIFIED_OFFLINE_BINDING', id='binding'),
]


@pytest.mark.parametrize(
    'cli,module_name,value_factory,expected,error,success_status', CASES)
def test_real_subprocess_success_is_one_fixed_json_line(
        tmp_path, cli, module_name, value_factory, expected, error,
        success_status):
    del cli, error, success_status
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(value_factory()), encoding='utf-8')
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    completed = subprocess.run(
        [sys.executable, '-m', module_name, '--input', str(source)],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
    assert completed.returncode == 0
    assert completed.stderr == ''
    assert completed.stdout == json.dumps(
        expected, ensure_ascii=False, sort_keys=True) + '\n'
    assert len(json.loads(completed.stdout)) == (13 if module_name.endswith(
        'evidence_history_fixture_v2_cli') else 15)


@pytest.mark.parametrize(
    'cli,module_name,value_factory,expected,error,success_status', CASES)
def test_inspected_failure_is_json_and_exit_two(
        tmp_path, capsys, cli, module_name, value_factory, expected, error,
        success_status):
    del module_name, expected, error, success_status
    value = value_factory()
    value['mode'] = 'wrong-mode'
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(value), encoding='utf-8')
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    result = json.loads(captured.out)
    assert result['status'] == 'DATA_INCOMPLETE'
    assert result['reason_codes'] == [
        'INVALID_MODE' if cli is history_cli else 'INVALID_BUNDLE']
    assert captured.out.count('\n') == 1


@pytest.mark.parametrize(
    'cli,module_name,value_factory,expected,error,success_status', CASES)
def test_missing_argument_and_missing_file_use_exact_fixed_error(
        tmp_path, capsys, cli, module_name, value_factory, expected, error,
        success_status):
    del module_name, value_factory, expected, success_status
    for argv in ([], ['--input', str(tmp_path / 'private-missing.json')]):
        assert cli.main(argv) == 2
        captured = capsys.readouterr()
        assert captured.out == ''
        assert captured.err == error


@pytest.mark.parametrize(
    'cli,module_name,value_factory,expected,error,success_status', CASES)
@pytest.mark.parametrize('body', [
    b'{', b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}',
])
def test_malformed_duplicate_and_nonfinite_input_never_reaches_api(
        tmp_path, capsys, monkeypatch, cli, module_name, value_factory,
        expected, error, success_status, body):
    del module_name, value_factory, expected, success_status
    monkeypatch.setattr(
        cli, cli.inspect_evidence_history.__name__ if cli is history_cli
        else cli.inspect_history_validity_binding.__name__,
        lambda value: pytest.fail(f'API called with {value!r}'))
    source = tmp_path / 'private.json'
    source.write_bytes(body)
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == error


@pytest.mark.parametrize(
    'cli,module_name,value_factory,expected,error,success_status', CASES)
def test_one_mib_is_allowed_and_one_byte_over_is_rejected_before_api(
        tmp_path, capsys, monkeypatch, cli, module_name, value_factory,
        expected, error, success_status):
    del module_name, value_factory
    calls = []
    api_name = ('inspect_evidence_history' if cli is history_cli
                else 'inspect_history_validity_binding')
    monkeypatch.setattr(
        cli, api_name, lambda value: calls.append(value) or expected)
    for excess in (0, 1):
        source = tmp_path / f'boundary-{excess}.json'
        source.write_bytes(b'{}' + b' ' * (1024 * 1024 + excess - 2))
        assert cli.main(['--input', str(source)]) == (
            0 if excess == 0 else 2)
        captured = capsys.readouterr()
        if excess:
            assert captured.out == ''
            assert captured.err == error
        else:
            assert captured.err == ''
            assert json.loads(captured.out)['status'] == success_status
    assert calls == [{}]


@pytest.mark.parametrize(
    'cli,module_name,value_factory,expected,error,success_status', CASES)
def test_api_exception_details_never_leak(
        tmp_path, capsys, monkeypatch, cli, module_name, value_factory,
        expected, error, success_status):
    del module_name, value_factory, expected, success_status
    api_name = ('inspect_evidence_history' if cli is history_cli
                else 'inspect_history_validity_binding')
    monkeypatch.setattr(
        cli, api_name,
        lambda value: (_ for _ in ()).throw(RuntimeError(
            'secret receipt and private path')))
    source = tmp_path / 'secret-name.json'
    source.write_text('{}', encoding='utf-8')
    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == error
    assert 'secret' not in captured.err and str(source) not in captured.err
