"""Explicit CLI wrapper for existing managed mock STOP controls."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
from datetime import datetime
from pathlib import Path

from .managed_stop import apply_managed_control
from .runner import encoded


_MAX_SETTINGS_BYTES = 1024 * 1024


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error includes untrusted argument values.
        self.exit(2, '管理STOP操作の引数を確認してください。詳細は --help を参照してください。\n')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _is_link(path):
    try:
        if path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path)):
            return True
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return False


def _aware(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('aware datetime required')
    return parsed


def _settings(path):
    path = Path(path).absolute()
    for component in reversed((path, *path.parents)):
        if _is_link(component):
            raise ValueError('unsafe settings file')

    def bounded_read():
        if _is_link(path):
            raise ValueError('unsafe settings file')
        observed = path.lstat()
        if not stat.S_ISREG(observed.st_mode) or observed.st_size > _MAX_SETTINGS_BYTES:
            raise ValueError('invalid settings file')
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_SETTINGS_BYTES:
                raise ValueError('invalid settings file')
            body = stream.read(_MAX_SETTINGS_BYTES + 1)
        if len(body) > _MAX_SETTINGS_BYTES:
            raise ValueError('invalid settings file')
        return observed, opened, body

    before, opened_before, body = bounded_read()
    after, opened_after, repeated = bounded_read()
    if (body != repeated
            or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            or (opened_before.st_size, opened_before.st_mtime_ns)
            != (opened_after.st_size, opened_after.st_mtime_ns)):
        raise ValueError('changed settings file')
    value = json.loads(body.decode('utf-8'), object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise ValueError('settings object required')
    return value


def main(argv=None):
    parser = _PrivateArgumentParser(description='管理STOPの明示操作（mock・stub限定）')
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--action', choices=('STOP', 'RESUME'), required=True)
    parser.add_argument('--now', required=True, help='タイムゾーン付きISO日時')
    parser.add_argument('--settings', type=Path, required=True, help='固定済みJSON設定file')
    parser.add_argument('--reconciled-at')
    parser.add_argument('--event-at')
    parser.add_argument('--event-id')
    args = parser.parse_args(argv)
    try:
        now = _aware(args.now)
        reconciled_at = (_aware(args.reconciled_at)
                         if args.reconciled_at is not None else None)
        event_at = _aware(args.event_at) if args.event_at is not None else None
        settings = _settings(args.settings)
        result = apply_managed_control(
            args.home, action=args.action, now=now, settings=settings,
            reconciled_at=reconciled_at, event_at=event_at, event_id=args.event_id)
    except (OSError, UnicodeDecodeError, ValueError, TypeError,
            json.JSONDecodeError, sqlite3.Error, RecursionError):
        parser.exit(2, '管理STOP操作の入力または固定状態を確認できません。適用状態は未確認です。読取診断で確認してください。\n')
    print(encoded(result))
    return 0 if result.get('result') in ('STOPPED', 'RESUMED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
