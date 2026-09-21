"""Artificial history file inspection must leave trading state untouched."""
import hashlib
import json

import pytest

from aitrader.evidence_history_fixture_cli import main


def sample():
    payload = {'code': '0001', 'lot_size': 100}
    sha = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                   separators=(',', ':')).encode('utf-8')).hexdigest()
    return {'mode': 'evidence_history_fixture_v1', 'source_origin': 'offline_fixture',
            'decision_at': '2026-09-11T07:00:00+09:00', 'entries': [{
                'receipt_id': 'secret-receipt', 'revision_id': 'secret-revision',
                'subject': {'group': 'lots', 'code': '0001'},
                'observed_at': '2026-09-10T16:00:00+09:00',
                'recorded_at': '2026-09-10T16:01:00+09:00',
                'payload': payload, 'sha256': sha, 'supersedes': None}]}


def test_history_cli_metadata_and_no_trading_writes(tmp_path, capsys):
    source = tmp_path / 'history.json'
    source.write_text(json.dumps(sample()), encoding='utf-8')
    (tmp_path / 'ledger.sqlite').write_bytes(b'database-sentinel')
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    assert main(['--input', str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    result = json.loads(captured.out)
    assert result['status'] == 'VERIFIED_OFFLINE_HISTORY'
    assert result['receipt_count'] == result['revision_count'] == result['selected_revision_count'] == 1
    assert result['ready_for_live'] is result['current_signal'] is False
    assert 'secret' not in captured.out and '0001' not in captured.out
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == before


def test_duplicate_receipt_content_conflict_fails_closed(tmp_path, capsys):
    value = sample()
    conflicting = dict(value['entries'][0], revision_id='another-revision')
    value['entries'].append(conflicting)
    source = tmp_path / 'history.json'
    source.write_text(json.dumps(value), encoding='utf-8')
    assert main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    result = json.loads(captured.out)
    assert result['status'] == 'DATA_INCOMPLETE'
    assert result['receipt_count'] == 0
    assert result['selection_sha256'] is None


@pytest.mark.parametrize('text', ['{"secret":', '{"entries": [], "entries": []}', '{"x":Infinity}'])
def test_bad_file_is_not_echoed(tmp_path, capsys, text):
    source = tmp_path / 'secret.json'
    source.write_text(text, encoding='utf-8')
    assert main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == '証拠履歴を検査できません。入力形式と訂正の参照を確認してください。\n'
