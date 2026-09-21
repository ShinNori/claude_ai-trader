"""File boundary for explicit artificial validity evidence."""
import hashlib
import json
from pathlib import Path

import pytest

from aitrader.strict_validity_cli import main


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def sample():
    source = Path(__file__).resolve().parents[1] / 'examples' / 'strict_input_valid.json'
    original = json.loads(source.read_text(encoding='utf-8'))
    evidence = []
    for group in ('lots', 'events'):
        record = original['source_documents'][0]['payload'][group]['0001']
        payload = {'group': group, 'code': '0001', 'record_sha256': digest(record),
                   'valid_from': '2026-09-10T00:00:00+09:00',
                   'valid_until': '2026-09-13T00:00:00+09:00'}
        evidence.append({'id': 'private-' + group, 'sha256': digest(payload), 'payload': payload})
    return {'mode': 'strict_validity_fixture_v1', 'source_origin': 'offline_fixture',
            'input': original, 'validity_documents': evidence}


def test_validity_cli_is_read_only_and_metadata_only(tmp_path, capsys):
    source = tmp_path / 'validity.json'
    source.write_text(json.dumps(sample()), encoding='utf-8')
    database = tmp_path / 'ledger.sqlite'
    database.write_bytes(b'existing-state')
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    assert main(['--input', str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    metadata = json.loads(captured.out)
    assert metadata['status'] == 'VERIFIED_OFFLINE_VALIDITY'
    assert metadata['verified_evidence_count'] == 2
    assert metadata['ready_for_live'] is metadata['current_signal'] is False
    assert 'private' not in captured.out and '0001' not in captured.out
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == before


def test_expired_evidence_returns_incomplete_json(tmp_path, capsys):
    value = sample()
    doc = value['validity_documents'][0]
    doc['payload']['valid_until'] = value['input']['as_of']
    doc['sha256'] = digest(doc['payload'])
    source = tmp_path / 'expired.json'
    source.write_text(json.dumps(value), encoding='utf-8')
    assert main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    result = json.loads(captured.out)
    assert result['status'] == 'DATA_INCOMPLETE'
    assert 'VALIDITY_EXPIRED' in result['reason_codes']


@pytest.mark.parametrize('text', ['{"private":', '{"input":1,"input":2}', '{"bad":NaN}'])
def test_bad_json_does_not_echo(tmp_path, capsys, text):
    source = tmp_path / 'PRIVATE.json'
    source.write_text(text, encoding='utf-8')
    assert main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == '適用期間の検査に失敗しました。入力形式と原本の対応を確認してください。\n'
