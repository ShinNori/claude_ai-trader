"""Safely copy the summary/report pair from a completed backtest folder.

Locks coordinate cooperating writers. Publication is not atomic for concurrent
readers and no fsync or power-loss guarantee is made.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path


FILES = ('summary.json', 'report.html')
_REPARSE_POINT = 0x400
_MAX_SUMMARY_BYTES = 8 * 1024 * 1024


class ExportRestoreError(RuntimeError):
    """The export failed and its exact previous two-file state was not restored."""


class ExportCleanupError(RuntimeError):
    """New files were published, but cleanup needs manual inspection."""


def _observe(path, expected=None):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError('バックテストexport pathを安全に確認できません') from None
    unsafe = (stat.S_ISLNK(info.st_mode)
              or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))
              or bool(getattr(info, 'st_file_attributes', 0) & _REPARSE_POINT))
    valid = (expected is None
             or (expected == 'directory' and stat.S_ISDIR(info.st_mode))
             or (expected == 'file' and stat.S_ISREG(info.st_mode)))
    if unsafe or not valid:
        raise ValueError('バックテストexport pathを安全に確認できません')
    return info


def _guard(folder, *, files_required):
    for item in reversed((folder.parent, *folder.parent.parents)):
        _observe(item, 'directory')
    folder_info = _observe(folder, 'directory')
    if files_required and folder_info is None:
        raise ValueError('バックテストexport元を確認できません')
    for name in FILES:
        info = _observe(folder/name, 'file')
        if files_required and info is None:
            raise ValueError('バックテストexport元を確認できません')


def _fingerprint(path):
    info = _observe(path, 'file')
    if info is None:
        return None
    identity = lambda value: (value.st_dev, value.st_ino, value.st_mode)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if identity(info) != identity(opened) or not stat.S_ISREG(opened.st_mode):
                raise ValueError('バックテストexport対象が変化しました')
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
    except OSError:
        raise ValueError('バックテストexport対象を読み取れません') from None
    after = _observe(path, 'file')
    if (after is None or identity(info) != identity(after) or size != info.st_size
            or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns)):
        raise ValueError('バックテストexport対象が変化しました')
    return size, digest.hexdigest()


def _snapshot(folder):
    return {name: _fingerprint(folder/name) for name in FILES}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('バックテストsummaryを確認できません')
        result[key] = value
    return result


def export_summary_report(source_folder, target_folder):
    """Publish a byte-matched two-file copy; no cross-file generation receipt is proved."""
    try:
        source = Path(source_folder).absolute()
        target = Path(target_folder).absolute()
    except (TypeError, ValueError, OSError):
        raise ValueError('バックテストexport pathを安全に確認できません') from None
    if source == target:
        raise ValueError('export元と先には異なるfolderが必要です')
    if any('dropbox' in part.lower() for part in source.parts):
        raise ValueError('export元はDropbox外の実行結果に限定します')
    _guard(source, files_required=True)
    _guard(target, files_required=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    _guard(target, files_required=False)

    lock_paths = sorted(
        (source.parent/('.'+source.name+'.publish.lock'),
         target.parent/('.'+target.name+'.publish.lock')),
        key=lambda item: os.path.normcase(str(item)))
    acquired = []
    keep = set()
    try:
        for lock in lock_paths:
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise ValueError('export対象の公開処理が実行中または要確認です') from None
            os.close(descriptor)
            acquired.append(lock)

        _guard(source, files_required=True)
        _guard(target, files_required=False)
        source_before = _snapshot(source)
        if source_before['summary.json'][0] > _MAX_SUMMARY_BYTES:
            raise ValueError('バックテストsummaryが大きすぎます')
        target_before = (_snapshot(target) if target.exists()
                         else {name: None for name in FILES})
        stage = Path(tempfile.mkdtemp(prefix='.'+target.name+'.export-stage.', dir=target.parent))
        backup = Path(tempfile.mkdtemp(prefix='.'+target.name+'.export-backup.', dir=target.parent))
        _observe(stage, 'directory')
        _observe(backup, 'directory')
        for name in FILES:
            with (source/name).open('rb') as incoming, (stage/name).open('xb') as outgoing:
                while True:
                    chunk = incoming.read(1024 * 1024)
                    if not chunk:
                        break
                    outgoing.write(chunk)
        summary = json.loads((stage/'summary.json').read_text(encoding='utf-8'),
                             object_pairs_hook=_unique)
        try:
            json.dumps(summary, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError('バックテストsummaryを確認できません') from None
        report_fingerprint = _fingerprint(stage/'report.html')
        if (not isinstance(summary, dict) or report_fingerprint is None
                or report_fingerprint[0] <= 0):
            raise ValueError('バックテストexport内容を確認できません')
        if _snapshot(stage) != source_before or _snapshot(source) != source_before:
            raise ValueError('export元が観測中に変化しました')
        _guard(target, files_required=False)
        current = _snapshot(target) if target.exists() else {name: None for name in FILES}
        if current != target_before:
            raise ValueError('export先が観測中に変化しました')
        created_target = not target.exists()
        target.mkdir(parents=False, exist_ok=True)
        published = []
        try:
            for name in FILES:
                if target_before[name] is not None:
                    os.replace(target/name, backup/name)
            for name in FILES:
                os.replace(stage/name, target/name)
                published.append(name)
        except BaseException as publish_error:
            restore_failed = False
            for name in reversed(FILES):
                try:
                    if (backup/name).exists():
                        os.replace(backup/name, target/name)
                    elif target_before[name] is None and name in published:
                        (target/name).unlink()
                except BaseException:
                    restore_failed = True
            try:
                restored = _snapshot(target)
            except BaseException:
                restored = None
                restore_failed = True
            if restored != target_before:
                restore_failed = True
            if restore_failed:
                target_lock = target.parent/('.'+target.name+'.publish.lock')
                keep.add(target_lock)
                raise ExportRestoreError(
                    'バックテストexportの公開と復元に失敗しました') from publish_error
            if created_target and not any(target.iterdir()):
                target.rmdir()
            raise
        # Publication has completed. Cleanup may already have removed part of
        # the old generation, so a cleanup failure must not claim rollback.
        try:
            for name in FILES:
                old = backup/name
                if _observe(old, 'file') is not None:
                    old.unlink()
            backup.rmdir()
            stage.rmdir()
        except BaseException:
            keep.add(target.parent/('.'+target.name+'.publish.lock'))
            raise ExportCleanupError(
                'バックテストexportは公開済みですが後片付けが未完了です。手動確認が必要です') from None
        return {'summary': target/'summary.json', 'report': target/'report.html'}
    finally:
        for lock in reversed(acquired):
            if lock not in keep:
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
