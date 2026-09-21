"""Cleanup failures after a complete four-file publication stay fail-closed."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import aitrader.backtest_artifacts as artifacts


def _frames(version):
    return (pd.DataFrame([{'version': version}]),
            pd.DataFrame([{'version': version, 'equity': 100 + version}]),
            {'version': version})


@pytest.fixture(autouse=True)
def _render(monkeypatch):
    def render(folder):
        version = json.loads((Path(folder) / 'summary.json').read_text())['version']
        (Path(folder) / 'report.html').write_text(f'<p>{version}</p>', encoding='utf-8')
    monkeypatch.setattr(artifacts, 'render', render)


def _publish(folder, version):
    artifacts.publish_backtest_artifacts(folder, *_frames(version))


def _hashes(folder):
    return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
            for name in artifacts.FILES}


def _prepare(tmp_path, name):
    reference = tmp_path / ('reference-' + name)
    folder = tmp_path / name
    _publish(reference, 2)
    expected = _hashes(reference)
    _publish(folder, 1)
    return folder, expected


@pytest.mark.parametrize('index', range(4))
def test_backup_file_cleanup_failure_keeps_new_set_and_lock(
        tmp_path, monkeypatch, index):
    folder, expected = _prepare(tmp_path, f'unlink-{index}')
    original = Path.unlink
    seen = 0

    def fail_unlink(self, *args, **kwargs):
        nonlocal seen
        if self.parent.name.startswith(f'.{folder.name}.backup.'):
            if seen == index:
                raise OSError('SECRET cleanup detail')
            seen += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_unlink)
    with pytest.raises(artifacts.ArtifactCleanupError) as caught:
        _publish(folder, 2)
    assert str(caught.value) == (
        'バックテスト成果は公開済みですが後片付けが未完了です。手動確認が必要です')
    assert caught.value.__cause__ is None
    assert _hashes(folder) == expected
    lock = tmp_path / f'.{folder.name}.publish.lock'
    assert lock.is_file()
    remaining = [path.name for path in tmp_path.iterdir()
                 if path.name.startswith(f'.{folder.name}.backup.')]
    assert len(remaining) == 1
    with pytest.raises(ValueError, match='実行中|要確認'):
        _publish(folder, 3)


@pytest.mark.parametrize('kind', ('backup', 'stage'))
def test_directory_cleanup_failure_keeps_new_set_and_lock(
        tmp_path, monkeypatch, kind):
    folder, expected = _prepare(tmp_path, f'rmdir-{kind}')
    original = Path.rmdir
    injected = False

    def fail_rmdir(self):
        nonlocal injected
        if not injected and self.name.startswith(f'.{folder.name}.{kind}.'):
            injected = True
            raise OSError('SECRET directory cleanup detail')
        return original(self)

    monkeypatch.setattr(Path, 'rmdir', fail_rmdir)
    with pytest.raises(artifacts.ArtifactCleanupError) as caught:
        _publish(folder, 2)
    assert injected
    assert 'SECRET' not in str(caught.value)
    assert caught.value.__cause__ is None
    assert _hashes(folder) == expected
    lock = tmp_path / f'.{folder.name}.publish.lock'
    assert lock.is_file()
    with pytest.raises(ValueError, match='実行中|要確認'):
        _publish(folder, 3)

