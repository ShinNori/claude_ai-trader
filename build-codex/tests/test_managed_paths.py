"""Managed marker and clock paths fail closed before unsafe reads or writes."""
from datetime import timedelta
import os
from pathlib import Path

import pytest

from aitrader import managed_stop
from aitrader.managed_stop import (advance_managed_operation, inspect_managed_stop,
                                   managed_stop_policy)
from aitrader.runner import RunError, initialize_mock
from test_managed_stop import AT, SETTINGS, _initialize


class ReparseStat:
    def __init__(self, original):
        self._original = original
        self.st_file_attributes = getattr(original, 'st_file_attributes', 0) | 0x400

    def __getattr__(self, name):
        return getattr(self._original, name)


def inject_reparse(monkeypatch, target):
    original = Path.lstat
    target = target.absolute()

    def marked(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        return ReparseStat(value) if path.absolute() == target else value

    monkeypatch.setattr(Path, 'lstat', marked)


def test_exact_legacy_marker_still_returns_none(tmp_path):
    home = tmp_path/'legacy'
    initialize_mock(home, 1_000_000, [], AT)
    assert managed_stop_policy(home) is None


def test_normal_managed_marker_and_clock_remain_readable(tmp_path):
    home, _ = _initialize(tmp_path)
    policy = managed_stop_policy(home)
    assert policy['mode'] == 'mock' and policy['version'] == 2
    result = inspect_managed_stop(home, now=AT+timedelta(minutes=1))
    assert result['known'] is True and result['status'] == 'CLEAR'


def test_marker_reparse_is_rejected_before_open(tmp_path, monkeypatch):
    home, _ = _initialize(tmp_path)
    marker = home/'mock-runner.json'
    inject_reparse(monkeypatch, marker)
    opened = []
    original_open = Path.open

    def observed(path, *args, **kwargs):
        opened.append(path.absolute())
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', observed)
    with pytest.raises(RunError, match='marker'):
        managed_stop_policy(home)
    assert marker.absolute() not in opened


def test_clock_reparse_blocks_advance_before_writer(tmp_path, monkeypatch):
    home, _ = _initialize(tmp_path)
    policy = managed_stop_policy(home)
    clock = home/'managed-stop-clock.json'
    inject_reparse(monkeypatch, clock)
    opened = []
    original_open = Path.open

    def observed(path, *args, **kwargs):
        opened.append(path.absolute())
        return original_open(path, *args, **kwargs)

    def forbidden_write(*args, **kwargs):
        pytest.fail('unsafe clock must not be overwritten')

    monkeypatch.setattr(Path, 'open', observed)
    monkeypatch.setattr('aitrader.runner.write_json', forbidden_write)
    with pytest.raises(RunError):
        advance_managed_operation(home, policy, now=AT+timedelta(minutes=1))
    assert clock.absolute() not in opened


def test_existing_stop_file_reparse_makes_policy_invalid_and_status_unknown(
        tmp_path, monkeypatch):
    home, _ = _initialize(tmp_path)
    stop = home/'STOP'
    stop.write_text('stop', encoding='utf-8')
    inject_reparse(monkeypatch, stop)

    with pytest.raises(RunError):
        managed_stop_policy(home)
    status = inspect_managed_stop(home, now=AT+timedelta(minutes=1))
    assert status['status'] == 'UNKNOWN'
    assert status['known'] is False and status['effective_stop'] is True


def test_oversized_marker_is_rejected_without_unbounded_read(tmp_path):
    home = tmp_path/'oversized'
    initialize_mock(home, 1_000_000, [], AT)
    marker = home/'mock-runner.json'
    marker.write_bytes(b'{' + b' ' * (1024 * 1024) + b'}')

    with pytest.raises(RunError, match='marker'):
        managed_stop_policy(home)


def test_marker_replaced_during_read_is_rejected(tmp_path, monkeypatch):
    home, _ = _initialize(tmp_path)
    marker = home/'mock-runner.json'
    original_open = Path.open
    replaced = False

    class ReplacingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, *args):
            nonlocal replaced
            body = self.stream.read(*args)
            observed = marker.stat()
            os.utime(marker, ns=(observed.st_atime_ns,
                                 observed.st_mtime_ns + 1_000_000_000))
            replaced = True
            return body

    def changing_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return ReplacingStream(stream) if path.absolute() == marker.absolute() else stream

    monkeypatch.setattr(Path, 'open', changing_open)
    with pytest.raises(ValueError, match='観測中'):
        managed_stop._read(marker)
    assert replaced


def test_reparse_parent_is_rejected_before_marker_read(tmp_path, monkeypatch):
    home, _ = _initialize(tmp_path)
    inject_reparse(monkeypatch, home)
    marker = home/'mock-runner.json'
    opened = []
    original_open = Path.open

    def observed(path, *args, **kwargs):
        opened.append(path.absolute())
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', observed)
    with pytest.raises(ValueError):
        managed_stop_policy(home)
    assert marker.absolute() not in opened
