"""Transactional-style publication for the four phase-1 result artifacts.

Cooperating writers are serialized. Publication is not an atomic snapshot for
concurrent readers and no fsync/power-loss guarantee is made.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from .report import render


FILES = ('trades.csv', 'equity.csv', 'summary.json', 'report.html')
_REPARSE_POINT = 0x400


class ArtifactRestoreError(RuntimeError):
    """Publication failed and the exact prior four-file state was not restored."""


class ArtifactCleanupError(RuntimeError):
    """New files were published, but cleanup needs manual inspection."""


def _observe(path, expected=None):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    unsafe = (stat.S_ISLNK(info.st_mode)
              or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))
              or bool(getattr(info, 'st_file_attributes', 0) & _REPARSE_POINT))
    valid = (expected is None
             or (expected == 'directory' and stat.S_ISDIR(info.st_mode))
             or (expected == 'file' and stat.S_ISREG(info.st_mode)))
    if unsafe or not valid:
        raise ValueError('バックテスト成果の保存先を安全に確認できません')
    return info


def _guard(folder):
    for parent in reversed((folder.parent, *folder.parent.parents)):
        _observe(parent, 'directory')
    _observe(folder, 'directory')
    for name in FILES:
        _observe(folder/name, 'file')


def _fingerprint(path):
    info = _observe(path, 'file')
    if info is None:
        return None
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        identity = lambda value: (value.st_dev, value.st_ino, value.st_mode)
        if identity(info) != identity(opened) or not stat.S_ISREG(opened.st_mode):
            raise ValueError('バックテスト成果が観測中に変化しました')
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    after = _observe(path, 'file')
    if (after is None or identity(info) != identity(after) or size != info.st_size
            or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns)):
        raise ValueError('バックテスト成果が観測中に変化しました')
    return size, digest.hexdigest()


def _snapshot(folder):
    return {name: _fingerprint(folder/name) for name in FILES}


def publish_backtest_artifacts(folder, trades, equity, summary):
    """Stage and publish one complete result set while retaining unrelated files."""
    try:
        folder = Path(folder).absolute()
    except (TypeError, ValueError, OSError):
        raise ValueError('バックテスト成果の保存先を安全に確認できません') from None
    if any('dropbox' in part.lower() for part in folder.parts):
        raise ValueError('バックテスト成果はDropbox外に保存してください')
    _guard(folder)
    folder.parent.mkdir(parents=True, exist_ok=True)
    _guard(folder)

    lock = folder.parent/('.'+folder.name+'.publish.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('バックテスト成果の公開処理が既に実行中または要確認です') from None
    os.close(descriptor)
    keep_lock = False
    try:
        _guard(folder)
        original = _snapshot(folder) if folder.exists() else {name: None for name in FILES}
        stage = Path(tempfile.mkdtemp(prefix='.'+folder.name+'.stage.', dir=folder.parent))
        backup = Path(tempfile.mkdtemp(prefix='.'+folder.name+'.backup.', dir=folder.parent))
        _observe(stage, 'directory')
        _observe(backup, 'directory')
        trades.to_csv(stage/'trades.csv', index=False)
        equity.to_csv(stage/'equity.csv', index=False)
        (stage/'summary.json').write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        render(stage)
        for name in FILES:
            if _fingerprint(stage/name) is None:
                raise ValueError('バックテスト成果が完成しませんでした')
        if json.loads((stage/'summary.json').read_text(encoding='utf-8')) != summary:
            raise ValueError('バックテストsummaryを確認できません')
        _guard(folder)
        current = _snapshot(folder) if folder.exists() else {name: None for name in FILES}
        if current != original:
            raise ValueError('保存済みバックテスト成果が変化しました')
        created_folder = not folder.exists()
        folder.mkdir(parents=False, exist_ok=True)
        backed_up = []
        published = []
        try:
            for name in FILES:
                target = folder/name
                if original[name] is not None:
                    os.replace(target, backup/name)
                    backed_up.append(name)
            for name in FILES:
                os.replace(stage/name, folder/name)
                published.append(name)
        except BaseException as publish_error:
            restore_failed = False
            for name in reversed(FILES):
                target = folder/name
                try:
                    if (backup/name).exists():
                        os.replace(backup/name, target)
                    elif original[name] is None and name in published:
                        target.unlink()
                except BaseException:
                    restore_failed = True
            try:
                restored = _snapshot(folder)
            except BaseException:
                restore_failed = True
                restored = None
            if restored != original:
                restore_failed = True
            if restore_failed:
                keep_lock = True
                raise ArtifactRestoreError(
                    'バックテスト成果の公開と復元に失敗しました') from publish_error
            if created_folder and not any(folder.iterdir()):
                folder.rmdir()
            raise
        try:
            for name in FILES:
                backup_file = backup/name
                if _observe(backup_file, 'file') is not None:
                    backup_file.unlink()
            backup.rmdir()
            stage.rmdir()
        except BaseException:
            keep_lock = True
            raise ArtifactCleanupError(
                'バックテスト成果は公開済みですが後片付けが未完了です。手動確認が必要です') from None
    finally:
        if not keep_lock:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
