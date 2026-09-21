"""File and process boundaries of offline coverage inspection."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from aitrader.strict_coverage_cli import main


def sample():
    payload = {'page_id': 'page-private', 'rows': [{'code': 'private-code', 'date': '2026-09-11'}]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':')).encode('utf-8')).hexdigest()
    return {'mode': 'strict_coverage_v1', 'source_origin': 'offline_fixture',
            'request': {'start': '2026-09-11', 'end': '2026-09-11',
                        'expected_pages': ['page-private'], 'expected_records': payload['rows']},
            'source_documents': [{'id': 'document-private', 'sha256': digest, 'payload': payload}]}


def test_real_module_entrypoint_is_read_only_and_metadata_only(tmp_path):
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(sample()), encoding='utf-8')
    database = tmp_path / 'ledger.sqlite'
    database.write_bytes(b'untouched-trading-state')
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    build = str(Path(__file__).resolve().parents[1])
    env['PYTHONPATH'] = build + os.pathsep + env.get('PYTHONPATH', '')
    result = subprocess.run([sys.executable, '-m', 'aitrader.strict_coverage_cli', '--input', str(source)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    metadata = json.loads(result.stdout)
    assert metadata['status'] == 'VERIFIED_OFFLINE_COVERAGE'
    assert metadata['read_only'] is True
    assert metadata['ready_for_live'] is metadata['current_signal'] is False
    assert 'private' not in result.stdout
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == before


def test_missing_page_returns_incomplete_json(tmp_path, capsys):
    value = sample()
    value['request']['expected_pages'].append('missing-page')
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(value), encoding='utf-8')
    assert main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    result = json.loads(captured.out)
    assert result['status'] == 'DATA_INCOMPLETE'
    assert 'PAGE_SET_MISMATCH' in result['reason_codes']
    assert result['current_signal'] is False


@pytest.mark.parametrize('content', ['{"secret":"SENSITIVE', '{"mode":1,"mode":2}',
                                     '{"x":NaN}', '{' + ' ' * (1024 * 1024) + '}'],
                         ids=['malformed', 'duplicate-key', 'nonfinite', 'oversized'])
def test_invalid_file_has_fixed_error_without_echo(tmp_path, capsys, content):
    source = tmp_path / 'SENSITIVE.json'
    source.write_text(content, encoding='utf-8')
    assert main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == '対象範囲の検査に失敗しました。入力形式と原本の対応を確認してください。\n'


def test_missing_arguments_have_fixed_error(capsys):
    assert main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == '対象範囲の検査に失敗しました。入力形式と原本の対応を確認してください。\n'
