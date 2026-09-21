"""Read-only and fail-closed boundaries for durable STOP observation."""
from contextlib import closing
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3

import pytest

from aitrader_ops.models import JST
from aitrader_ops.stop_status import inspect_stop_status


NOW = datetime(2026, 9, 8, 7, 10, tzinfo=JST)
SCHEMA = """
CREATE TABLE kv(name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE controls(seq INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
 at TEXT NOT NULL, event_id TEXT, reconciled_at TEXT, event_at TEXT);
"""


def make_db(tmp_path, controls=(), kv=()):
    path = tmp_path/'notification.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.executemany('INSERT INTO controls VALUES(?,?,?,?,?,?)', controls)
        db.executemany('INSERT INTO kv VALUES(?,?)', kv)
    return path


def stopped_db(tmp_path):
    at = NOW-timedelta(minutes=5)
    return make_db(tmp_path, [
        (1, 'STOP', at.isoformat(), 'stop-1', None, at.isoformat()),
    ], [
        ('stopped', 'true'),
        ('stopped_at', json.dumps(at.isoformat())),
        ('stopped_event_at', json.dumps(at.isoformat())),
    ])


def resumed_db(tmp_path):
    stop_at = NOW-timedelta(minutes=8)
    resume_at = NOW-timedelta(minutes=3)
    return make_db(tmp_path, [
        (1, 'STOP', stop_at.isoformat(), 'stop-1', None, stop_at.isoformat()),
        (2, 'RESUME', resume_at.isoformat(), 'resume-1', resume_at.isoformat(),
         resume_at.isoformat()),
    ], [
        ('stopped', 'false'),
        ('stopped_at', json.dumps(stop_at.isoformat())),
        ('stopped_event_at', json.dumps(stop_at.isoformat())),
    ])


def assert_unknown(result):
    assert result['status'] == 'UNKNOWN'
    assert result['known'] is False
    assert result['effective_stop'] is True
    assert result['persistent_stopped'] is None
    assert result['read_only'] is True


@pytest.mark.parametrize('factory,status,persistent', [
    (lambda p: make_db(p), 'CLEAR', False),
    (stopped_db, 'STOPPED', True),
    (resumed_db, 'CLEAR', False),
])
def test_known_persistent_states_are_read_only(tmp_path, factory, status, persistent):
    path = factory(tmp_path)
    before = path.read_bytes()

    result = inspect_stop_status(path, now=NOW)

    assert result['status'] == status
    assert result['known'] is True
    assert result['persistent_stopped'] is persistent
    assert result['effective_stop'] is (status == 'STOPPED')
    assert result['read_only'] is True
    assert path.read_bytes() == before
    assert not any(Path(str(path)+suffix).exists()
                   for suffix in ('-wal', '-shm', '-journal'))


def test_stop_file_is_or_composed_without_mutation(tmp_path):
    path = make_db(tmp_path)
    stop_file = tmp_path/'STOP'
    stop_file.write_text('manual stop', encoding='utf-8')
    db_before, stop_before = path.read_bytes(), stop_file.read_bytes()

    result = inspect_stop_status(path, now=NOW, stop_files=(stop_file,))

    assert result['status'] == 'STOPPED' and result['known'] is True
    assert result['persistent_stopped'] is False
    assert result['effective_stop'] is True
    assert 'STOP_FILE' in result['reasons']
    assert path.read_bytes() == db_before and stop_file.read_bytes() == stop_before


@pytest.mark.parametrize('raw', ['1', '"true"', 'null', '{}'])
def test_stopped_kv_is_not_truthiness_coerced(tmp_path, raw):
    at = NOW-timedelta(minutes=1)
    path = make_db(tmp_path, [
        (1, 'STOP', at.isoformat(), None, None, at.isoformat()),
    ], [('stopped', raw), ('stopped_at', json.dumps(at.isoformat())),
        ('stopped_event_at', json.dumps(at.isoformat()))])

    assert_unknown(inspect_stop_status(path, now=NOW))


@pytest.mark.parametrize('stopped,action', [('false', 'STOP'), ('true', 'RESUME')])
def test_history_and_kv_contradiction_is_unknown(tmp_path, stopped, action):
    stop_at = NOW-timedelta(minutes=2)
    rows = [(1, 'STOP', stop_at.isoformat(), None, None, stop_at.isoformat())]
    if action == 'RESUME':
        resume_at = NOW-timedelta(minutes=1)
        rows.append((2, 'RESUME', resume_at.isoformat(), None,
                     resume_at.isoformat(), resume_at.isoformat()))
    path = make_db(tmp_path, rows, [
        ('stopped', stopped), ('stopped_at', json.dumps(stop_at.isoformat())),
        ('stopped_event_at', json.dumps(stop_at.isoformat())),
    ])

    assert_unknown(inspect_stop_status(path, now=NOW))


def test_duplicate_stop_kv_rows_are_unknown_even_without_primary_key(tmp_path):
    path = tmp_path/'notification.sqlite'
    at = NOW-timedelta(minutes=1)
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE kv(name TEXT, value TEXT NOT NULL);
        CREATE TABLE controls(seq INTEGER PRIMARY KEY AUTOINCREMENT,
          action TEXT NOT NULL, at TEXT NOT NULL, event_id TEXT,
          reconciled_at TEXT, event_at TEXT);
        """)
        db.execute('INSERT INTO controls VALUES(?,?,?,?,?,?)',
                   (1, 'STOP', at.isoformat(), None, None, at.isoformat()))
        db.executemany('INSERT INTO kv VALUES(?,?)', [
            ('stopped', 'true'), ('stopped', 'false'),
            ('stopped_at', json.dumps(at.isoformat())),
            ('stopped_event_at', json.dumps(at.isoformat())),
        ])

    assert_unknown(inspect_stop_status(path, now=NOW))


@pytest.mark.parametrize('field,value', [
    ('at', 'broken'), ('event_at', 'broken'),
    ('stopped_at', 'broken'), ('stopped_event_at', 'broken'),
])
def test_broken_control_or_kv_time_is_unknown(tmp_path, field, value):
    at = NOW-timedelta(minutes=1)
    row = {'at': at.isoformat(), 'event_at': at.isoformat()}
    row[field] = value
    kv = {'stopped': 'true', 'stopped_at': json.dumps(at.isoformat()),
          'stopped_event_at': json.dumps(at.isoformat())}
    if field in kv:
        kv[field] = json.dumps(value)
    path = make_db(tmp_path, [
        (1, 'STOP', row['at'], None, None, row['event_at']),
    ], list(kv.items()))

    assert_unknown(inspect_stop_status(path, now=NOW))


@pytest.mark.parametrize('field', ['at', 'event_at', 'stopped_at', 'stopped_event_at'])
def test_future_control_or_kv_time_is_unknown(tmp_path, field):
    future = NOW+timedelta(seconds=1)
    at = NOW-timedelta(minutes=1)
    row = {'at': at.isoformat(), 'event_at': at.isoformat()}
    row[field] = future.isoformat()
    kv = {'stopped': 'true', 'stopped_at': json.dumps(at.isoformat()),
          'stopped_event_at': json.dumps(at.isoformat())}
    if field in kv:
        kv[field] = json.dumps(future.isoformat())
    path = make_db(tmp_path, [
        (1, 'STOP', row['at'], None, None, row['event_at']),
    ], list(kv.items()))

    assert_unknown(inspect_stop_status(path, now=NOW))


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal', '.wal', '.shm'])
def test_any_database_sidecar_forces_unknown_without_db_read(tmp_path, monkeypatch, suffix):
    path = stopped_db(tmp_path)
    Path(str(path)+suffix).write_bytes(b'ambiguous')
    from aitrader_ops import stop_status
    monkeypatch.setattr(stop_status, '_read_persistent',
                        lambda *a, **k: pytest.fail('DB read despite sidecar'))

    result = inspect_stop_status(path, now=NOW)

    assert_unknown(result)
    assert 'STATE_DB_SIDECAR' in result['reasons']


def test_state_db_symlink_is_rejected_before_target_read(tmp_path, monkeypatch):
    target_dir = tmp_path/'target'
    target_dir.mkdir()
    target = stopped_db(target_dir)
    link = tmp_path/'linked.sqlite'
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'symlink creation unavailable: {exc}')
    original_open = Path.open
    opened = []

    def observed(path, *args, **kwargs):
        opened.append(path.absolute())
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', observed)
    result = inspect_stop_status(link, now=NOW)

    assert_unknown(result)
    assert target.absolute() not in opened and link.absolute() not in opened


def test_reparse_attribute_rejects_state_before_read_without_link_privilege(
        tmp_path, monkeypatch):
    from types import SimpleNamespace
    import stat

    path = stopped_db(tmp_path).absolute()
    original_lstat, original_open = Path.lstat, Path.open
    opened = []

    def marked(path_value):
        value = original_lstat(path_value)
        if path_value.absolute() == path:
            return SimpleNamespace(st_mode=value.st_mode,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return value

    def observed(path_value, *args, **kwargs):
        opened.append(path_value.absolute())
        return original_open(path_value, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', marked)
    monkeypatch.setattr(Path, 'open', observed)
    result = inspect_stop_status(path, now=NOW)

    assert_unknown(result)
    assert path not in opened


def test_database_change_during_observation_is_unknown(tmp_path, monkeypatch):
    from aitrader_ops import stop_status

    path = stopped_db(tmp_path)
    original = stop_status._read_persistent

    def read_then_change(target, now):
        result = original(target, now)
        with target.open('ab') as stream:
            stream.write(b'changed-after-read')
        return result

    monkeypatch.setattr(stop_status, '_read_persistent', read_then_change)
    result = inspect_stop_status(path, now=NOW)

    assert_unknown(result)
    assert 'STATE_DB_CHANGED' in result['reasons']


def test_sidecar_created_during_database_read_forces_unknown(tmp_path, monkeypatch):
    from aitrader_ops import stop_status

    path = make_db(tmp_path)
    sidecar = Path(str(path)+'-wal')
    original = stop_status._read_persistent

    def read_then_add_sidecar(target, now):
        result = original(target, now)
        sidecar.write_bytes(b'appeared during observation')
        return result

    monkeypatch.setattr(stop_status, '_read_persistent', read_then_add_sidecar)
    result = inspect_stop_status(path, now=NOW)

    assert_unknown(result)
    assert 'STATE_DB_SIDECAR' in result['reasons']


def test_stop_file_created_after_its_first_check_forces_unknown(tmp_path, monkeypatch):
    path = make_db(tmp_path)
    first, second = tmp_path/'STOP-first', tmp_path/'STOP-second'
    original_lstat = Path.lstat
    inserted = False

    def racing_lstat(candidate):
        nonlocal inserted
        if candidate.absolute() == second.absolute() and not inserted:
            first.write_text('appeared during observation', encoding='utf-8')
            inserted = True
        return original_lstat(candidate)

    monkeypatch.setattr(Path, 'lstat', racing_lstat)
    result = inspect_stop_status(path, now=NOW, stop_files=(first, second))

    assert inserted
    assert_unknown(result)


def test_database_change_while_stop_files_are_checked_forces_unknown(tmp_path, monkeypatch):
    path = make_db(tmp_path)
    stop_file = tmp_path/'STOP-race'
    original_lstat = Path.lstat
    changed = False

    def mutate_database(candidate):
        nonlocal changed
        if candidate.absolute() == stop_file.absolute() and not changed:
            at = NOW-timedelta(seconds=1)
            with closing(sqlite3.connect(path)) as db:
                db.execute('INSERT INTO controls VALUES(?,?,?,?,?,?)',
                           (1, 'STOP', at.isoformat(), None, None, at.isoformat()))
                db.executemany('INSERT INTO kv VALUES(?,?)', [
                    ('stopped', 'true'),
                    ('stopped_at', json.dumps(at.isoformat())),
                    ('stopped_event_at', json.dumps(at.isoformat())),
                ])
                db.commit()
            changed = True
        return original_lstat(candidate)

    monkeypatch.setattr(Path, 'lstat', mutate_database)
    result = inspect_stop_status(path, now=NOW, stop_files=(stop_file,))

    assert changed
    assert_unknown(result)
    assert 'STATE_DB_CHANGED' in result['reasons']


def test_read_failure_is_unknown_and_does_not_create_files(tmp_path, monkeypatch):
    path = stopped_db(tmp_path)
    before = set(tmp_path.iterdir())
    original_open = Path.open

    def denied(path_value, *args, **kwargs):
        if path_value.absolute() == path.absolute():
            raise PermissionError('injected read denial')
        return original_open(path_value, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', denied)
    result = inspect_stop_status(path, now=NOW)

    assert_unknown(result)
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize('stop_files', [None, 'STOP', [object()]])
def test_invalid_stop_file_collection_is_rejected_before_observation(tmp_path, stop_files):
    path = make_db(tmp_path)

    with pytest.raises(ValueError):
        inspect_stop_status(path, now=NOW, stop_files=stop_files)
