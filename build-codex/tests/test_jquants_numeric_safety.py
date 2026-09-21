"""J-Quants numeric fields are validated before any database mutation."""
import math
import warnings
from datetime import date

import pytest

from aitrader.db import connect
from aitrader.jquants import fetch
from test_jquants_ingestion_atomicity import Client, START, END, snapshot


FIELDS = [
    ('daily_quotes', 'Open'), ('daily_quotes', 'High'),
    ('daily_quotes', 'Low'), ('daily_quotes', 'Close'),
    ('daily_quotes', 'Volume'), ('daily_quotes', 'TurnoverValue'),
    ('daily_quotes', 'AdjustmentFactor'),
    ('weekly_margin_interest', 'LongMarginTradeVolume'),
    ('weekly_margin_interest', 'ShortMarginTradeVolume'),
    ('topix', 'Close'),
]
INVALID = [float('inf'), float('nan'), 'Infinity', True, 'not-a-number']


class MutatingClient(Client):
    def __init__(self, key, field, value, version=2):
        super().__init__(version)
        self.key, self.field, self.value = key, field, value

    def rows(self, endpoint, key, **params):
        rows = super().rows(endpoint, key, **params)
        if key == self.key:
            rows[0][self.field] = self.value
        return rows


def ingest(home, client):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        fetch(home, START, END, client)


@pytest.fixture
def existing(tmp_path):
    home = tmp_path/'market'
    ingest(home, Client(1))
    return home


@pytest.mark.parametrize('key,field', FIELDS)
@pytest.mark.parametrize('invalid', INVALID,
                         ids=['positive-inf', 'nan', 'string-infinity', 'bool', 'bad-string'])
def test_invalid_numeric_never_changes_any_saved_table(
        existing, key, field, invalid):
    before = snapshot(existing)
    with pytest.raises((ValueError, TypeError)):
        ingest(existing, MutatingClient(key, field, invalid))
    assert snapshot(existing) == before


@pytest.mark.parametrize('field', ['Open', 'High', 'Low', 'Close'])
def test_missing_ohlc_remains_rejected_without_mutation(existing, field):
    before = snapshot(existing)
    with pytest.raises((ValueError, TypeError)):
        ingest(existing, MutatingClient('daily_quotes', field, None))
    assert snapshot(existing) == before


class NumericStrings(Client):
    def rows(self, endpoint, key, **params):
        rows = super().rows(endpoint, key, **params)
        fields = {
            'daily_quotes': ('Open', 'High', 'Low', 'Close', 'Volume',
                             'TurnoverValue', 'AdjustmentFactor'),
            'weekly_margin_interest': ('LongMarginTradeVolume',
                                       'ShortMarginTradeVolume'),
            'topix': ('Close',),
        }.get(key, ())
        for field in fields:
            rows[0][field] = str(rows[0][field])
        return rows


def test_finite_numeric_strings_remain_accepted(existing):
    ingest(existing, NumericStrings(2))
    with connect(existing) as database:
        price = database.execute(
            'SELECT open,high,low,close,volume,turnover,adj_factor FROM prices_daily').fetchone()
        margin = database.execute(
            'SELECT long_balance,short_balance FROM margin_weekly').fetchone()
        index = database.execute('SELECT close FROM index_daily').fetchone()[0]
    assert all(isinstance(value, float) and math.isfinite(value)
               for value in (*price, *margin, index))


def test_nullable_volume_is_preserved_as_null_not_zero(existing):
    ingest(existing, MutatingClient('daily_quotes', 'Volume', None))
    with connect(existing) as database:
        volume = database.execute(
            "SELECT volume FROM prices_daily WHERE code='1300'").fetchone()[0]
    assert volume is None


class PartialAdjustment(Client):
    def rows(self, endpoint, key, **params):
        rows = super().rows(endpoint, key, **params)
        if key == 'daily_quotes':
            rows[0]['AdjustmentOpen'] = 9999
        return rows


def test_partial_adjustment_fields_keep_all_raw_ohlc(existing):
    ingest(existing, PartialAdjustment(2))
    with connect(existing) as database:
        values = database.execute(
            'SELECT open,high,low,close FROM prices_daily').fetchone()
    assert values == (102., 112., 92., 107.)


class CompleteAdjustment(Client):
    def rows(self, endpoint, key, **params):
        rows = super().rows(endpoint, key, **params)
        if key == 'daily_quotes':
            rows[0].update(AdjustmentOpen='201', AdjustmentHigh='211',
                           AdjustmentLow='191', AdjustmentClose='206')
        return rows


def test_complete_adjustment_fields_remain_selected(existing):
    ingest(existing, CompleteAdjustment(2))
    with connect(existing) as database:
        values = database.execute(
            'SELECT open,high,low,close FROM prices_daily').fetchone()
    assert values == (201., 211., 191., 206.)


def test_publication_date_fallback_remains_date_plus_four_days(existing):
    class NoPublication(Client):
        def rows(self, endpoint, key, **params):
            rows = super().rows(endpoint, key, **params)
            if key == 'weekly_margin_interest':
                rows[0].pop('PublishedDate', None)
                rows[0].pop('PublishDate', None)
            return rows

    ingest(existing, NoPublication(2))
    with connect(existing) as database:
        published = database.execute(
            'SELECT publish_date FROM margin_weekly').fetchone()[0]
    assert published == date(2026, 9, 1)
