"""Safe local mapping inputs for packet generation; no ops dependency."""
import json
import os
from datetime import date
from pathlib import Path

import pytest

import aitrader.packet_cli as packet_cli
from aitrader.db import init, connect


def test_none_and_relative_bom_mapping_are_compatible(tmp_path, monkeypatch):
    assert packet_cli._mapping(None) == {}
    monkeypatch.chdir(tmp_path)
    Path('events.json').write_bytes(
        b'\xef\xbb\xbf' + json.dumps({'6857': {'note': '日本語'}},
                                      ensure_ascii=False).encode('utf-8'))
    assert packet_cli._mapping('events.json') == {'6857': {'note': '日本語'}}


@pytest.mark.parametrize('body', [
    '{"6857":1,"6857":2}',
    '{"6857":{"x":1,"x":2}}',
])
def test_duplicate_keys_at_any_depth_are_rejected(tmp_path, body):
    path = tmp_path/'mapping.json'
    path.write_text(body, encoding='utf-8')
    with pytest.raises(ValueError):
        packet_cli._mapping(path)


@pytest.mark.parametrize('token', ['NaN', 'Infinity', '-Infinity', '1e999'])
def test_nonfinite_numbers_are_rejected(tmp_path, token):
    path = tmp_path/'mapping.json'
    path.write_text('{"6857":{"value":'+token+'}}', encoding='utf-8')
    with pytest.raises(ValueError):
        packet_cli._mapping(path)


@pytest.mark.parametrize('body', ['[]', 'null', '"text"', '1'])
def test_top_level_must_be_object(tmp_path, body):
    path = tmp_path/'mapping.json'
    path.write_text(body, encoding='utf-8')
    with pytest.raises(ValueError):
        packet_cli._mapping(path)


def test_invalid_utf8_is_rejected(tmp_path):
    path = tmp_path/'mapping.json'
    path.write_bytes(b'{"x":"\xff"}')
    with pytest.raises(UnicodeDecodeError):
        packet_cli._mapping(path)


def test_exactly_one_mib_is_allowed_but_one_byte_more_is_rejected(tmp_path):
    maximum = packet_cli._MAX_MAPPING_BYTES
    prefix, suffix = b'{"x":"', b'"}'
    exact = tmp_path/'exact.json'
    exact.write_bytes(prefix + b'a'*(maximum-len(prefix)-len(suffix)) + suffix)
    assert len(exact.read_bytes()) == maximum
    assert packet_cli._mapping(exact)['x'].startswith('a')
    oversized = tmp_path/'oversized.json'
    oversized.write_bytes(exact.read_bytes() + b' ')
    with pytest.raises(ValueError):
        packet_cli._mapping(oversized)


@pytest.mark.parametrize('unsafe_part', ['target', 'parent'])
def test_reparse_target_or_parent_is_rejected_before_open(tmp_path, monkeypatch,
                                                          unsafe_part):
    path = tmp_path/'folder'/'mapping.json'
    path.parent.mkdir()
    path.write_text('{}', encoding='utf-8')
    unsafe = path if unsafe_part == 'target' else path.parent
    original = packet_cli._is_link
    observed = False

    def injected(item, stat_result):
        nonlocal observed
        if Path(item) == unsafe:
            observed = True
            return True
        return original(item, stat_result)

    monkeypatch.setattr(packet_cli, '_is_link', injected)
    monkeypatch.setattr(Path, 'open',
                        lambda *a, **k: pytest.fail('unsafe mapping was opened'))
    with pytest.raises(ValueError):
        packet_cli._mapping(path)
    assert observed


def test_change_during_read_is_detected(tmp_path, monkeypatch):
    path = tmp_path/'mapping.json'
    path.write_text('{"6857":100}', encoding='utf-8')
    original_open = Path.open
    injected = False

    class ChangingReader:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def fileno(self):
            return self.stream.fileno()
        def read(self, *args):
            nonlocal injected
            body = self.stream.read(*args)
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(descriptor, b' ')
            finally:
                os.close(descriptor)
            injected = True
            return body

    def changing_open(self, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        return ChangingReader(stream) if self == path else stream

    monkeypatch.setattr(Path, 'open', changing_open)
    with pytest.raises(ValueError):
        packet_cli._mapping(path)
    assert injected


def test_invalid_mapping_precedes_strategy_execution(tmp_path, monkeypatch):
    invalid = tmp_path/'events.json'
    invalid.write_text('{"x":1,"x":2}', encoding='utf-8')
    monkeypatch.setattr(packet_cli, '_run_signals_in_connection',
                        lambda *a, **k: pytest.fail('strategy ran before mapping validation'))
    with pytest.raises(ValueError):
        packet_cli.generate(tmp_path/'missing-market', 'margin_bucket_long',
                            date(2026, 9, 4), events_path=invalid)


def test_default_lot_and_unknown_events_remain_compatible(tmp_path, monkeypatch):
    home = tmp_path/'market'
    init(home)
    as_of = date(2026, 9, 4)
    with connect(home) as database:
        database.execute('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)',
                         ['6857', as_of, 1000.])
        database.execute('INSERT INTO calendar VALUES(?,true)', [date(2026, 9, 7)])
    monkeypatch.setattr(packet_cli, '_run_signals_in_connection', lambda *a: [{
        'code': '6857', 'side': 'BUY', 'strategy': 'margin_bucket_long',
        'strategy_version': 'v1', 'reason': 'fixture'}])
    proposal = packet_cli.generate(home, 'margin_bucket_long', as_of)[0]
    assert proposal['lot_size'] == 100
    assert proposal['qty'] == 200
    assert proposal['events']['margin_regulated'] == 'UNKNOWN'
