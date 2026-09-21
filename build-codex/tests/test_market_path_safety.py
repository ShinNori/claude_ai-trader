"""Runtime market database paths reject links without breaking normal homes."""
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

import aitrader.api as api
import aitrader.db as db
import aitrader.jquants as jquants


def test_fresh_existing_and_relative_home_remain_read_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with closing(db.connect('relative-market')) as connection:
        connection.execute('CREATE TABLE fixture(value INTEGER)')
        connection.execute('INSERT INTO fixture VALUES (7)')
    database = tmp_path/'relative-market'/'market.duckdb'
    assert database.is_file()
    with closing(db.connect('relative-market')) as connection:
        assert connection.execute('SELECT value FROM fixture').fetchone()[0] == 7
        connection.execute('INSERT INTO fixture VALUES (8)')
    with closing(db.connect(tmp_path/'relative-market')) as connection:
        assert connection.execute('SELECT sum(value) FROM fixture').fetchone()[0] == 15


def test_dropbox_path_is_rejected_before_mkdir_or_duckdb(tmp_path, monkeypatch):
    home = tmp_path/'Dropbox'/'forbidden'
    original_mkdir = Path.mkdir
    mkdir_called = False

    def observed_mkdir(self, *args, **kwargs):
        nonlocal mkdir_called
        if self == home:
            mkdir_called = True
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'mkdir', observed_mkdir)
    monkeypatch.setattr(db.duckdb, 'connect',
                        lambda *a, **k: pytest.fail('Dropbox reached DuckDB'))
    with pytest.raises(ValueError, match='Dropbox'):
        db.connect(home)
    assert mkdir_called is False
    assert not home.exists()


@pytest.mark.parametrize('unsafe_kind', ['parent', 'home', 'database', 'wal', 'tmp'])
def test_reparse_injection_is_rejected_before_duckdb(
        tmp_path, monkeypatch, unsafe_kind):
    home = tmp_path/'market'
    home.mkdir()
    database = home/'market.duckdb'
    paths = {
        'parent': home.parent,
        'home': home,
        'database': database,
        'wal': Path(str(database)+'.wal'),
        'tmp': Path(str(database)+'.tmp'),
    }
    unsafe = paths[unsafe_kind]
    if unsafe_kind in ('database', 'wal'):
        unsafe.write_bytes(b'fixture')
    elif unsafe_kind == 'tmp':
        unsafe.mkdir()
    original = db.os.path.isjunction
    observed = False

    def junction(path):
        nonlocal observed
        if Path(path) == unsafe:
            observed = True
            return True
        return original(path)

    monkeypatch.setattr(db.os.path, 'isjunction', junction)
    monkeypatch.setattr(db.duckdb, 'connect',
                        lambda *a, **k: pytest.fail('unsafe path reached DuckDB'))
    with pytest.raises(ValueError):
        db.connect(home)
    assert observed
    assert unsafe.exists()


@pytest.mark.parametrize('wrong_kind', ['wal-directory', 'tmp-file'])
def test_sidecar_wrong_type_is_rejected(tmp_path, monkeypatch, wrong_kind):
    home = tmp_path/'market'
    home.mkdir()
    database = home/'market.duckdb'
    if wrong_kind == 'wal-directory':
        Path(str(database)+'.wal').mkdir()
    else:
        Path(str(database)+'.tmp').write_bytes(b'wrong type')
    monkeypatch.setattr(db.duckdb, 'connect',
                        lambda *a, **k: pytest.fail('bad sidecar reached DuckDB'))
    with pytest.raises(ValueError):
        db.connect(home)


def test_normal_wal_file_and_tmp_directory_pass_runtime_guard(tmp_path, monkeypatch):
    home = tmp_path/'market'
    home.mkdir()
    database = home/'market.duckdb'
    database.write_bytes(b'normal database placeholder')
    Path(str(database)+'.wal').write_bytes(b'normal wal placeholder')
    Path(str(database)+'.tmp').mkdir()

    class FakeConnection:
        def execute(self, statement):
            assert statement == 'SET threads=1'
            return self

    called = []
    monkeypatch.setattr(db.duckdb, 'connect',
                        lambda path: called.append(path) or FakeConnection())
    assert isinstance(db.connect(home), FakeConnection)
    assert called == [str(database)]


def test_post_mkdir_guard_detects_database_type_race(tmp_path, monkeypatch):
    home = tmp_path/'new-home'
    original_guard = db._runtime_path_guard
    calls = 0

    def racing_guard(value):
        nonlocal calls
        result = original_guard(value)
        calls += 1
        if calls == 1:
            # A competing creator places the wrong leaf type after preflight.
            result.mkdir(parents=True, exist_ok=True)
            (result/'market.duckdb').mkdir()
        return result

    monkeypatch.setattr(db, '_runtime_path_guard', racing_guard)
    monkeypatch.setattr(db.duckdb, 'connect',
                        lambda *a, **k: pytest.fail('race reached DuckDB'))
    with pytest.raises(ValueError):
        db.connect(home)
    assert calls == 1  # second guard raises inside original_guard
    assert (home/'market.duckdb').is_dir()


@pytest.mark.parametrize('entry', ['init', 'load', 'fetch'])
def test_public_market_entrypoints_reject_dropbox_before_build_or_client(
        tmp_path, monkeypatch, entry):
    home = tmp_path/'Dropbox'/'forbidden'
    monkeypatch.setattr(api.importlib.util, 'spec_from_file_location',
                        lambda *a, **k: pytest.fail('shared build was loaded'))

    class Client:
        def rows(self, *args, **kwargs):
            pytest.fail('J-Quants client was reached')

    with pytest.raises(ValueError, match='Dropbox'):
        if entry == 'init':
            api.init_db(home)
        elif entry == 'load':
            api.load_synthetic(home, seed=42)
        else:
            jquants.fetch(home, date(2026, 9, 1), date(2026, 9, 2), Client())
    assert not home.exists()


@pytest.mark.parametrize('close_fails', [False, True])
def test_set_threads_failure_closes_connection_and_preserves_original_exception(
        tmp_path, monkeypatch, close_fails):
    original_error = RuntimeError('SET threads failed')

    class FakeConnection:
        def __init__(self):
            self.closed = False
        def execute(self, statement):
            assert statement == 'SET threads=1'
            raise original_error
        def close(self):
            self.closed = True
            if close_fails:
                raise OSError('secondary close failure')

    fake = FakeConnection()
    monkeypatch.setattr(db.duckdb, 'connect', lambda path: fake)
    with pytest.raises(RuntimeError) as caught:
        db.connect(tmp_path/'market')
    assert caught.value is original_error
    assert fake.closed is True


def test_duckdb_connect_failure_has_no_connection_to_close(tmp_path, monkeypatch):
    original_error = OSError('connect failed before object exists')
    calls = 0

    def fail(path):
        nonlocal calls
        calls += 1
        raise original_error

    monkeypatch.setattr(db.duckdb, 'connect', fail)
    with pytest.raises(OSError) as caught:
        db.connect(tmp_path/'market')
    assert caught.value is original_error
    assert calls == 1
