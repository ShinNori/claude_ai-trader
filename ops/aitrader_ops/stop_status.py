"""Conservative, read-only inspection of the durable notification STOP state."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .models import JST


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('nowはタイムゾーン付きdatetimeで指定してください')
    return value.astimezone(JST)


def _is_link(path):
    try:
        if path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path)):
            return True
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, FileNotFoundError):
        return False


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path):
    info = path.stat()
    return (info.st_size, info.st_mtime_ns, getattr(info, 'st_ino', None), _hash(path))


def _exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _result(now, status, known, effective, persistent, reasons, control_event_at=None):
    return {'status': status, 'known': known, 'effective_stop': effective,
            'persistent_stopped': persistent, 'reasons': sorted(set(reasons)),
            'observed_at': now.isoformat(),
            'control_event_at': control_event_at.isoformat() if control_event_at else None,
            'read_only': True}


def _unknown(now, *reasons):
    return _result(now, 'UNKNOWN', False, True, None, reasons)


def _json_value(raw):
    return json.loads(raw)


def _time(value, now):
    parsed = _aware(value)
    if parsed > now:
        raise ValueError('future timestamp')
    return parsed


def _read_persistent(path, now):
    uri = path.as_uri() + '?mode=ro&immutable=1'
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        check = db.execute('PRAGMA quick_check').fetchone()
        if check != ('ok',):
            raise ValueError('database integrity')
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'kv', 'controls'}.issubset(tables):
            raise ValueError('schema missing')
        kv_columns = {row[1] for row in db.execute('PRAGMA table_info(kv)')}
        control_columns = {row[1] for row in db.execute('PRAGMA table_info(controls)')}
        if not {'name', 'value'}.issubset(kv_columns) or not {
                'seq', 'action', 'at', 'event_id', 'reconciled_at', 'event_at'}.issubset(control_columns):
            raise ValueError('schema mismatch')
        rows = db.execute(
            'SELECT seq,action,at,event_id,reconciled_at,event_at FROM controls ORDER BY seq').fetchall()
        kv_rows = db.execute(
            "SELECT name,value FROM kv WHERE name IN ('stopped','stopped_at','stopped_event_at')").fetchall()
        if len({row[0] for row in kv_rows}) != len(kv_rows):
            raise ValueError('duplicate STOP kv')
        raw_kv = dict(kv_rows)

    controls, previous_seq, latest_event = [], 0, None
    last_stop = None
    for seq, action, at_raw, event_id, reconciled_raw, event_raw in rows:
        if type(seq) is not int or seq <= previous_seq or action not in {
                'STOP', 'STOP_LATE', 'RESUME', 'RESUME_LATE'}:
            raise ValueError('invalid control history')
        previous_seq = seq
        at = _time(at_raw, now)
        event_at = _time(event_raw, now)
        if at < event_at:
            raise ValueError('control observed before event')
        if action.endswith('_LATE'):
            if latest_event is None or event_at >= latest_event:
                raise ValueError('late control is not late')
        else:
            if latest_event is not None and event_at < latest_event:
                raise ValueError('accepted control rolled time back')
            if action == 'STOP':
                if reconciled_raw is not None:
                    raise ValueError('STOP has reconciliation')
                last_stop = (at, event_at)
            else:
                if reconciled_raw is None:
                    raise ValueError('RESUME lacks reconciliation')
                reconciled = _time(reconciled_raw, now)
                if reconciled > at or (last_stop and reconciled < last_stop[0]):
                    raise ValueError('invalid reconciliation time')
            latest_event = event_at
        controls.append((action, at, event_at))

    stop_keys = {'stopped', 'stopped_at', 'stopped_event_at'}
    if not rows and not raw_kv:
        return False, None
    if 'stopped' not in raw_kv or set(raw_kv) - stop_keys:
        raise ValueError('incomplete STOP kv')
    stopped = _json_value(raw_kv['stopped'])
    if type(stopped) is not bool or not controls:
        raise ValueError('invalid stopped value')
    accepted = [item for item in controls if not item[0].endswith('_LATE')]
    if not accepted:
        raise ValueError('accepted control missing')
    expected = accepted[-1][0] == 'STOP'
    if stopped is not expected:
        raise ValueError('history and stopped disagree')
    if last_stop is None:
        if stopped or set(raw_kv) != {'stopped'} or accepted[-1][0] != 'RESUME':
            raise ValueError('STOP history missing')
    else:
        if not {'stopped_at', 'stopped_event_at'}.issubset(raw_kv):
            raise ValueError('STOP timestamps missing')
        stopped_at = _time(_json_value(raw_kv['stopped_at']), now)
        stopped_event_at = _time(_json_value(raw_kv['stopped_event_at']), now)
        if (stopped_at, stopped_event_at) != last_stop:
            raise ValueError('STOP timestamps disagree')
    return stopped, accepted[-1][2]


def _stop_file_states(stop_files):
    states = []
    for raw in stop_files:
        candidate = Path(raw).absolute()
        present = _exists(candidate)
        unsafe = present and (_is_link(candidate) or not candidate.is_file())
        states.append((str(candidate), present, unsafe))
    return states


def inspect_stop_status(state_path, *, now, stop_files=()):
    """Inspect persisted and file STOP sources without creating or changing them."""
    now = _aware(now)
    if not isinstance(stop_files, (list, tuple)) or any(
            not isinstance(item, (str, os.PathLike)) for item in stop_files):
        raise ValueError('stop_filesはpathのlistまたはtupleで指定してください')
    try:
        path = Path(state_path).absolute()
    except (TypeError, ValueError, OSError):
        raise ValueError('state_pathを正しく指定してください')
    sidecars = [Path(str(path) + suffix) for suffix in
                ('-wal', '-shm', '-journal', '.wal', '.shm')]
    try:
        if not _exists(path):
            return _unknown(now, 'STATE_DB_MISSING')
        if _is_link(path) or not path.is_file():
            return _unknown(now, 'STATE_DB_UNSAFE')
        if any(_exists(candidate) for candidate in sidecars):
            return _unknown(now, 'STATE_DB_SIDECAR')
        before = _fingerprint(path)
        persistent, event_at = _read_persistent(path, now)
        after = _fingerprint(path)
        if before != after:
            return _unknown(now, 'STATE_DB_CHANGED')
        if any(_exists(candidate) for candidate in sidecars):
            return _unknown(now, 'STATE_DB_SIDECAR')
    except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
        return _unknown(now, 'STATE_DB_INVALID')

    try:
        first_files = _stop_file_states(stop_files)
        second_files = _stop_file_states(stop_files)
    except (OSError, ValueError, TypeError):
        return _unknown(now, 'STOP_FILE_READ_FAILED')
    if first_files != second_files:
        return _unknown(now, 'STOP_FILE_CHANGED')
    if any(item[2] for item in first_files):
        return _unknown(now, 'STOP_FILE_UNSAFE')
    try:
        if any(_exists(candidate) for candidate in sidecars):
            return _unknown(now, 'STATE_DB_SIDECAR')
        if _fingerprint(path) != before:
            return _unknown(now, 'STATE_DB_CHANGED')
    except OSError:
        return _unknown(now, 'STATE_DB_INVALID')
    file_stop = any(item[1] for item in first_files)
    file_reasons = ['STOP_FILE'] if file_stop else []
    reasons = (['PERSISTENT_STOP'] if persistent else []) + file_reasons
    effective = persistent or file_stop
    return _result(now, 'STOPPED' if effective else 'CLEAR', True, effective,
                   persistent, reasons, event_at)
