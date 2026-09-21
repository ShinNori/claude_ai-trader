"""Independent read-only inspection tests for existing market databases."""
import hashlib
from datetime import date, timedelta
from pathlib import Path

import pytest

import aitrader.market_inspection as inspection
from aitrader.db import init, connect


AS_OF = date(2026, 9, 4)


def hashes(home):
    return {str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in home.rglob('*') if path.is_file()}


@pytest.fixture
def market(tmp_path):
    home = tmp_path/'market'
    init(home)
    with connect(home) as database:
        database.execute('INSERT INTO listed VALUES (?,?,?,?,?)',
                         ['6857', 'Fixture', 'prime', '10', date(2020, 1, 6)])
        database.execute('INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)',
                         ['6857', AS_OF, 1000., 1010., 990., 1005., 10000., 1e7, 1.])
        database.execute('INSERT INTO margin_weekly VALUES (?,?,?,?,?)',
                         ['6857', AS_OF, AS_OF, 100., 10.])
        database.execute('INSERT INTO index_daily VALUES (?,?,?)',
                         ['TOPIX', AS_OF, 2000.])
        database.executemany('INSERT INTO calendar VALUES (?,?)',
                             [(AS_OF, True), (AS_OF+timedelta(days=3), True)])
        database.execute("INSERT INTO provenance VALUES ('data_mode','synthetic')")
    return home


def test_normal_inspection_is_repeatable_read_only_and_external_access_disabled(
        market, monkeypatch):
    original = inspection.duckdb.connect
    calls = []

    def observed(*args, **kwargs):
        calls.append((args, kwargs.copy()))
        return original(*args, **kwargs)

    monkeypatch.setattr(inspection.duckdb, 'connect', observed)
    before = hashes(market)
    first = inspection.inspect_market_inputs(market, as_of=AS_OF)
    second = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert hashes(market) == before
    assert first == second
    assert first['status'] == 'INSPECTED'
    assert first['source'] == 'synthetic'
    assert first['observation_unchanged'] is True
    assert first['observation_atomic'] is False
    assert first['read_only'] is True
    assert first['current_signal'] is False
    assert first['ready_for_live'] is False
    assert set(first['tables']) == {'listed', 'prices_daily', 'margin_weekly',
                                    'index_daily', 'calendar'}
    assert all(item['rows'] > 0 for item in first['tables'].values())
    assert all(item['scope'] == 'SAVED_ALL_ROWS' for item in first['tables'].values())
    assert calls and all(call[1]['read_only'] is True for call in calls)
    assert all(call[1]['config'] == {
        'enable_external_access': 'false',
        'autoinstall_known_extensions': 'false',
        'autoload_known_extensions': 'false'} for call in calls)


@pytest.mark.parametrize('kind', ['missing-home', 'missing-db'])
def test_missing_input_is_unknown_and_never_created(tmp_path, kind):
    home = tmp_path/kind
    if kind == 'missing-db':
        home.mkdir()
    before_exists = home.exists()
    report = inspection.inspect_market_inputs(home, as_of=AS_OF)
    assert report['status'] == 'UNKNOWN'
    assert report['tables'] == {}
    assert home.exists() is before_exists
    assert not (home/'market.duckdb').exists()


@pytest.mark.parametrize('schema_kind', ['view', 'missing-column'])
def test_view_or_invalid_schema_is_unknown(market, schema_kind):
    with connect(market) as database:
        if schema_kind == 'view':
            database.execute('DROP TABLE calendar')
            database.execute('CREATE VIEW calendar AS SELECT current_date AS date, true AS is_business_day')
        else:
            database.execute('ALTER TABLE prices_daily DROP COLUMN turnover')
    report = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert report['status'] == 'UNKNOWN'
    assert report['reason'] == 'SCHEMA_UNVERIFIED'
    assert report['tables'] == {}


@pytest.mark.parametrize('unsafe_part', ['target', 'parent'])
def test_target_or_parent_reparse_is_rejected_before_connect(
        market, monkeypatch, unsafe_part):
    database = market/'market.duckdb'
    unsafe = database if unsafe_part == 'target' else market
    original = inspection._is_link
    observed = False

    def injected(path, stat_result):
        nonlocal observed
        if Path(path) == unsafe:
            observed = True
            return True
        return original(path, stat_result)

    monkeypatch.setattr(inspection, '_is_link', injected)
    monkeypatch.setattr(inspection.duckdb, 'connect',
                        lambda *a, **k: pytest.fail('unsafe path reached database connect'))
    report = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert observed
    assert report['status'] == 'UNKNOWN'
    assert report['tables'] == {}


def test_dropbox_home_is_rejected_before_read_or_creation(tmp_path, monkeypatch):
    home = tmp_path/'Dropbox'/'market'
    monkeypatch.setattr(inspection, '_fingerprint',
                        lambda *a: pytest.fail('Dropbox path reached file read'))
    report = inspection.inspect_market_inputs(home, as_of=AS_OF)
    assert report['status'] == 'UNKNOWN'
    assert report['reason'] == 'DROPBOX_HOME_REJECTED'
    assert not home.exists()


@pytest.mark.parametrize('suffix', ['.wal', '.tmp'])
def test_sidecar_is_rejected_before_database_connect(market, monkeypatch, suffix):
    sidecar = Path(str(market/'market.duckdb') + suffix)
    sidecar.write_bytes(b'live-state')
    monkeypatch.setattr(inspection.duckdb, 'connect',
                        lambda *a, **k: pytest.fail('sidecar reached database connect'))
    report = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert report['status'] == 'UNKNOWN'
    assert report['reason'] == 'DATABASE_SIDECAR_PRESENT'
    assert report['tables'] == [] or report['tables'] == {}
    assert sidecar.read_bytes() == b'live-state'


def test_database_change_during_observation_hides_all_aggregates(market, monkeypatch):
    original = inspection._fingerprint
    calls = 0

    def changing(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            with connect(market) as database:
                database.execute("UPDATE listed SET name='Changed' WHERE code='6857'")
        return original(path)

    monkeypatch.setattr(inspection, '_fingerprint', changing)
    report = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert calls == 2
    assert report['status'] == 'UNKNOWN'
    assert report['reason'] == 'OBSERVATION_CHANGED'
    assert report['tables'] == {}


@pytest.mark.parametrize('statement,warning', [
    ("UPDATE prices_daily SET open='NaN'", 'NONFINITE_NUMERIC_PRESENT'),
    ("UPDATE index_daily SET close='Infinity'", 'NONFINITE_NUMERIC_PRESENT'),
    ('UPDATE prices_daily SET open=NULL', 'REQUIRED_VALUE_MISSING'),
    ('UPDATE prices_daily SET volume=NULL', 'REQUIRED_VALUE_MISSING'),
    ('UPDATE prices_daily SET close=0', 'NONPOSITIVE_PRICE_PRESENT'),
    ('UPDATE prices_daily SET high=900, low=1100', 'OHLC_RANGE_INVALID'),
    ('UPDATE listed SET listed_date=NULL', 'LISTED_DATE_MISSING'),
    ('UPDATE margin_weekly SET publish_date=NULL', 'PUBLICATION_DATE_MISSING'),
    ('UPDATE calendar SET is_business_day=NULL WHERE date=(SELECT min(date) FROM calendar)',
     'CALENDAR_VALUE_INVALID'),
])
def test_data_quality_warnings(market, statement, warning):
    with connect(market) as database:
        database.execute(statement)
    report = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert report['status'] == 'INSPECTED'
    assert warning in report['warnings']


def test_unknown_provenance_is_reported_without_claiming_source(market):
    with connect(market) as database:
        database.execute("UPDATE provenance SET value='mystery' WHERE key='data_mode'")
    report = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert report['status'] == 'INSPECTED'
    assert report['source'] == 'UNKNOWN'
    assert 'PROVENANCE_INCOMPLETE' in report['warnings']


def test_legacy_jquants_source_is_explicitly_unverified(market):
    with connect(market) as database:
        database.execute("UPDATE provenance SET value='jquants' WHERE key='data_mode'")
        database.execute("INSERT INTO provenance VALUES ('api_contract','legacy-v1-unverified')")
    report = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert report['source'] == 'jquants_legacy_unverified'
    assert 'PUBLICATION_ESTIMATE_HISTORY_UNVERIFIED' in report['warnings']
    assert report['ready_for_live'] is False


def test_future_market_rows_are_warned(market):
    with connect(market) as database:
        database.execute('INSERT INTO index_daily VALUES (?,?,?)',
                         ['TOPIX', AS_OF+timedelta(days=1), 2001.])
    report = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert 'FUTURE_MARKET_ROWS' in report['warnings']


def test_missing_as_of_price_is_warned(market):
    with connect(market) as database:
        database.execute('UPDATE prices_daily SET date=? WHERE date=?',
                         [AS_OF-timedelta(days=1), AS_OF])
    report = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert 'AS_OF_PRICE_MISSING' in report['warnings']


@pytest.mark.parametrize('missing,warning', [
    ('as-of', 'CALENDAR_AS_OF_MISSING'),
    ('next', 'CALENDAR_NEXT_SESSION_MISSING'),
])
def test_calendar_coverage_warnings(market, missing, warning):
    with connect(market) as database:
        if missing == 'as-of':
            database.execute('DELETE FROM calendar WHERE date=?', [AS_OF])
        else:
            database.execute('DELETE FROM calendar WHERE date>?', [AS_OF])
    report = inspection.inspect_market_inputs(market, as_of=AS_OF)
    assert warning in report['warnings']


def test_future_as_of_does_not_claim_calendar_coverage(market):
    future = AS_OF + timedelta(days=30)
    report = inspection.inspect_market_inputs(market, as_of=future)
    assert 'CALENDAR_AS_OF_MISSING' in report['warnings']
    assert 'CALENDAR_NEXT_SESSION_MISSING' in report['warnings']
