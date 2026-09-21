"""Safe two-file export of completed backtest summaries."""
import hashlib
import json
import os
from pathlib import Path

import pytest

import aitrader.backtest_exports as exports


def make_source(path, version=1):
    path.mkdir(parents=True)
    (path/'summary.json').write_text(
        json.dumps({'version': version, 'label': f'世代-{version}'}, ensure_ascii=False),
        encoding='utf-8')
    (path/'report.html').write_text(
        f'<html>generation {version}</html>', encoding='utf-8')
    return path


def pair(path):
    return {name: (path/name).read_bytes() for name in exports.FILES}


def lock(path):
    return path.parent/f'.{path.name}.publish.lock'


def test_normal_export_to_shared_target_preserves_unrelated_csv(tmp_path):
    source = make_source(tmp_path/'runtime'/'source')
    target = tmp_path/'Dropbox'/'shared'
    target.mkdir(parents=True)
    unrelated = target/'trades.csv'
    unrelated.write_bytes(b'private detailed trades')
    result = exports.export_summary_report(source, target)
    assert pair(target) == pair(source)
    assert result == {'summary': target/'summary.json', 'report': target/'report.html'}
    assert unrelated.read_bytes() == b'private detailed trades'
    assert not lock(source).exists() and not lock(target).exists()
    assert not any('.export-stage.' in item.name or '.export-backup.' in item.name
                   for item in target.parent.iterdir())


def test_same_source_and_target_is_rejected_without_changes(tmp_path):
    source = make_source(tmp_path/'source')
    before = pair(source)
    with pytest.raises(ValueError):
        exports.export_summary_report(source, source)
    assert pair(source) == before
    assert not lock(source).exists()


@pytest.mark.parametrize('existing', [False, True], ids=['new-target', 'existing-target'])
def test_copy_failure_keeps_target_unchanged_until_both_files_complete(
        tmp_path, monkeypatch, existing):
    source = make_source(tmp_path/'source')
    target = tmp_path/'target'
    old = None
    if existing:
        make_source(target, 0)
        old = pair(target)
    original = Path.open
    injected = False

    def fail_stage_report(self, mode='r', *args, **kwargs):
        nonlocal injected
        if '.export-stage.' in self.parent.name and self.name == 'report.html' and 'x' in mode:
            injected = True
            raise OSError('copy stage failed')
        return original(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', fail_stage_report)
    with pytest.raises(OSError, match='copy stage failed'):
        exports.export_summary_report(source, target)
    assert injected
    if existing:
        assert pair(target) == old
    else:
        assert not target.exists()
    assert not lock(source).exists() and not lock(target).exists()


@pytest.mark.parametrize('publish_index', [0, 1])
def test_publish_failure_restores_old_pair(tmp_path, monkeypatch, publish_index):
    source = make_source(tmp_path/'source', 2)
    target = make_source(tmp_path/'target', 1)
    old = pair(target)
    original = exports.os.replace
    count = 0

    def fail_publish(source_path, target_path):
        nonlocal count
        source_path, target_path = Path(source_path), Path(target_path)
        if '.export-stage.' in source_path.parent.name and target_path.parent == target:
            if count == publish_index:
                raise OSError('export publish failed')
            count += 1
        return original(source_path, target_path)

    monkeypatch.setattr(exports.os, 'replace', fail_publish)
    with pytest.raises(OSError, match='export publish failed'):
        exports.export_summary_report(source, target)
    assert pair(target) == old
    assert not lock(source).exists() and not lock(target).exists()


def test_restore_failure_keeps_target_lock_and_backup_but_releases_source_lock(
        tmp_path, monkeypatch):
    source = make_source(tmp_path/'source', 2)
    target = make_source(tmp_path/'target', 1)
    original = exports.os.replace
    published = False
    primary_failed = False

    def fail_publish_restore(source_path, target_path):
        nonlocal published, primary_failed
        source_path, target_path = Path(source_path), Path(target_path)
        if '.export-stage.' in source_path.parent.name and target_path.parent == target:
            if published:
                primary_failed = True
                raise OSError('primary export failure')
            published = True
        if (primary_failed and '.export-backup.' in source_path.parent.name
                and target_path.parent == target):
            raise OSError('restore failure')
        return original(source_path, target_path)

    monkeypatch.setattr(exports.os, 'replace', fail_publish_restore)
    with pytest.raises(exports.ExportRestoreError) as caught:
        exports.export_summary_report(source, target)
    assert isinstance(caught.value.__cause__, OSError)
    assert lock(target).is_file()
    assert not lock(source).exists()
    assert any('.export-backup.' in item.name for item in target.parent.iterdir())


def test_source_change_during_copy_is_detected_and_target_unchanged(
        tmp_path, monkeypatch):
    source = make_source(tmp_path/'source', 2)
    target = make_source(tmp_path/'target', 1)
    old = pair(target)
    original = exports._snapshot
    source_calls = 0

    def changing(folder):
        nonlocal source_calls
        result = original(folder)
        if Path(folder) == source:
            source_calls += 1
            if source_calls == 1:
                (source/'summary.json').write_text('{"version":3}', encoding='utf-8')
        return result

    monkeypatch.setattr(exports, '_snapshot', changing)
    with pytest.raises(ValueError, match='変化'):
        exports.export_summary_report(source, target)
    assert pair(target) == old
    assert not lock(source).exists() and not lock(target).exists()


@pytest.mark.parametrize('unsafe_kind', [
    'source-parent', 'source-folder', 'source-file',
    'target-parent', 'target-folder', 'target-file',
])
def test_raw_reparse_paths_are_rejected_before_copy(
        tmp_path, monkeypatch, unsafe_kind):
    source = make_source(tmp_path/'source-parent'/'source')
    target = make_source(tmp_path/'target-parent'/'target', 0)
    unsafe = {
        'source-parent': source.parent,
        'source-folder': source,
        'source-file': source/'summary.json',
        'target-parent': target.parent,
        'target-folder': target,
        'target-file': target/'summary.json',
    }[unsafe_kind]
    original = exports.os.path.isjunction
    observed = False

    def junction(path):
        nonlocal observed
        if Path(path) == unsafe:
            observed = True
            return True
        return original(path)

    monkeypatch.setattr(exports.os.path, 'isjunction', junction)
    monkeypatch.setattr(exports.tempfile, 'mkdtemp',
                        lambda *a, **k: pytest.fail('unsafe path reached copy stage'))
    with pytest.raises(ValueError):
        exports.export_summary_report(source, target)
    assert observed


@pytest.mark.parametrize('which', ['source', 'target'])
def test_existing_lock_is_preserved_and_only_other_self_lock_is_released(
        tmp_path, which):
    source = make_source(tmp_path/'a-source')
    target = make_source(tmp_path/'z-target', 0)
    blocked = source if which == 'source' else target
    blocked_lock = lock(blocked)
    blocked_lock.write_bytes(b'existing owner')
    with pytest.raises(ValueError, match='実行中|要確認'):
        exports.export_summary_report(source, target)
    assert blocked_lock.read_bytes() == b'existing owner'
    other = target if which == 'source' else source
    assert not lock(other).exists()


def test_source_shared_path_is_rejected_but_target_shared_path_is_allowed(tmp_path):
    forbidden = tmp_path/'Dropbox'/'source'
    forbidden.mkdir(parents=True)
    (forbidden/'summary.json').write_text('{}', encoding='utf-8')
    (forbidden/'report.html').write_text('x', encoding='utf-8')
    with pytest.raises(ValueError, match='Dropbox'):
        exports.export_summary_report(forbidden, tmp_path/'target')
    assert not (tmp_path/'target').exists()

    source = make_source(tmp_path/'runtime-source')
    shared = tmp_path/'Dropbox'/'allowed-target'
    exports.export_summary_report(source, shared)
    assert pair(shared) == pair(source)
