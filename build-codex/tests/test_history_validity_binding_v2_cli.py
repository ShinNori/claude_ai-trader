"""Additional contract checks for the history/validity binding v2 CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import aitrader.history_validity_binding_cli as v1_cli
import aitrader.history_validity_binding_v2_cli as cli
from aitrader.packet_cli import _mapping
from aitrader.strict_input_cli import _Parser


ROOT = Path(__file__).parents[1]
ERROR = '履歴と適用期間を検査できません。入力形式とサイズを確認してください。\n'


def test_cli_reuses_the_closed_shared_reader_and_parser_objects():
    assert cli._mapping is _mapping
    assert cli._Parser is _Parser


def test_v1_bundle_reaches_v2_api_and_is_json_invalid_bundle(tmp_path, capsys):
    source = tmp_path / 'binding-v1.json'
    source.write_bytes((
        ROOT / 'examples/history_validity_binding_valid.json'
    ).read_bytes())

    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    result = json.loads(captured.out)
    assert result['status'] == 'DATA_INCOMPLETE'
    assert result['reason_codes'] == ['INVALID_BUNDLE']
    assert captured.out.count('\n') == 1


def test_link_input_is_rejected_before_the_v2_api(tmp_path, capsys, monkeypatch):
    target = tmp_path / 'target.json'
    target.write_bytes((
        ROOT / 'examples/history_validity_binding_v2_valid.json'
    ).read_bytes())
    source = tmp_path / 'binding-link.json'
    try:
        os.symlink(target, source)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f'symlink unavailable: {error}')
    monkeypatch.setattr(
        cli, 'inspect_history_validity_binding',
        lambda value: pytest.fail(f'API called with {value!r}'))

    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ERROR


def test_all_pre_api_failures_use_the_single_adopted_stderr(tmp_path, capsys):
    assert cli._ERROR == v1_cli._ERROR
    sources = []
    for name, body in [('syntax.json', b'{'), ('non-dict.json', b'[]')]:
        source = tmp_path / name
        source.write_bytes(body)
        sources.append(source)
    for argv in ([], *(['--input', str(source)] for source in sources)):
        assert cli.main(argv) == 2
        captured = capsys.readouterr()
        assert captured.out == ''
        assert captured.err == ERROR
