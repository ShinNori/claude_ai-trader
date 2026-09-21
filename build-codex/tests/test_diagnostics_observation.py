"""Independent observation guards for read-only runner diagnostics."""
import hashlib
import os
from pathlib import Path

import pytest

from test_runner import setup, DAY
from aitrader.runner_diagnostics import diagnose_mock_run


def _hashes(home):
    return {str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in home.rglob('*') if path.is_file()}


@pytest.fixture
def completed(setup):
    home, execute = setup
    execute()
    return home


def test_normal_observation_is_consistent_and_read_only(completed):
    before = _hashes(completed)
    first = diagnose_mock_run(completed, 'run1', DAY)
    second = diagnose_mock_run(completed, 'run1', DAY)
    assert _hashes(completed) == before
    assert first == second
    assert first['classification'] == 'COMPLETE'
    assert first['snapshot_consistent'] is False
    assert first['observation_unchanged'] is True
    assert first['automatic_resume_allowed'] is False
    assert first['repaired'] is False
    assert first['current_signal'] is False


@pytest.mark.parametrize('database', ['orchestration.sqlite', 'ledger.sqlite'])
@pytest.mark.parametrize('suffix', ['-journal', '.wal', '.shm'])
def test_any_database_sidecar_hides_candidates(completed, database, suffix):
    sidecar = Path(str(completed/database) + suffix)
    sidecar.write_bytes(b'uncommitted')
    report = diagnose_mock_run(completed, 'run1', DAY)
    assert report['classification'] == 'NEEDS_RECONCILIATION'
    assert report['reasons'] == ['LIVE_DATABASE_UNSUPPORTED']
    assert report['candidates'] == []
    assert sidecar.read_bytes() == b'uncommitted'


@pytest.mark.parametrize('relative', [
    'orchestration.sqlite',
    'ledger.sqlite',
])
def test_change_during_observation_hides_candidates(completed, monkeypatch, relative):
    import aitrader.runner_diagnostics as diagnostics
    target = completed / relative
    original = diagnostics._diagnose_mock_run_internal
    injected = False

    def changing(*args, **kwargs):
        nonlocal injected
        result = original(*args, **kwargs)
        target.write_bytes(target.read_bytes() + b' ')
        injected = True
        return result

    monkeypatch.setattr(diagnostics, '_diagnose_mock_run_internal', changing)
    report = diagnostics.diagnose_mock_run(completed, 'run1', DAY)
    assert injected
    assert report['classification'] == 'NEEDS_RECONCILIATION'
    assert report['reasons'] == ['OBSERVATION_CHANGED']
    assert report['candidates'] == []
    assert report['snapshot_consistent'] is False


@pytest.mark.parametrize('relative', [
    'orchestration.sqlite',
    'ledger.sqlite',
])
def test_missing_required_file_is_missing_and_never_recreated(completed, relative):
    target = completed / relative
    target.unlink()
    before = _hashes(completed)
    report = diagnose_mock_run(completed, 'run1', DAY)
    assert report['classification'] == 'MISSING'
    assert report['candidates'] == []
    assert not target.exists()
    assert _hashes(completed) == before


def test_missing_result_is_conflict_and_never_recreated(completed):
    target = completed/f'runs/{DAY}/run1/result.json'
    target.unlink()
    before = _hashes(completed)
    report = diagnose_mock_run(completed, 'run1', DAY)
    assert report['classification'] == 'CONFLICT'
    assert report['reasons'] == ['ORIGINAL_OR_ARTIFACT_MISMATCH']
    assert report['candidates'] == []
    assert not target.exists()
    assert _hashes(completed) == before


def test_linked_marker_is_rejected_before_internal_read(completed, monkeypatch):
    marker = completed/'mock-runner.json'
    real = completed/'marker-target.json'
    marker.replace(real)
    try:
        os.symlink(real, marker)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'symlink unavailable: {exc}')
    import aitrader.runner_diagnostics as diagnostics
    monkeypatch.setattr(diagnostics, '_diagnose_mock_run_internal',
                        lambda *a, **k: pytest.fail('unsafe marker reached internal read'))
    report = diagnostics.diagnose_mock_run(completed, 'run1', DAY)
    assert report['classification'] == 'NEEDS_RECONCILIATION'
    assert report['reasons'] == ['OBSERVATION_UNSAFE']
    assert report['candidates'] == []


def test_linked_home_parent_is_rejected_before_observation(completed, tmp_path, monkeypatch):
    linked_parent = tmp_path/'linked-parent'
    try:
        os.symlink(completed.parent, linked_parent, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'symlink unavailable: {exc}')
    linked_home = linked_parent/completed.name
    import aitrader.runner_diagnostics as diagnostics
    monkeypatch.setattr(diagnostics, '_fingerprint',
                        lambda *a, **k: pytest.fail('unsafe parent reached file read'))
    with pytest.raises(Exception):
        diagnostics.diagnose_mock_run(linked_home, 'run1', DAY)


@pytest.mark.parametrize('unsafe_part', ['marker', 'parent'])
def test_injected_reparse_is_rejected_before_read(completed, monkeypatch, unsafe_part):
    import aitrader.runner_diagnostics as diagnostics
    marker = completed/'mock-runner.json'
    parent = completed.parent
    original = diagnostics._link_or_reparse
    observed = False

    def unsafe(path):
        nonlocal observed
        if Path(path) == (marker if unsafe_part == 'marker' else parent):
            observed = True
            return True
        return original(path)

    monkeypatch.setattr(diagnostics, '_link_or_reparse', unsafe)
    monkeypatch.setattr(diagnostics, '_diagnose_mock_run_internal',
                        lambda *a, **k: pytest.fail('unsafe path reached internal read'))
    if unsafe_part == 'parent':
        with pytest.raises(Exception):
            diagnostics.diagnose_mock_run(completed, 'run1', DAY)
    else:
        report = diagnostics.diagnose_mock_run(completed, 'run1', DAY)
        assert report['classification'] == 'NEEDS_RECONCILIATION'
        assert report['candidates'] == []
    assert observed
