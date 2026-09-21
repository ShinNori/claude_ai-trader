"""Mask collision audit probes through review(); synthetic and offline."""
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'common/tests/phase2')]
import test_judges as j

offline = j.offline


@pytest.mark.parametrize('judge', ['claude', 'codex'])
@pytest.mark.parametrize('kind', ['secret', 'truncation', 'nested'])
def test_mask_collision_retains_every_audit_entry(tmp_path, monkeypatch, judge, kind):
    secret = 'dummy-mask-collision-secret'
    placeholder = '<LINE_CHANNEL_ACCESS_TOKEN>'
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', secret)
    if kind == 'truncation':
        prefix = 'あ' * 22000
        extra = {prefix + 'a': 'first', prefix + 'b': 'second'}
    else:
        extra = {secret: 'first', placeholder: 'second',
                 '__masked_key_collision__': 'literal-user-key'}
    record = j.record(judge)
    record['extra'] = [extra] if kind == 'nested' else extra
    original = deepcopy(record)
    result = j.run(tmp_path, judge, record)
    assert result['failure'] is None
    assert result['verdict'].decision == 'APPROVE'
    assert record == original, 'logging must not mutate caller input'
    raw = next((tmp_path / 'decisions').glob('*.jsonl')).read_text(encoding='utf-8')
    assert secret not in raw
    logged = json.loads(raw)['record']['extra']
    if kind == 'nested':
        logged = logged[0]
    entries = logged['__masked_key_collision__']
    assert [entry['value'] for entry in entries][:2] == ['first', 'second']
    assert entries[0]['key'] == entries[1]['key']
    assert len(entries) == len(extra)
    if kind != 'truncation':
        assert entries[-1] == {'key': '__masked_key_collision__', 'value': 'literal-user-key'}


@pytest.mark.parametrize('judge', ['claude', 'codex'])
def test_mask_noncolliding_dictionary_keeps_shape(tmp_path, monkeypatch, judge):
    secret = 'dummy-mask-collision-secret'
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', secret)
    response = j.record(judge)
    response['extra'] = {'prefix-' + secret: ['value-' + secret], 'count': 2}
    j.run(tmp_path, judge, response)
    logged = json.loads(next((tmp_path / 'decisions').glob('*.jsonl')).read_text(encoding='utf-8'))
    assert logged['record']['extra'] == {
        'prefix-<LINE_CHANNEL_ACCESS_TOKEN>': ['value-<LINE_CHANNEL_ACCESS_TOKEN>'], 'count': 2}
