"""The lineage lab proves stored consistency only and never repairs data."""
from pathlib import Path
import hashlib
import json

import pytest
import duckdb

from aitrader.db import connect
from aitrader import provenance_fixture_inspection as inspection
from test_provenance_fixture_isolation import ingest


@pytest.fixture
def home(tmp_path):
    value = tmp_path/'lineage'
    ingest(value)
    return value


def assert_unknown(report):
    assert report['status'] == 'UNKNOWN'
    assert report['tables'] == {}
    assert report['runs'] is None
    assert report['estimated_current_rows'] is None
    assert report['read_only'] is True
    for key in ('ready_for_live', 'current_signal', 'automatic_resume_allowed',
                'observation_atomic', 'observation_unchanged'):
        assert report[key] is False


def test_valid_fixture_is_verified_without_file_changes(home):
    database = home/'market.duckdb'
    before, stamp = database.read_bytes(), database.stat().st_mtime_ns
    result = inspection.inspect_provenance_fixture(home)
    assert result['status'] == 'VERIFIED_FIXTURE'
    assert result['tables'] == dict.fromkeys(
        ('listed', 'prices_daily', 'margin_weekly', 'index_daily', 'calendar'), 1)
    assert result['runs'] == 1
    assert result['estimated_current_rows'] == 0
    assert result['fixture_only'] is True and result['read_only'] is True
    assert result['current_signal'] is False and result['ready_for_live'] is False
    assert result['automatic_resume_allowed'] is False
    assert result['observation_atomic'] is False
    assert (database.read_bytes(), database.stat().st_mtime_ns) == (before, stamp)
    assert {p.name for p in home.iterdir()} == {'market.duckdb'}


@pytest.mark.parametrize('statement', [
    'UPDATE prices_daily SET close=close+1',
    "UPDATE listed SET name='tampered'",
    'UPDATE calendar SET is_business_day=NOT is_business_day',
    "UPDATE provenance SET value='9' WHERE key='market_provenance_schema'",
    "UPDATE market_row_provenance SET ohlc_basis='BOGUS' WHERE table_name='prices_daily'",
    "DELETE FROM market_ingest_run_rows WHERE table_name='listed'",
    "INSERT INTO market_ingest_run_rows SELECT 'orphan',table_name,row_key_json,"
    "normalized_row_sha256,normalized_row_json FROM market_ingest_run_rows LIMIT 1",
])
def test_corruption_is_unknown_and_not_repaired(home, statement):
    with connect(home) as con:
        con.execute(statement)
    database = home/'market.duckdb'
    before = database.read_bytes()
    assert_unknown(inspection.inspect_provenance_fixture(home))
    assert database.read_bytes() == before


@pytest.mark.parametrize('statement', [
    'UPDATE market_ingest_runs SET recorded_at=NULL',
    "UPDATE market_ingest_runs SET status='FAILED'",
])
def test_run_constraints_reject_invalid_states(home, statement):
    with pytest.raises(duckdb.ConstraintException):
        with connect(home) as con:
            con.execute(statement)
    assert inspection.inspect_provenance_fixture(home)['status'] == 'VERIFIED_FIXTURE'


def test_views_are_rejected_even_when_they_have_matching_columns(home):
    with connect(home) as con:
        con.execute('ALTER TABLE listed RENAME TO saved_listed')
        con.execute('CREATE VIEW listed AS SELECT * FROM saved_listed')
    assert_unknown(inspection.inspect_provenance_fixture(home))


def test_empty_dedicated_schema_is_not_a_completed_fixture(home):
    with connect(home) as con:
        for table in ('listed', 'prices_daily', 'margin_weekly', 'index_daily',
                      'calendar', 'market_ingest_run_rows', 'market_row_provenance',
                      'market_ingest_runs'):
            con.execute(f'DELETE FROM {table}')
    assert_unknown(inspection.inspect_provenance_fixture(home))


@pytest.mark.parametrize('field,value', [('listed_date', 'not-a-date'), ('name', 42)])
def test_historical_invalid_types_are_rejected_even_with_matching_hashes(home, field, value):
    """Hash agreement alone must not validate malformed historical row types."""
    with connect(home) as con:
        old_id = con.execute('SELECT run_id FROM market_ingest_runs').fetchone()[0]
    ingest(home, version=2)
    canonical = lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False,
                                        separators=(',', ':'), allow_nan=False)
    with connect(home) as con:
        rows = con.execute('SELECT table_name,row_key_json,normalized_row_json '
                           'FROM market_ingest_run_rows WHERE run_id=? '
                           'ORDER BY table_name,row_key_json', [old_id]).fetchall()
        tables = {name: [] for name in (
            'listed', 'prices_daily', 'margin_weekly', 'index_daily', 'calendar')}
        replacements = []
        for table, key, text in rows:
            payload = json.loads(text)
            if table == 'listed':
                payload['values'][field] = value
            text = canonical(payload)
            tables[table].append(payload)
            replacements.append([table, key, hashlib.sha256(text.encode()).hexdigest(), text])
        envelope = {'schema_version': 1, 'contract': 'legacy-v1-unverified-fixture',
                    'request_start': '2026-08-01', 'request_end': '2026-08-31', 'tables': tables}
        digest = hashlib.sha256(canonical(envelope).encode()).hexdigest()
        altered_id = 'mprov1:'+digest
        con.execute('INSERT INTO market_ingest_runs '
                    'SELECT ?,schema_version,contract,request_start,request_end,?,recorded_at,status '
                    'FROM market_ingest_runs WHERE run_id=?', [altered_id, digest, old_id])
        con.execute('DELETE FROM market_ingest_run_rows WHERE run_id=?', [old_id])
        con.execute('DELETE FROM market_ingest_runs WHERE run_id=?', [old_id])
        con.executemany('INSERT INTO market_ingest_run_rows VALUES (?,?,?,?,?)',
                        [[altered_id, *row] for row in replacements])
    assert_unknown(inspection.inspect_provenance_fixture(home))
    with pytest.raises(ValueError):
        ingest(home, version=2)


def test_missing_home_is_not_created(tmp_path):
    missing = tmp_path/'absent'
    assert_unknown(inspection.inspect_provenance_fixture(missing))
    assert not missing.exists()


def test_writer_sidecar_blocks_database_open(home, monkeypatch):
    (home/'market.duckdb.wal').write_bytes(b'pending fixture')
    monkeypatch.setattr(inspection.duckdb, 'connect',
                        lambda *a, **k: pytest.fail('busy database was opened'))
    assert_unknown(inspection.inspect_provenance_fixture(home))
    assert (home/'market.duckdb.wal').read_bytes() == b'pending fixture'


def test_observation_change_hides_all_partial_results(home, monkeypatch):
    original = inspection._fingerprint
    calls = 0
    def changed(path):
        nonlocal calls
        calls += 1
        value = original(path)
        return value if calls == 1 else (*value[:-1], 'changed')
    monkeypatch.setattr(inspection, '_fingerprint', changed)
    assert_unknown(inspection.inspect_provenance_fixture(home))
    assert calls == 2


def test_raw_parent_reparse_is_unknown_before_open(home, monkeypatch):
    import os
    original = os.path.isjunction
    monkeypatch.setattr(os.path, 'isjunction',
                        lambda path: Path(path) == home.parent or original(path))
    monkeypatch.setattr(inspection.duckdb, 'connect',
                        lambda *a, **k: pytest.fail('unsafe database was opened'))
    assert_unknown(inspection.inspect_provenance_fixture(home))
