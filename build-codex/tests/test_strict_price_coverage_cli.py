"""End-to-end file boundary for selected-price coverage."""
import json
from pathlib import Path

import pytest

from aitrader.strict_price_coverage_cli import main


def files(tmp_path):
    source = tmp_path / 'input.json'
    source.write_bytes((Path(__file__).resolve().parents[1] / 'examples' /
                        'strict_input_valid.json').read_bytes())
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'start': '2026-09-11', 'end': '2026-09-11',
                                  'expected_pages': ['artificial-example'],
                                  'expected_records': [{'code': '0001', 'date': '2026-09-11'}]}),
                       encoding='utf-8')
    return source, request


def test_two_files_are_checked_without_writing(tmp_path, capsys):
    source, request = files(tmp_path)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    assert main(['--input', str(source), '--request', str(request)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    result = json.loads(captured.out)
    assert result['status'] == 'VERIFIED_OFFLINE_PRICE_COVERAGE'
    assert result['scope'] == 'selected_price_records'
    assert result['ready_for_live'] is result['current_signal'] is False
    assert '0001' not in captured.out
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize('target', ['input', 'request'])
def test_either_malformed_file_fails_without_echo(tmp_path, capsys, target):
    source, request = files(tmp_path)
    (source if target == 'input' else request).write_text('{"secret":"DO_NOT_ECHO', encoding='utf-8')
    assert main(['--input', str(source), '--request', str(request)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == '価格原本の範囲照合に失敗しました。入力と要求範囲を確認してください。\n'


def test_wrong_expected_day_is_incomplete_not_success(tmp_path, capsys):
    source, request = files(tmp_path)
    data = json.loads(request.read_text(encoding='utf-8'))
    data.update(start='2026-09-10', end='2026-09-11')
    data['expected_records'][0]['date'] = '2026-09-10'
    request.write_text(json.dumps(data), encoding='utf-8')
    assert main(['--input', str(source), '--request', str(request)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'DATA_INCOMPLETE'
    assert 'COVERAGE_RECORD_SET_MISMATCH' in result['reason_codes']
