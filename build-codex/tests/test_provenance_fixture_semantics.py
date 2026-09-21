"""Semantic lineage checks must survive fully re-hashed database tampering."""
import hashlib
import json

import pytest

from aitrader.db import connect
from aitrader.provenance_fixture_inspection import inspect_provenance_fixture
from test_provenance_fixture import fixture, ingest


TABLES = ('listed', 'prices_daily', 'margin_weekly', 'index_daily', 'calendar')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def rehash_only_run(con, transform=lambda table, payload: payload):
    """Rewrite the sole run, its rows and current index as a coherent new run."""
    old_id, start, end, recorded_at = con.execute(
        'SELECT run_id,request_start,request_end,CAST(recorded_at AS VARCHAR) '
        'FROM market_ingest_runs'
    ).fetchone()
    source = con.execute(
        'SELECT table_name,row_key_json,normalized_row_json '
        'FROM market_ingest_run_rows WHERE run_id=? ORDER BY table_name,row_key_json',
        [old_id]).fetchall()
    tables = {name: [] for name in TABLES}
    rows = []
    for table, key, raw in source:
        payload = transform(table, json.loads(raw))
        if payload is None:
            continue
        raw = canonical(payload)
        tables[table].append(payload)
        rows.append((table, key, hashlib.sha256(raw.encode()).hexdigest(), raw,
                     payload['provenance']))
    envelope = {'schema_version': 1, 'contract': 'legacy-v1-unverified-fixture',
                'request_start': start.isoformat(), 'request_end': end.isoformat(),
                'tables': tables}
    digest = hashlib.sha256(canonical(envelope).encode()).hexdigest()
    new_id = 'mprov1:' + digest
    con.execute('DELETE FROM market_row_provenance')
    con.execute('DELETE FROM market_ingest_run_rows')
    con.execute('DELETE FROM market_ingest_runs')
    con.execute('INSERT INTO market_ingest_runs VALUES (?,?,?,?,?,?,?,?)',
                [new_id, 1, 'legacy-v1-unverified-fixture', start, end, digest,
                 recorded_at, 'COMPLETED'])
    for table, key, row_hash, raw, provenance in rows:
        con.execute('INSERT INTO market_ingest_run_rows VALUES (?,?,?,?,?)',
                    [new_id, table, key, row_hash, raw])
        con.execute('INSERT INTO market_row_provenance VALUES (?,?,?,?,?,?,?,?)',
                    [table, key, new_id, provenance['ohlc_basis'],
                     provenance['volume_basis'], provenance['turnover_basis'],
                     provenance['factor_basis'], provenance['publication_source']])


def assert_unknown_and_next_ingest_rejected(home):
    assert inspect_provenance_fixture(home)['status'] == 'UNKNOWN'
    with pytest.raises(ValueError):
        ingest(home, fixture(close=106))


def test_self_consistent_run_with_four_empty_tables_is_not_accepted(tmp_path):
    home = tmp_path/'fixture-home'
    ingest(home, fixture())
    with connect(home) as con:
        old_id, start, end, recorded_at = con.execute(
            'SELECT run_id,request_start,request_end,CAST(recorded_at AS VARCHAR) '
            'FROM market_ingest_runs'
        ).fetchone()
        table, key, raw = con.execute(
            "SELECT table_name,row_key_json,normalized_row_json "
            "FROM market_ingest_run_rows WHERE run_id=? AND table_name='listed'",
            [old_id]).fetchone()
        payload = json.loads(raw)
        tables = {name: [] for name in TABLES}
        tables['listed'] = [payload]
        envelope = {'schema_version': 1, 'contract': 'legacy-v1-unverified-fixture',
                    'request_start': start.isoformat(), 'request_end': end.isoformat(),
                    'tables': tables}
        digest = hashlib.sha256(canonical(envelope).encode()).hexdigest()
        sparse_id = 'mprov1:' + digest
        con.execute('INSERT INTO market_ingest_runs VALUES (?,?,?,?,?,?,?,?)',
                    [sparse_id, 1, 'legacy-v1-unverified-fixture', start, end,
                     digest, recorded_at, 'COMPLETED'])
        con.execute('INSERT INTO market_ingest_run_rows VALUES (?,?,?,?,?)',
                    [sparse_id, table, key,
                     hashlib.sha256(raw.encode()).hexdigest(), raw])
        assert con.execute('SELECT count(*) FROM market_ingest_runs').fetchone()[0] == 2
        assert con.execute('SELECT count(*) FROM market_row_provenance').fetchone()[0] == 5
        assert all(con.execute(f'SELECT count(*) FROM {name}').fetchone()[0] == 1
                   for name in TABLES)
    assert_unknown_and_next_ingest_rejected(home)


@pytest.mark.parametrize(('values_change', 'provenance_change', 'sql'), [
    ({'volume': None}, {'volume_basis': 'RAW'},
     'UPDATE prices_daily SET volume=NULL'),
    ({'volume': 10000.0}, {'volume_basis': 'MISSING'},
     'UPDATE prices_daily SET volume=10000'),
    ({'turnover': None}, {'turnover_basis': 'RAW'},
     'UPDATE prices_daily SET turnover=NULL'),
    ({'adj_factor': 2.0}, {'factor_basis': 'DEFAULT_ONE'},
     'UPDATE prices_daily SET adj_factor=2'),
])
def test_rehashed_price_lineage_contradictions_are_rejected(
        tmp_path, values_change, provenance_change, sql):
    home = tmp_path/'fixture-home'
    ingest(home, fixture())

    def transform(table, payload):
        if table == 'prices_daily':
            payload['values'].update(values_change)
            payload['provenance'].update(provenance_change)
        return payload

    with connect(home) as con:
        rehash_only_run(con, transform)
        con.execute(sql)
    assert_unknown_and_next_ingest_rejected(home)


def test_rehashed_date_plus_four_with_different_publish_date_is_rejected(tmp_path):
    home = tmp_path/'fixture-home'
    ingest(home, fixture())

    def transform(table, payload):
        if table == 'margin_weekly':
            payload['values']['publish_date'] = '2026-09-05'
        return payload

    with connect(home) as con:
        rehash_only_run(con, transform)
        con.execute("UPDATE margin_weekly SET publish_date=DATE '2026-09-05'")
    assert_unknown_and_next_ingest_rejected(home)


def test_valid_nullable_bases_and_default_factor_remain_accepted(tmp_path):
    home = tmp_path/'fixture-home'
    value = fixture(volume=None)
    value['daily_quotes'][0]['TurnoverValue'] = None
    value['daily_quotes'][0].pop('AdjustmentFactor')
    ingest(home, value)
    result = inspect_provenance_fixture(home)
    assert result['status'] == 'VERIFIED_FIXTURE'
    with connect(home) as con:
        assert con.execute(
            "SELECT volume_basis,turnover_basis,factor_basis "
            "FROM market_row_provenance WHERE table_name='prices_daily'"
        ).fetchone() == ('MISSING', 'MISSING', 'DEFAULT_ONE')
