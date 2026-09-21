"""Additional contract checks for the evidence-history v2 CLI."""
from __future__ import annotations

from copy import deepcopy
from datetime import timezone
import json
import os
from pathlib import Path

import pytest

import aitrader.evidence_history_fixture_cli as v1_cli
import aitrader.evidence_history_fixture_v2_cli as cli
from aitrader.packet_cli import _mapping
from aitrader.strict_input import _aware
from aitrader.strict_input_cli import _Parser


ROOT = Path(__file__).parents[1]
ERROR = '証拠履歴を検査できません。入力形式と訂正の参照を確認してください。\n'


def _v2_value():
    value = json.loads((
        ROOT / 'examples/evidence_history_valid.json'
    ).read_text(encoding='utf-8'))
    value['mode'] = 'evidence_history_fixture_v2'
    return value


def _utc_spelling(value):
    instant = _aware(value)
    return instant.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def test_cli_reuses_the_closed_shared_reader_and_parser_objects():
    assert cli._mapping is _mapping
    assert cli._Parser is _Parser


def test_v1_bundle_reaches_v2_api_and_is_json_invalid_mode(tmp_path, capsys):
    source = tmp_path / 'history-v1.json'
    source.write_bytes((ROOT / 'examples/evidence_history_valid.json').read_bytes())

    assert cli.main(['--input', str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    result = json.loads(captured.out)
    assert result['status'] == 'DATA_INCOMPLETE'
    assert result['reason_codes'] == ['INVALID_MODE']
    assert captured.out.count('\n') == 1


def test_equivalent_utc_timestamp_spellings_keep_the_cli_digest(tmp_path, capsys):
    jst = _v2_value()
    utc = deepcopy(jst)
    utc['decision_at'] = _utc_spelling(utc['decision_at'])
    for entry in utc['entries']:
        entry['observed_at'] = _utc_spelling(entry['observed_at'])
        entry['recorded_at'] = _utc_spelling(entry['recorded_at'])

    digests = []
    for name, value in [('jst', jst), ('utc', utc)]:
        source = tmp_path / f'{name}.json'
        source.write_text(json.dumps(value), encoding='utf-8')
        assert cli.main(['--input', str(source)]) == 0
        captured = capsys.readouterr()
        assert captured.err == ''
        digests.append(json.loads(captured.out)['selection_sha256'])
    assert digests[0] == digests[1]


def test_link_input_is_rejected_before_the_v2_api(tmp_path, capsys, monkeypatch):
    target = tmp_path / 'target.json'
    target.write_text(json.dumps(_v2_value()), encoding='utf-8')
    source = tmp_path / 'history-link.json'
    try:
        os.symlink(target, source)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f'symlink unavailable: {error}')
    monkeypatch.setattr(
        cli, 'inspect_evidence_history',
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
