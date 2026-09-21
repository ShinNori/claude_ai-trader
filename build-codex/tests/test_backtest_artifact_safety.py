"""Complete four-file publication for backtest research artifacts."""
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

import aitrader.backtest_artifacts as artifacts


def frames(version):
    trades = pd.DataFrame([{'version': version, 'pnl': version * 10}])
    equity = pd.DataFrame([{'version': version, 'equity': 100 + version}])
    summary = {'version': version, 'label': f'世代-{version}'}
    return trades, equity, summary


@pytest.fixture(autouse=True)
def deterministic_render(monkeypatch):
    def render(folder):
        summary = json.loads((Path(folder)/'summary.json').read_text(encoding='utf-8'))
        (Path(folder)/'report.html').write_text(
            f"<html>generation {summary['version']}</html>", encoding='utf-8')
    monkeypatch.setattr(artifacts, 'render', render)


def publish(folder, version):
    return artifacts.publish_backtest_artifacts(folder, *frames(version))


def hashes(folder):
    return {name: hashlib.sha256((folder/name).read_bytes()).hexdigest()
            for name in artifacts.FILES}


def residuals(folder):
    parent = folder.parent
    prefixes = (f'.{folder.name}.stage.', f'.{folder.name}.backup.')
    return [path for path in parent.iterdir()
            if path.name.startswith(prefixes)]


def test_normal_repeat_replaces_complete_set_and_preserves_unrelated_file(tmp_path):
    folder = tmp_path/'fixture_v1'
    publish(folder, 1)
    old = hashes(folder)
    unrelated = folder/'notes.txt'
    unrelated.write_bytes(b'user research note')
    publish(folder, 2)
    assert all(hashes(folder)[name] != old[name] for name in artifacts.FILES)
    assert unrelated.read_bytes() == b'user research note'
    assert not (tmp_path/f'.{folder.name}.publish.lock').exists()
    assert residuals(folder) == []


@pytest.mark.parametrize('stage', ['trades', 'equity', 'summary', 'render'])
@pytest.mark.parametrize('existing', [False, True], ids=['new-folder', 'existing-folder'])
def test_generation_failure_never_publishes_partial_final_set(
        tmp_path, monkeypatch, stage, existing):
    folder = tmp_path/f'{stage}-{existing}'
    sentinel = tmp_path/'unrelated.txt'
    sentinel.write_bytes(b'preserve')
    old = None
    if existing:
        publish(folder, 1)
        old = hashes(folder)
    if stage in ('trades', 'equity'):
        original = pd.DataFrame.to_csv
        wanted = stage+'.csv'
        def fail_csv(self, path, *args, **kwargs):
            if Path(path).name == wanted:
                raise OSError(f'{stage} generation failed')
            return original(self, path, *args, **kwargs)
        monkeypatch.setattr(pd.DataFrame, 'to_csv', fail_csv)
    elif stage == 'summary':
        original = Path.write_text
        def fail_summary(self, *args, **kwargs):
            if self.name == 'summary.json':
                raise OSError('summary generation failed')
            return original(self, *args, **kwargs)
        monkeypatch.setattr(Path, 'write_text', fail_summary)
    else:
        monkeypatch.setattr(artifacts, 'render',
                            lambda folder: (_ for _ in ()).throw(
                                OSError('render generation failed')))
    with pytest.raises(OSError, match='failed'):
        publish(folder, 2)
    if existing:
        assert hashes(folder) == old
    else:
        assert not folder.exists()
    assert sentinel.read_bytes() == b'preserve'


@pytest.mark.parametrize('publish_index', range(4))
def test_publish_replace_failure_restores_all_four_old_files(
        tmp_path, monkeypatch, publish_index):
    folder = tmp_path/f'publish-{publish_index}'
    publish(folder, 1)
    old = hashes(folder)
    original = artifacts.os.replace
    published = 0
    injected = False

    def fail_one(source, target):
        nonlocal published, injected
        source, target = Path(source), Path(target)
        if source.parent.name.startswith(f'.{folder.name}.stage.') and target.parent == folder:
            if published == publish_index:
                injected = True
                raise OSError('publish replace failed')
            published += 1
        return original(source, target)

    monkeypatch.setattr(artifacts.os, 'replace', fail_one)
    with pytest.raises(OSError, match='publish replace failed'):
        publish(folder, 2)
    assert injected
    assert hashes(folder) == old
    assert not (tmp_path/f'.{folder.name}.publish.lock').exists()


@pytest.mark.parametrize('backup_index', range(4))
def test_backup_replace_failure_restores_all_four_old_files(
        tmp_path, monkeypatch, backup_index):
    folder = tmp_path/f'backup-{backup_index}'
    publish(folder, 1)
    old = hashes(folder)
    original = artifacts.os.replace
    backed_up = 0
    injected = False

    def fail_one(source, target):
        nonlocal backed_up, injected
        source, target = Path(source), Path(target)
        if source.parent == folder and target.parent.name.startswith(
                f'.{folder.name}.backup.'):
            if backed_up == backup_index:
                injected = True
                raise OSError('backup replace failed')
            backed_up += 1
        return original(source, target)

    monkeypatch.setattr(artifacts.os, 'replace', fail_one)
    with pytest.raises(OSError, match='backup replace failed'):
        publish(folder, 2)
    assert injected
    assert hashes(folder) == old
    assert not (tmp_path/f'.{folder.name}.publish.lock').exists()


def test_publish_failure_restores_preexisting_missing_artifact_state(
        tmp_path, monkeypatch):
    folder = tmp_path/'partial-old'
    publish(folder, 1)
    (folder/'trades.csv').unlink()
    old = {name: ((folder/name).read_bytes() if (folder/name).exists() else None)
           for name in artifacts.FILES}
    original = artifacts.os.replace
    staged = 0

    def fail_second_publish(source, target):
        nonlocal staged
        source, target = Path(source), Path(target)
        if source.parent.name.startswith(f'.{folder.name}.stage.') and target.parent == folder:
            if staged == 1:
                raise OSError('partial publish failed')
            staged += 1
        return original(source, target)

    monkeypatch.setattr(artifacts.os, 'replace', fail_second_publish)
    with pytest.raises(OSError, match='partial publish failed'):
        publish(folder, 2)
    assert {name: ((folder/name).read_bytes() if (folder/name).exists() else None)
            for name in artifacts.FILES} == old


def test_source_disappearing_after_fingerprint_read_is_value_error(
        tmp_path, monkeypatch):
    folder = tmp_path/'vanishing-source'
    publish(folder, 1)
    target = folder/'summary.json'
    original_open = Path.open
    injected = False

    class VanishingReader:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            nonlocal injected
            result = self.stream.__exit__(*args)
            target.unlink()
            injected = True
            return result
        def fileno(self):
            return self.stream.fileno()
        def read(self, *args):
            return self.stream.read(*args)

    def vanishing_open(self, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        return VanishingReader(stream) if self == target else stream

    monkeypatch.setattr(Path, 'open', vanishing_open)
    with pytest.raises(ValueError):
        publish(folder, 2)
    assert injected


def test_restore_failure_keeps_backup_and_lock_and_blocks_next_run(
        tmp_path, monkeypatch):
    folder = tmp_path/'restore-failure'
    publish(folder, 1)
    original = artifacts.os.replace
    staged = 0
    publish_failed = False
    restore_failed = False

    def fail_publish_and_restore(source, target):
        nonlocal staged, publish_failed, restore_failed
        source, target = Path(source), Path(target)
        if source.parent.name.startswith(f'.{folder.name}.stage.') and target.parent == folder:
            if staged == 1:
                publish_failed = True
                raise OSError('primary publish failure')
            staged += 1
        if (publish_failed and source.parent.name.startswith(f'.{folder.name}.backup.')
                and target.parent == folder and not restore_failed):
            restore_failed = True
            raise OSError('restore failure')
        return original(source, target)

    monkeypatch.setattr(artifacts.os, 'replace', fail_publish_and_restore)
    with pytest.raises(artifacts.ArtifactRestoreError) as caught:
        publish(folder, 2)
    assert publish_failed and restore_failed
    assert isinstance(caught.value.__cause__, OSError)
    lock = tmp_path/f'.{folder.name}.publish.lock'
    assert lock.is_file()
    assert any(path.name.startswith(f'.{folder.name}.backup.')
               for path in tmp_path.iterdir())
    monkeypatch.setattr(artifacts.os, 'replace', original)
    with pytest.raises(ValueError, match='実行中|要確認'):
        publish(folder, 3)
    assert lock.is_file()


@pytest.mark.parametrize('unsafe_part', ['parent', 'folder', 'target'])
def test_reparse_path_is_rejected_before_staging(
        tmp_path, monkeypatch, unsafe_part):
    folder = tmp_path/'safe-parent'/'fixture'
    folder.parent.mkdir()
    if unsafe_part in ('folder', 'target'):
        publish(folder, 1)
    unsafe = {'parent': folder.parent,
              'folder': folder,
              'target': folder/'summary.json'}[unsafe_part]
    original = artifacts.os.path.isjunction
    observed = False
    def junction(path):
        nonlocal observed
        if Path(path) == unsafe:
            observed = True
            return True
        return original(path)
    monkeypatch.setattr(artifacts.os.path, 'isjunction', junction)
    monkeypatch.setattr(artifacts.tempfile, 'mkdtemp',
                        lambda *a, **k: pytest.fail('unsafe path reached staging'))
    with pytest.raises(ValueError):
        publish(folder, 2)
    assert observed


def test_forbidden_cloud_path_is_rejected_before_staging(tmp_path, monkeypatch):
    dropbox = tmp_path/'Dropbox'/'fixture'
    monkeypatch.setattr(artifacts.tempfile, 'mkdtemp',
                        lambda *a, **k: pytest.fail('Dropbox reached staging'))
    with pytest.raises(ValueError, match='Dropbox'):
        publish(dropbox, 1)
    assert not dropbox.exists()


def test_stale_lock_is_never_automatically_overwritten(tmp_path):
    folder = tmp_path/'locked'
    publish(folder, 1)
    old = hashes(folder)
    lock = tmp_path/f'.{folder.name}.publish.lock'
    lock.write_bytes(b'stale or active owner')
    with pytest.raises(ValueError, match='実行中|要確認'):
        publish(folder, 2)
    assert hashes(folder) == old
    assert lock.read_bytes() == b'stale or active owner'
