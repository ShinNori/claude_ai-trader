"""Atomic J-Quants ingestion into all five market tables and provenance."""
from datetime import date
import warnings

import pytest

import aitrader.jquants as jquants
from aitrader.db import connect


START = date(2026, 8, 1)
END = date(2026, 8, 31)
TABLES = ('listed', 'prices_daily', 'margin_weekly', 'index_daily',
          'calendar', 'provenance')


class Client:
    def __init__(self, version=1, fail_key=None, malformed=None):
        self.version = version
        self.fail_key = fail_key
        self.malformed = malformed or {}

    def rows(self, endpoint, key, **params):
        if key == self.fail_key:
            raise RuntimeError('offline late endpoint failure')
        if key in self.malformed:
            return self.malformed[key]
        value = self.version
        rows = {
            'daily_quotes': [dict(
                Code='1300', Date='2026-08-31', Open=100+value,
                High=110+value, Low=90+value, Close=105+value,
                Volume=10000+value, TurnoverValue=1000000+value,
                AdjustmentFactor=1)],
            'weekly_margin_interest': [dict(
                Code='1300', Date='2026-08-28', PublishedDate='2026-09-01',
                LongMarginTradeVolume=100+value,
                ShortMarginTradeVolume=50+value)],
            'info': [dict(Code='1300', CompanyName=f'Fixture-{value}',
                          MarketCode='0111', Sector33Code='10',
                          ListedDate='2020-01-06')],
            'topix': [dict(Date='2026-08-31', Close=2000+value)],
            'trading_calendar': [dict(Date='2026-08-31',
                                      HolidayDivision='1' if value == 1 else '0')],
        }
        return rows[key]


def ingest(home, client):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        jquants.fetch(home, START, END, client)


def snapshot(home):
    with connect(home) as database:
        return {table: database.execute(
            f'SELECT * FROM {table} ORDER BY ALL').fetchall() for table in TABLES}


@pytest.fixture
def existing(tmp_path):
    home = tmp_path/'market'
    ingest(home, Client(1))
    return home


def test_corrected_ingestion_updates_all_rows_and_repeat_is_idempotent(existing):
    before = snapshot(existing)
    ingest(existing, Client(2))
    corrected = snapshot(existing)
    assert corrected != before
    for table in TABLES:
        assert len(corrected[table]) == len(before[table])
    ingest(existing, Client(2))
    assert snapshot(existing) == corrected


class FailingConnection:
    def __init__(self, connection, needle):
        self.connection = connection
        self.needle = needle
        self.injected = False

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, *args):
        return self.connection.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, *args, **kwargs):
        if self.needle in sql and not self.injected:
            self.injected = True
            raise RuntimeError('offline injected insert failure')
        return self.connection.execute(sql, *args, **kwargs)


@pytest.mark.parametrize('needle', [
    'INSERT OR REPLACE INTO listed',
    'INSERT OR REPLACE INTO prices_daily',
    'INSERT OR REPLACE INTO margin_weekly',
    'INSERT OR REPLACE INTO index_daily',
    'INSERT OR REPLACE INTO calendar',
    "INSERT OR REPLACE INTO provenance VALUES ('data_mode'",
])
def test_failure_during_any_table_or_provenance_insert_rolls_back_everything(
        existing, monkeypatch, needle):
    before = snapshot(existing)
    real_connect = jquants.connect
    calls = 0
    wrapper = None

    def connect_with_failure(home):
        nonlocal calls, wrapper
        calls += 1
        connection = real_connect(home)
        if calls == 2:
            wrapper = FailingConnection(connection, needle)
            return wrapper
        return connection

    monkeypatch.setattr(jquants, 'connect', connect_with_failure)
    with pytest.raises(RuntimeError, match='offline injected insert failure'):
        ingest(existing, Client(2))
    assert wrapper is not None and wrapper.injected
    assert snapshot(existing) == before


@pytest.mark.parametrize('fail_key', ['info', 'topix', 'trading_calendar'])
def test_late_client_failure_leaves_existing_database_unchanged(existing, fail_key):
    before = snapshot(existing)
    with pytest.raises(RuntimeError, match='offline late endpoint failure'):
        ingest(existing, Client(2, fail_key=fail_key))
    assert snapshot(existing) == before


@pytest.mark.parametrize('malformed', [
    {'daily_quotes': [dict(Code='1300', Date='2026-08-31')]},
    {'info': [dict(Code='1300')]},
    {'trading_calendar': [dict(Date='2026-08-31')]},
])
def test_conversion_failure_leaves_existing_database_unchanged(existing, malformed):
    before = snapshot(existing)
    with pytest.raises((KeyError, ValueError, TypeError)):
        ingest(existing, Client(2, malformed=malformed))
    assert snapshot(existing) == before
