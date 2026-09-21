"""Cheap input rejection checks; never generate a synthetic market database."""
import pytest

from test_notification_plan import offline
from aitrader import synthetic_pipeline as pipeline
from aitrader.runner import RunError


def forbidden(*args, **kwargs):
    pytest.fail('invalid input reached initialization or market generation')


@pytest.mark.parametrize('seed', [None, True, False, '42', 42.0, [], {}, -1, -100])
def test_invalid_seed_is_rejected_before_any_creation(tmp_path, monkeypatch, seed):
    home = tmp_path/'invalid'
    monkeypatch.setattr(pipeline, 'initialize_mock', forbidden)
    monkeypatch.setattr(pipeline, 'load_synthetic', forbidden)
    with pytest.raises(RunError):
        pipeline.run_synthetic_pipeline(home, seed=seed)
    assert not home.exists()


@pytest.mark.parametrize('kind', ['empty_directory', 'populated_directory', 'file'])
def test_existing_home_is_rejected_without_mutation(tmp_path, monkeypatch, kind):
    home = tmp_path/'existing'
    if kind == 'file':
        home.write_bytes(b'existing-file')
    else:
        home.mkdir()
        if kind == 'populated_directory':
            (home/'ledger.sqlite').write_bytes(b'not-a-database')
    before = home.read_bytes() if home.is_file() else {p.name: p.read_bytes() for p in home.iterdir()}
    monkeypatch.setattr(pipeline, 'initialize_mock', forbidden)
    monkeypatch.setattr(pipeline, 'load_synthetic', forbidden)
    with pytest.raises(RunError):
        pipeline.run_synthetic_pipeline(home)
    after = home.read_bytes() if home.is_file() else {p.name: p.read_bytes() for p in home.iterdir()}
    assert after == before


@pytest.mark.parametrize('folder', ['Dropbox', 'DROPBOX', 'Ace-1_Dropbox'])
def test_dropbox_destination_is_rejected_before_creation(tmp_path, monkeypatch, folder):
    home = tmp_path/folder/'new-home'
    monkeypatch.setattr(pipeline, 'load_synthetic', forbidden)
    with pytest.raises(RunError):
        pipeline.run_synthetic_pipeline(home)
    assert not (tmp_path/folder).exists()
