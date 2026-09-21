"""Recorded page-chain CLI success and failure boundaries."""
import json

import pytest

from aitrader.jquants_v2_page_chain_cli import main


def sample():
    return {'mode': 'v2_page_chain_fixture_v1', 'dataset': 'calendar', 'pages': [
        {'query': {'from': '2026-09-10', 'to': '2026-09-11'}, 'request_token': None,
         'response': {'data': [{'Date': '2026-09-10', 'HolDiv': '1'}], 'pagination_key': 'secret-token'}},
        {'query': {'from': '2026-09-10', 'to': '2026-09-11'}, 'request_token': 'secret-token',
         'response': {'data': [{'Date': '2026-09-11', 'HolDiv': '1'}]}}]}


def test_valid_chain_metadata_and_no_writes(tmp_path, capsys):
    source = tmp_path / 'chain.json'
    source.write_text(json.dumps(sample()), encoding='utf-8')
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    assert main(['--input', str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    assert 'secret-token' not in captured.out
    result = json.loads(captured.out)
    assert result['status'] == 'VERIFIED_OFFLINE_PAGE_CHAIN'
    assert result['page_count'] == result['row_count'] == 2
    assert result['consistent_snapshot_verified'] is False
    assert result['ready_for_live'] is result['current_signal'] is False
    assert list(tmp_path.iterdir()) == [source]
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_unfinished_chain_fails_with_metadata(tmp_path, capsys):
    value = sample()
    value['pages'].pop()
    source = tmp_path / 'chain.json'
    source.write_text(json.dumps(value), encoding='utf-8')
    assert main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    assert 'MISSING_TERMINAL' in json.loads(captured.out)['reason_codes']
    assert 'secret-token' not in captured.out


@pytest.mark.parametrize('text', ['{"secret":', '{"pages": [], "pages": []}', '{"x": Infinity}'],
                         ids=['malformed', 'duplicate-key', 'nonfinite'])
def test_invalid_json_does_not_echo(tmp_path, capsys, text):
    source = tmp_path / 'SECRET.json'
    source.write_text(text, encoding='utf-8')
    assert main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == 'ページ記録を検査できません。入力形式と取得順の記録を確認してください。\n'
