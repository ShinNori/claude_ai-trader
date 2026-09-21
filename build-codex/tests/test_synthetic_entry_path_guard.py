"""Reject raw unsafe parents before synthetic initialization has side effects."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from aitrader import daily_rehearsal as daily
from aitrader import synthetic_pipeline as pipeline
from aitrader.runner import RunError


@pytest.mark.parametrize('entry', ['daily', 'managed_daily', 'pipeline'])
@pytest.mark.parametrize('unsafe', ['reparse', 'file', 'dropbox'])
def test_unsafe_raw_parent_never_reaches_initialization(tmp_path, monkeypatch, entry, unsafe):
    parent = tmp_path / ('Dropbox-fixture' if unsafe == 'dropbox' else 'parent')
    parent.mkdir()
    home = parent / 'new-home'
    original = Path.lstat

    def observed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == parent and unsafe in ('reparse', 'file'):
            return SimpleNamespace(st_mode=(0o100644 if unsafe == 'file' else value.st_mode),
                                   st_file_attributes=0x400 if unsafe == 'reparse' else 0)
        return value

    def forbidden(*args, **kwargs):
        pytest.fail('initialization must not run')

    monkeypatch.setattr(Path, 'lstat', observed)
    monkeypatch.setattr(daily, 'initialize_mock', forbidden)
    monkeypatch.setattr(daily, 'initialize_managed_mock', forbidden)
    monkeypatch.setattr(pipeline, 'initialize_mock', forbidden)
    with pytest.raises(RunError):
        if entry == 'pipeline':
            pipeline.run_synthetic_pipeline(home)
        else:
            daily.run_daily_rehearsal(home, managed_stop=entry == 'managed_daily')
    assert not home.exists()


@pytest.mark.parametrize('entry', ['daily', 'managed_daily', 'pipeline'])
def test_safe_relative_home_reaches_initialization(tmp_path, monkeypatch, entry):
    monkeypatch.chdir(tmp_path)
    seen = []

    class Reached(Exception):
        pass

    def initialize(home, *args, **kwargs):
        seen.append(home)
        raise Reached()

    monkeypatch.setattr(daily, 'initialize_mock', initialize)
    monkeypatch.setattr(daily, 'initialize_managed_mock', initialize)
    monkeypatch.setattr(pipeline, 'initialize_mock', initialize)
    with pytest.raises(Reached):
        if entry == 'pipeline':
            pipeline.run_synthetic_pipeline('new-home')
        else:
            daily.run_daily_rehearsal('new-home', managed_stop=entry == 'managed_daily')
    assert seen == [tmp_path / 'new-home']
