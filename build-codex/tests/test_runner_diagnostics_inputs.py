"""Malformed diagnostic input must fail conservatively without writing files."""
import hashlib
import sqlite3
from contextlib import closing

import pytest

from test_runner import setup, DAY, NOW, RunError
from aitrader.runner_diagnostics import diagnose_mock_run


def hashes(home):
    return {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in home.rglob('*') if p.is_file()}


def assert_refused_unchanged(home, run_id='run1', day=DAY):
    before = hashes(home)
    try:
        result = diagnose_mock_run(home, run_id, day)
    except RunError:
        pass
    else:
        assert result['classification'] in ('MISSING', 'CONFLICT')
        assert result['repaired'] is False
        assert result['automatic_resume_allowed'] is False
    finally:
        assert hashes(home) == before


@pytest.mark.parametrize('run_id', [None, 1, [], '../other', 'a/b', '', 'x'*81])
def test_invalid_run_id_does_not_create_home(tmp_path, run_id):
    home = tmp_path/'absent'
    assert_refused_unchanged(home, run_id=run_id)
    assert not home.exists()


@pytest.mark.parametrize('day', [NOW, '2026-09-08', None, 42])
def test_invalid_execution_day_does_not_create_home(tmp_path, day):
    home = tmp_path/'absent'
    assert_refused_unchanged(home, day=day)
    assert not home.exists()


@pytest.mark.parametrize('marker', [None, 'not-json', '[]', '{"mode":"live","version":1}'])
def test_missing_or_invalid_marker_is_read_only(tmp_path, marker):
    home = tmp_path/'home'
    home.mkdir()
    if marker is not None:
        (home/'mock-runner.json').write_text(marker, encoding='utf-8')
    assert_refused_unchanged(home)


@pytest.mark.parametrize('database', ['ledger.sqlite', 'orchestration.sqlite'])
def test_corrupt_database_is_read_only_conflict(tmp_path, database):
    home = tmp_path/'home'
    home.mkdir()
    (home/'mock-runner.json').write_text('{"mode":"mock","version":1}', encoding='utf-8')
    for name in ('ledger.sqlite', 'orchestration.sqlite'):
        if name == database:
            (home/name).write_bytes(b'not sqlite')
        else:
            with closing(sqlite3.connect(home/name)):
                pass
    assert_refused_unchanged(home)


@pytest.mark.parametrize('value', ['[]', 'null', '42', '"text"'])
def test_non_mapping_manifest_is_read_only_conflict(setup, value):
    home, execute = setup
    execute()
    with closing(sqlite3.connect(home/'orchestration.sqlite')) as con:
        con.execute('UPDATE runs SET manifest=? WHERE id=?', [value, 'run1'])
        con.commit()
    assert_refused_unchanged(home)


@pytest.mark.parametrize('value', ['[]', 'null', '{"proposal":null}', 'not-json'])
def test_invalid_notice_payload_is_read_only_conflict(setup, value):
    home, execute = setup
    execute()
    with closing(sqlite3.connect(home/'ledger.sqlite')) as con:
        con.execute("UPDATE ledger_events SET payload=? WHERE kind='NOTICE_CREATED'", [value])
        con.commit()
    assert_refused_unchanged(home)
