"""Seal and inspect completed mock daily history without running it again."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path

from aitrader_ops.models import JST

from .runner import RunError, encoded, write_json


_EXCLUDED = {'daily_receipt.json', 'daily_progress.json'}
_TERMINAL_STATUSES = {
    'CANDIDATES', 'DATA_INCOMPLETE', 'NO_SESSION', 'REVIEW_INCOMPLETE',
    'REVIEW_INVALID', 'NOT_APPROVED', 'NO_SIGNAL',
}
_MANAGED_POLICY = 'managed-v1'


def _is_link(path):
    try:
        if path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path)):
            return True
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return False


def _forbidden_temporary(relative):
    name = relative.name.lower()
    return (name.endswith('.tmp') or name.endswith('-wal') or name.endswith('-shm')
            or name.endswith('-journal')
            or name.endswith('.wal') or name.endswith('.shm'))


def _guard_home_parents(home):
    """Validate the raw path chain without resolving through a link."""
    chain = list(reversed(home.parents)) + [home]
    for index, path in enumerate(chain):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if index == len(chain) - 1:
                return False
            raise RunError('日次homeの親フォルダを確認できません') from None
        except OSError:
            raise RunError('日次homeの親フォルダを確認できません') from None
        if _is_link(path) or not stat.S_ISDIR(info.st_mode):
            raise RunError('日次homeの親フォルダを安全に確認できません')
    return True


def _snapshot(home):
    """Return a content snapshot, rejecting ambiguous filesystem objects."""
    if not _guard_home_parents(home) or _is_link(home) or not home.is_dir():
        raise RunError('日次homeが通常フォルダではありません')
    paths, pending = [], [home]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as scanned:
            children = sorted(scanned, key=lambda entry: entry.name)
        for entry in children:
            path = Path(entry.path)
            if entry.is_symlink() or _is_link(path):
                raise RunError('日次home内にリンクまたはjunctionがあります')
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                paths.append(path)
            else:
                raise RunError('日次home内に通常ファイル以外があります')
    entries, captured_bodies = [], {}
    for path in sorted(paths, key=lambda p: p.relative_to(home).as_posix()):
        relative = path.relative_to(home)
        key = relative.as_posix()
        if _forbidden_temporary(relative):
            raise RunError('日次home内に一時/WAL/SHMファイルがあります')
        if key in _EXCLUDED:
            continue
        capture = key in {'daily_rehearsal.json', 'mock-runner.json',
                          'managed-stop-clock.json'}
        size, hasher, captured = 0, hashlib.sha256(), [] if capture else None
        with path.open('rb') as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                hasher.update(chunk)
                if captured is not None:
                    captured.append(chunk)
        if captured is not None:
            captured_bodies[key] = b''.join(captured)
        entries.append({'path': key, 'size': size, 'sha256': hasher.hexdigest()})
    return entries, captured_bodies


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def _strict_loads(body):
    return json.loads(body, object_pairs_hook=_unique_object)


def _read_json(path):
    value = _strict_loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def _aware_text(value):
    if not isinstance(value, str):
        raise ValueError('aware ISO datetime required')
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('aware ISO datetime required')
    return parsed.astimezone(JST)


def _marker_kind(home, marker, clock=None):
    """Validate mock marker/clock semantics without opening the STOP database."""
    if (isinstance(marker, dict) and set(marker) == {'mode', 'version'}
            and marker.get('mode') == 'mock'
            and type(marker.get('version')) is int and marker.get('version') == 1):
        if clock is not None:
            raise ValueError('legacy marker cannot have managed clock')
        return 'legacy'
    required = {'mode', 'version', 'stop_policy', 'initialized_at',
                'settings_hash', 'stop_files'}
    if (not isinstance(marker, dict) or set(marker) != required
            or marker.get('mode') != 'mock'
            or type(marker.get('version')) is not int or marker.get('version') != 2
            or marker.get('stop_policy') != _MANAGED_POLICY
            or not isinstance(marker.get('settings_hash'), str)
            or len(marker['settings_hash']) != 64
            or any(ch not in '0123456789abcdef' for ch in marker['settings_hash'])
            or not isinstance(marker.get('stop_files'), list)
            or not marker['stop_files']
            or any(not isinstance(item, str) for item in marker['stop_files'])
            or len(set(marker['stop_files'])) != len(marker['stop_files'])):
        raise ValueError('invalid managed marker')
    initialized = _aware_text(marker['initialized_at'])
    stop_files = [Path(item) for item in marker['stop_files']]
    expected_first = (home/'STOP').resolve()
    if (stop_files[0] != expected_first
            or any(not item.is_absolute() or not item.resolve().is_relative_to(home.resolve())
                   for item in stop_files)):
        raise ValueError('invalid managed stop paths')
    if (not isinstance(clock, dict)
            or set(clock) != {'mode', 'version', 'observed_at'}
            or clock.get('mode') != 'mock'
            or type(clock.get('version')) is not int or clock.get('version') != 1
            or _aware_text(clock.get('observed_at')) < initialized):
        raise ValueError('invalid managed clock')
    return 'managed'


def seal_daily_rehearsal(home, *, expected_progress_seq):
    """Write a versioned manifest for an about-to-complete fresh rehearsal."""
    if type(expected_progress_seq) is not int or expected_progress_seq <= 0:
        raise RunError('expected_progress_seqは正の整数で指定してください')
    home = Path(home).absolute()
    if any('dropbox' in part.lower() for part in home.parts):
        raise RunError('実行先はDropbox外にしてください')
    _guard_home_parents(home)
    receipt_path = home/'daily_receipt.json'
    if receipt_path.exists():
        raise RunError('日次レシートは既に存在します')
    try:
        initial_files, _ = _snapshot(home)  # Validate every read target before opening it.
        if any(item['path'] == '.managed-stop-initializing' for item in initial_files):
            raise ValueError('managed initialization incomplete')
        progress = _read_json(home/'daily_progress.json')
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RunError('日次進捗を確認できません') from exc
    current_seq = progress.get('checkpoint_seq')
    if (progress.get('status') != 'RUNNING'
            or progress.get('current_stage') != 'finalize'
            or type(current_seq) is not int or current_seq < 0
            or current_seq + 1 != expected_progress_seq):
        raise RunError('完了直前の日次進捗と期待seqが一致しません')
    try:
        marker = _read_json(home/'mock-runner.json')
        summary = _read_json(home/'daily_rehearsal.json')
        clock_path = home/'managed-stop-clock.json'
        clock = _read_json(clock_path) if clock_path.is_file() else None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RunError('封印対象の日次成果物を確認できません') from exc
    try:
        marker_kind = _marker_kind(home, marker, clock)
    except (ValueError, TypeError, OSError) as exc:
        raise RunError('模擬日次homeではありません') from exc
    if (marker_kind == 'managed'
            and (summary.get('managed_stop') is not True
                 or summary.get('stop_policy') != _MANAGED_POLICY)):
        raise RunError('模擬日次homeではありません')
    if (summary.get('mode') != 'mock' or summary.get('source') != 'synthetic'
            or summary.get('virtual_clock') is not True
            or summary.get('real_sent') is not False
            or summary.get('delivery') != 'NOT_SENT'):
        raise RunError('安全な完全合成日次要約ではありません')
    files, _ = _snapshot(home)
    receipt = {'version': 1, 'mode': 'mock',
               'expected_completed_checkpoint_seq': expected_progress_seq,
               'files': files}
    write_json(receipt_path, receipt)
    return receipt


def _pending(reason):
    return {'status': 'NEEDS_RECONCILIATION', 'reason': reason,
            'auto_resume': False, 'repaired': False, 'current_signal': False}


def inspect_daily_rehearsal(home):
    """Read and verify sealed history; never open DBs, repair, or create files.

    Two complete content snapshots detect changes across this observation, but
    this is not an atomic filesystem snapshot or a power-loss guarantee.
    """
    try:
        home = Path(home).absolute()
    except (TypeError, ValueError, OSError):
        return _pending('INVALID_HOME')
    try:
        exists = _guard_home_parents(home)
    except RunError:
        return _pending('UNREADABLE_OR_UNSAFE_HISTORY')
    if not exists:
        return _pending('HOME_NOT_FOUND')
    try:
        receipt_path, progress_path = home/'daily_receipt.json', home/'daily_progress.json'
        first, _ = _snapshot(home)
        receipt_bytes_1 = receipt_path.read_bytes()
        progress_bytes_1 = progress_path.read_bytes()
        receipt = _strict_loads(receipt_bytes_1.decode('utf-8'))
        progress = _strict_loads(progress_bytes_1.decode('utf-8'))
        second, captured = _snapshot(home)
        receipt_bytes_2 = receipt_path.read_bytes()
        progress_bytes_2 = progress_path.read_bytes()
    except (OSError, ValueError, TypeError, json.JSONDecodeError, RunError,
            RecursionError):
        return _pending('UNREADABLE_OR_UNSAFE_HISTORY')
    if (receipt_bytes_1 != receipt_bytes_2 or progress_bytes_1 != progress_bytes_2
            or first != second):
        return _pending('CHANGED_DURING_INSPECTION')
    if (not isinstance(receipt, dict) or set(receipt) != {
            'version', 'mode', 'expected_completed_checkpoint_seq', 'files'}
            or type(receipt.get('version')) is not int or receipt.get('version') != 1
            or receipt.get('mode') != 'mock'
            or type(receipt.get('expected_completed_checkpoint_seq')) is not int
            or not isinstance(receipt.get('files'), list)):
        return _pending('INVALID_RECEIPT')
    for item in receipt['files']:
        if (not isinstance(item, dict) or set(item) != {'path', 'size', 'sha256'}
                or not isinstance(item['path'], str) or not item['path']
                or Path(item['path']).is_absolute() or '\\' in item['path']
                or any(part in ('', '.', '..') for part in item['path'].split('/'))
                or type(item['size']) is not int or item['size'] < 0
                or not isinstance(item['sha256'], str) or len(item['sha256']) != 64):
            return _pending('INVALID_RECEIPT')
    if len({item['path'] for item in receipt['files']}) != len(receipt['files']):
        return _pending('INVALID_RECEIPT')
    if receipt['files'] != second:
        return _pending('FILE_SET_OR_CONTENT_MISMATCH')
    if any(item['path'] == '.managed-stop-initializing' for item in second):
        return _pending('UNSAFE_MANAGED_HISTORY')
    expected_seq = receipt['expected_completed_checkpoint_seq']
    if expected_seq <= 0:
        return _pending('INVALID_RECEIPT')
    if (not isinstance(progress, dict) or progress.get('status') != 'COMPLETED'
            or progress.get('current_stage') != 'finalize'
            or type(progress.get('checkpoint_seq')) is not int
            or progress.get('checkpoint_seq') != expected_seq
            or progress.get('mode') != 'mock' or progress.get('source') != 'synthetic'
            or progress.get('virtual_clock') is not True
            or progress.get('real_sent') is not False):
        return _pending('PROGRESS_NOT_COMPLETED')
    try:
        summary = _strict_loads(captured['daily_rehearsal.json'].decode('utf-8'))
    except (KeyError, AttributeError, UnicodeDecodeError, ValueError, TypeError,
            json.JSONDecodeError, RecursionError):
        return _pending('INVALID_SUMMARY')
    summary_status = summary.get('status') if isinstance(summary, dict) else None
    if (not isinstance(summary, dict) or summary.get('mode') != 'mock'
            or summary.get('source') != 'synthetic'
            or summary.get('virtual_clock') is not True
            or summary.get('real_sent') is not False
            or summary.get('delivery') != 'NOT_SENT'
            or not isinstance(summary_status, str)
            or summary_status not in _TERMINAL_STATUSES
            or progress.get('result_status') != summary_status):
        return _pending('UNSAFE_SUMMARY')
    try:
        marker = _strict_loads(captured['mock-runner.json'].decode('utf-8'))
        clock_body = captured.get('managed-stop-clock.json')
        clock = _strict_loads(clock_body.decode('utf-8')) if clock_body is not None else None
        marker_kind = _marker_kind(home, marker, clock)
    except (KeyError, UnicodeDecodeError, ValueError, TypeError, OSError,
            json.JSONDecodeError, RecursionError):
        return _pending('UNSAFE_MANAGED_HISTORY')
    if marker_kind == 'managed':
        if (summary.get('managed_stop') is not True
                or summary.get('stop_policy') != _MANAGED_POLICY):
            return _pending('UNSAFE_MANAGED_HISTORY')
    elif summary.get('managed_stop') is True or summary.get('stop_policy') == _MANAGED_POLICY:
        return _pending('UNSAFE_MANAGED_HISTORY')
    return {'status': 'VERIFIED_HISTORY', 'saved_summary': summary,
            'auto_resume': False, 'repaired': False, 'current_signal': False,
            'checkpoint_seq': expected_seq}


def main(argv=None):
    parser = argparse.ArgumentParser(description='封印済み完全合成日次履歴の読取専用検査')
    sub = parser.add_subparsers(dest='command', required=True)
    inspect_parser = sub.add_parser('inspect')
    inspect_parser.add_argument('--home', required=True, type=Path)
    args = parser.parse_args(argv)
    result = inspect_daily_rehearsal(args.home)
    print(encoded(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
