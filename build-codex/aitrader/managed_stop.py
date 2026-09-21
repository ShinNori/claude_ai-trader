"""Opt-in managed STOP policy for fresh mock homes only."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from datetime import datetime
from pathlib import Path

from aitrader_ops.ledger import Ledger
from aitrader_ops.models import JST
from aitrader_ops.notify import Notifier
from aitrader_ops.stop_status import inspect_stop_status


_FLAG = '.managed-stop-initializing'
_CLOCK = 'managed-stop-clock.json'
_MARKER = 'mock-runner.json'
_POLICY = 'managed-v1'
_HEX64 = re.compile(r'[0-9a-f]{64}')
_MAX_JSON_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def _link_stat(value):
    return (stat.S_ISLNK(value.st_mode)
            or bool(getattr(value, 'st_file_attributes', 0) & _REPARSE_POINT))


def _guard_components(path, *, include_leaf=True):
    """Reject links/reparse points before resolving a managed path."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('managed pathは絶対pathで指定してください')
    parts = path.parts if include_leaf else path.parent.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current/part
        try:
            found = current.lstat()
        except FileNotFoundError:
            continue
        if _link_stat(found):
            raise ValueError('managed pathにlink/reparse pointは使用できません')


def _read(path):
    """Read one bounded, stable, ordinary JSON object from a managed path."""
    path = Path(path)
    _guard_components(path)
    before = path.lstat()
    if _link_stat(before) or not stat.S_ISREG(before.st_mode):
        raise ValueError('managed JSONは通常fileで指定してください')
    if before.st_size > _MAX_JSON_BYTES:
        raise ValueError('managed JSONが大きすぎます')
    with path.open('rb') as stream:
        opened_before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened_before.st_mode)
                or (opened_before.st_dev, opened_before.st_ino)
                != (before.st_dev, before.st_ino)):
            raise ValueError('managed JSONが観測中に変化しました')
        body = stream.read(_MAX_JSON_BYTES + 1)
        opened_after = os.fstat(stream.fileno())
    after = path.lstat()
    if (len(body) > _MAX_JSON_BYTES
            or _link_stat(after)
            or (after.st_dev, after.st_ino, after.st_mode, after.st_size,
                after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_mode, before.st_size,
                before.st_mtime_ns)
            or (opened_after.st_dev, opened_after.st_ino, opened_after.st_mode,
                opened_after.st_size, opened_after.st_mtime_ns)
            != (opened_before.st_dev, opened_before.st_ino,
                opened_before.st_mode, opened_before.st_size,
                opened_before.st_mtime_ns)
            or len(body) != before.st_size):
        raise ValueError('managed JSONが観測中に変化しました')
    value = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('日時にはタイムゾーンが必要です')
    return value.astimezone(JST)


def _home_path(value):
    try:
        path = Path(value)
        if not path.is_absolute():
            path = path.absolute()
        _guard_components(path)
        path = path.resolve()
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError('homeを正しく指定してください') from exc
    if any('dropbox' in part.lower() for part in path.parts):
        raise ValueError('実行先はDropbox外にしてください')
    return path


def _settings_and_files(home, settings):
    if not isinstance(settings, dict):
        raise ValueError('settingsは辞書で指定してください')
    files = [home/'STOP']
    configured = settings.get('stop_file')
    if configured is not None:
        if not isinstance(configured, (str, os.PathLike)):
            raise ValueError('settings.stop_fileはpathで指定してください')
        candidate = Path(configured)
        if not candidate.is_absolute():
            raise ValueError('settings.stop_fileは絶対pathで指定してください')
        _guard_components(candidate)
        candidate = candidate.resolve()
        if not candidate.is_relative_to(home):
            raise ValueError('settings.stop_fileはhome内に限定してください')
        if candidate not in files:
            files.append(candidate)
    return [str(path) for path in files]


def _unknown(now, reason):
    return {'status': 'UNKNOWN', 'known': False, 'effective_stop': True,
            'persistent_stopped': None, 'reasons': [reason],
            'observed_at': now.isoformat(), 'control_event_at': None,
            'read_only': True}


def _exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def initialize_managed_mock(home, cash, positions, at, *, settings):
    """Exclusively claim and initialize a new managed mock home."""
    from .runner import digest, initialize_mock, write_json

    home = _home_path(home)
    at = _aware(at)
    stop_files = _settings_and_files(home, settings)
    if home.exists():
        raise ValueError('managed mockには未作成の専用homeを指定してください')
    home.mkdir(parents=True, exist_ok=False)
    flag = home/_FLAG
    flag.write_text('initialization incomplete; do not auto-repair\n', encoding='utf-8')
    initialize_mock(home, cash, positions, at)
    state_path = home/'notification.sqlite'
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        with closing(Notifier(ledger=ledger, state_path=state_path, settings=settings,
                              transport='stub', stub_results=[])):
            pass
    clock = {'mode': 'mock', 'version': 1, 'observed_at': at.isoformat()}
    write_json(home/_CLOCK, clock)
    marker = {'mode': 'mock', 'version': 2, 'stop_policy': _POLICY,
              'initialized_at': at.isoformat(), 'settings_hash': digest(settings),
              'stop_files': stop_files}
    write_json(home/_MARKER, marker)
    flag.unlink()
    return {'mode': 'mock', 'version': 2, 'stop_policy': _POLICY,
            'initialized_at': at.isoformat(), 'state_path': str(state_path),
            'stop_files': stop_files}


def managed_stop_policy(home):
    """Return a fixed managed policy, or None for an exact legacy v1 marker."""
    from .runner import RunError

    home = _home_path(home)
    try:
        initializing = _exists(home/_FLAG)
    except OSError as exc:
        raise RunError('managed STOP初期化flagを確認できません') from exc
    if initializing:
        raise RunError('managed STOPの初期化が未完了です')
    try:
        marker = _read(home/_MARKER)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RunError('mock markerを確認できません') from exc
    if (marker == {'mode': 'mock', 'version': 1}
            and type(marker.get('version')) is int):
        return None
    required = {'mode', 'version', 'stop_policy', 'initialized_at',
                'settings_hash', 'stop_files'}
    if (set(marker) != required or marker.get('mode') != 'mock'
            or type(marker.get('version')) is not int or marker.get('version') != 2
            or marker.get('stop_policy') != _POLICY
            or not isinstance(marker.get('settings_hash'), str)
            or _HEX64.fullmatch(marker['settings_hash']) is None
            or not isinstance(marker.get('stop_files'), list)
            or any(not isinstance(item, str) for item in marker['stop_files'])):
        raise RunError('managed STOP markerが不正です')
    try:
        initialized_at = _aware(marker['initialized_at'])
        stop_files = [Path(item) for item in marker['stop_files']]
        for item in stop_files:
            _guard_components(item)
    except (ValueError, TypeError, OSError) as exc:
        raise RunError('managed STOP markerが不正です') from exc
    if (not stop_files or stop_files[0] != (home/'STOP').resolve()
            or len(set(stop_files)) != len(stop_files)
            or any(not item.is_absolute() or not item.resolve().is_relative_to(home)
                   for item in stop_files)):
        raise RunError('managed STOPの固定ファイルが不正です')
    return {'mode': 'mock', 'version': 2, 'stop_policy': _POLICY,
            'initialized_at': initialized_at, 'settings_hash': marker['settings_hash'],
            'stop_files': tuple(stop_files), 'state_path': home/'notification.sqlite',
            'clock_path': home/_CLOCK}


def _clock(policy):
    from .runner import RunError

    try:
        clock = _read(policy['clock_path'])
        observed = _aware(clock.get('observed_at'))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RunError('managed STOP clockを確認できません') from exc
    if (set(clock) != {'mode', 'version', 'observed_at'}
            or clock.get('mode') != 'mock' or type(clock.get('version')) is not int
            or clock.get('version') != 1):
        raise RunError('managed STOP clockが不正です')
    return observed


def inspect_managed_stop(home, *, now):
    """Read the effective managed STOP state without opening a writable DB."""
    from .runner import RunError

    now = _aware(now)
    try:
        policy = managed_stop_policy(home)
        if policy is None:
            raise RunError('managed STOP policyがありません')
        observed = _clock(policy)
    except (RunError, ValueError, OSError, RecursionError):
        return _unknown(now, 'MANAGED_POLICY_INVALID')
    if observed < policy['initialized_at']:
        return _unknown(now, 'MANAGED_CLOCK_INVALID')
    if now < observed or now < policy['initialized_at']:
        return _unknown(now, 'MANAGED_CLOCK_ROLLBACK')
    return inspect_stop_status(policy['state_path'], now=now,
                               stop_files=policy['stop_files'])


def prepare_managed_operation(home, *, now, settings):
    """Validate closed state under the caller's runner lock, without writing."""
    from .runner import RunError, digest

    policy = managed_stop_policy(home)
    if policy is None:
        return None
    if digest(settings) != policy['settings_hash']:
        raise RunError('managed STOPの固定settingsが一致しません')
    status = inspect_managed_stop(home, now=now)
    if not status['known']:
        raise RunError('managed STOP状態を確認できません')
    return policy


def advance_managed_operation(home, policy, *, now):
    """Advance the shared floor before effects; caller holds runner-lock."""
    from .runner import RunError, write_json

    if policy is None:
        return
    now = _aware(now)
    if managed_stop_policy(home) != policy:
        raise RunError('managed STOP policyが操作中に変化しました')
    observed = _clock(policy)
    if observed < policy['initialized_at'] or now < observed:
        raise RunError('managed STOP clockより前の操作はできません')
    write_json(policy['clock_path'], {'mode': 'mock', 'version': 1,
                                     'observed_at': now.isoformat()})


def managed_file_check(home, policy, *, now):
    """Dynamic fail-closed check; never inspect a DB opened by Notifier."""
    now = _aware(now)

    def stopped():
        try:
            if managed_stop_policy(home) != policy:
                return True
            observed = _clock(policy)
            if observed < policy['initialized_at'] or now < observed:
                return True
            return any(_exists(path) for path in policy['stop_files'])
        except (OSError, ValueError, TypeError):
            return True
    return stopped


def apply_managed_control(home, *, action, now, settings, reconciled_at=None,
                          event_at=None, event_id=None):
    """Apply STOP/RESUME under runner-lock using the existing stub Notifier."""
    from .runner import RunError, digest, write_json

    if action not in ('STOP', 'RESUME'):
        raise RunError('actionはSTOPまたはRESUMEで指定してください')
    now = _aware(now)
    event_at = now if event_at is None else _aware(event_at)
    reconciled_at = None if reconciled_at is None else _aware(reconciled_at)
    if event_at > now:
        raise RunError('未来の制御操作は受け付けません')
    home = _home_path(home)
    policy = managed_stop_policy(home)
    if policy is None or digest(settings) != policy['settings_hash']:
        raise RunError('managed STOPの固定settingsが一致しません')
    lock_path = home/'runner-lock.sqlite'
    with closing(sqlite3.connect(lock_path, isolation_level=None, timeout=5)) as lock:
        lock.execute('BEGIN IMMEDIATE')
        locked_policy = managed_stop_policy(home)
        if locked_policy != policy:
            raise RunError('managed STOP policyがロック取得中に変化しました')
        observed = _clock(policy)
        if observed < policy['initialized_at']:
            raise RunError('managed STOP clockが初期化時刻より前です')
        if now < observed or now < policy['initialized_at']:
            raise RunError('managed STOP clockより前の操作はできません')
        ledger_path = home/'ledger.sqlite'
        try:
            if not _exists(ledger_path) or ledger_path.is_symlink() or not ledger_path.is_file():
                raise RunError('managed STOP台帳を確認できません')
        except OSError as exc:
            raise RunError('managed STOP台帳を確認できません') from exc
        before = inspect_stop_status(policy['state_path'], now=now,
                                     stop_files=policy['stop_files'])
        if not before['known']:
            raise RunError('managed STOP状態を確認できません')
        write_json(policy['clock_path'], {'mode': 'mock', 'version': 1,
                                         'observed_at': now.isoformat()})
        with closing(Ledger(home/'ledger.sqlite')) as ledger:
            with closing(Notifier(ledger=ledger, state_path=policy['state_path'],
                                  settings=settings, transport='stub',
                                  stub_results=[])) as notifier:
                if action == 'STOP':
                    result = notifier.stop(now=now, event_id=event_id,
                                           event_at=event_at)
                else:
                    result = notifier.resume(now=now, reconciled_at=reconciled_at,
                                             event_id=event_id, event_at=event_at)
        stop_status = inspect_stop_status(policy['state_path'], now=now,
                                          stop_files=policy['stop_files'])
        return {'mode': 'mock', 'action': action, 'result': result,
                'observed_at': now.isoformat(), 'stop_status': stop_status,
                'real_sent': False}
