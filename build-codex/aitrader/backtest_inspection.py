"""Read-only observation of one backtest result set.

This module does not acquire writer locks, repair files, or prove that the four
artifacts were generated as one atomic snapshot.
"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path


FILES = ('trades.csv', 'equity.csv', 'summary.json', 'report.html')
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_PARENT_ENTRIES = 5000
_REPARSE_POINT = 0x400


def _result(status, reason, *, artifacts=None, residues=None, unchanged=False):
    value = {
        'status': status,
        'reason': reason,
        'artifacts': artifacts if artifacts is not None else {},
        'residues': residues if residues is not None else {},
        'generation_verified': False,
        'ready_for_live': False,
        'automatic_resume_allowed': False,
        'observation_atomic': False,
        'observation_unchanged': unchanged,
        'read_only': True,
    }
    return value


def _unsafe(info, path):
    return (stat.S_ISLNK(info.st_mode)
            or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))
            or bool(getattr(info, 'st_file_attributes', 0) & _REPARSE_POINT))


def _lstat(path, expected):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError from None
    valid = ((expected == 'file' and stat.S_ISREG(info.st_mode))
             or (expected == 'directory' and stat.S_ISDIR(info.st_mode)))
    if _unsafe(info, path) or not valid:
        raise ValueError
    return info


def _guard_parents(folder):
    for item in reversed((folder.parent, *folder.parent.parents)):
        if _lstat(item, 'directory') is None:
            raise ValueError


def _same_folder(folder, original):
    _guard_parents(folder)
    current = _lstat(folder, 'directory')
    return current is not None and _identity(current) == _identity(original)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode


def _fingerprint(path):
    before = _lstat(path, 'file')
    if before is None:
        return None
    if before.st_size > _MAX_FILE_BYTES:
        raise ValueError
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if (_identity(opened) != _identity(before)
                    or not stat.S_ISREG(opened.st_mode)):
                raise ValueError
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_FILE_BYTES:
                    raise ValueError
                digest.update(chunk)
    except ValueError:
        raise
    except OSError:
        raise ValueError from None
    after = _lstat(path, 'file')
    if (after is None or _identity(after) != _identity(before)
            or size != before.st_size
            or (after.st_size, after.st_mtime_ns)
            != (before.st_size, before.st_mtime_ns)):
        raise ValueError
    return {'size': size, 'sha256': digest.hexdigest()}


def _parent_state(folder):
    """Return relevant entry identities without entering residue directories."""
    lock_name = '.' + folder.name + '.publish.lock'
    prefixes = {
        'stage': '.' + folder.name + '.stage.',
        'backup': '.' + folder.name + '.backup.',
        'export_stage': '.' + folder.name + '.export-stage.',
        'export_backup': '.' + folder.name + '.export-backup.',
    }
    entries = {}
    counts = {key: 0 for key in prefixes}
    count = 0
    try:
        with os.scandir(folder.parent) as iterator:
            for entry in iterator:
                count += 1
                if count > _MAX_PARENT_ENTRIES:
                    raise ValueError
                name = entry.name
                category = ('lock' if name == lock_name else
                            next((key for key, prefix in prefixes.items()
                                  if name.startswith(prefix)), None))
                if category is None:
                    continue
                path = folder.parent / name
                info = path.lstat()
                if _unsafe(info, path):
                    raise ValueError
                if category == 'lock':
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError
                else:
                    if not stat.S_ISDIR(info.st_mode):
                        raise ValueError
                    counts[category] += 1
                entries[name] = (_identity(info), info.st_size, info.st_mtime_ns)
    except (ValueError, FileNotFoundError):
        raise ValueError from None
    except OSError:
        raise ValueError from None
    return entries, {'lock': int(lock_name in entries), **counts}


def inspect_backtest_results(folder):
    """Inspect four result files without writing, locking, deleting, or repairing."""
    try:
        target = Path(folder).absolute()
        _guard_parents(target)
        target_info = _lstat(target, 'directory')
        if target_info is None:
            return _result('UNKNOWN', 'RESULT_FOLDER_MISSING')

        parent_before, residues = _parent_state(target)
        if any(residues.values()):
            parent_after, residues_after = _parent_state(target)
            if (parent_after != parent_before or residues_after != residues
                    or not _same_folder(target, target_info)):
                return _result('UNKNOWN', 'OBSERVATION_CHANGED')
            return _result('REVIEW_REQUIRED', 'PUBLICATION_RESIDUE_PRESENT',
                           residues=residues, unchanged=True)

        first = {name: _fingerprint(target / name) for name in FILES}
        second = {name: _fingerprint(target / name) for name in FILES}
        parent_after, residues_after = _parent_state(target)
        if (parent_after != parent_before or any(residues_after.values())
                or not _same_folder(target, target_info)):
            return _result('UNKNOWN', 'OBSERVATION_CHANGED')
        if first != second:
            return _result('UNKNOWN', 'OBSERVATION_CHANGED')
        if any(value is None for value in first.values()):
            return _result('INCOMPLETE', 'REQUIRED_ARTIFACT_MISSING',
                           unchanged=True)
        return _result('OBSERVED', 'FOUR_ARTIFACTS_OBSERVED', artifacts=first,
                       unchanged=True)
    except (TypeError, ValueError, OSError):
        return _result('UNKNOWN', 'UNSAFE_OR_UNREADABLE_PATH')
