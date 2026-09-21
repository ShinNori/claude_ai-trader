"""Confirmed legacy-v1 field mappings; every response is an in-process fixture."""
from datetime import date
import warnings

import pytest

from aitrader.db import connect
from aitrader.jquants import fetch


class MappingClient:
    def __init__(self, *, quote=None, margin=None):
        self.quote = quote or {
            'Code': '1300', 'Date': '2026-08-31', 'Open': 100,
            'High': 110, 'Low': 90, 'Close': 105, 'Volume': 1000,
            'TurnoverValue': 100000, 'AdjustmentFactor': 1,
        }
        self.margin = margin or {
            'Code': '1300', 'Date': '2026-08-28',
            'LongMarginTradeVolume': 100, 'ShortMarginTradeVolume': 50,
        }

    def rows(self, endpoint, key, **params):
        fixtures = {
            'daily_quotes': [self.quote],
            'weekly_margin_interest': [self.margin],
            'info': [{'Code': '1300', 'CompanyName': 'Fixture',
                      'MarketCode': '0111', 'Sector33Code': '10',
                      'ListedDate': '2000-01-01'}],
            'topix': [{'Date': '2026-08-31', 'Close': 2000}],
            'trading_calendar': [{'Date': '2026-08-31', 'HolidayDivision': '1'}],
        }
        return fixtures[key]


def ingest(home, client):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        fetch(home, date(2026, 8, 1), date(2026, 8, 31), client)


@pytest.mark.parametrize(
    ('extra', 'expected'),
    [({'PublishedDate': '2026-09-03', 'PublishDate': '2026-09-04'}, date(2026, 9, 3)),
     ({'PublishDate': '2026-09-04'}, date(2026, 9, 4)),
     ({}, date(2026, 9, 1))],
)
def test_margin_publication_date_prefers_explicit_columns_then_four_calendar_days(
        tmp_path, extra, expected):
    margin = dict(MappingClient().margin, **extra)
    ingest(tmp_path, MappingClient(margin=margin))
    with connect(tmp_path) as database:
        actual = database.execute(
            'SELECT publish_date FROM margin_weekly WHERE code=?', ['1300']).fetchone()[0]
    assert actual == expected


def test_complete_adjusted_ohlc_and_adjusted_volume_are_preferred(tmp_path):
    quote = dict(MappingClient().quote,
                 AdjustmentOpen=200, AdjustmentHigh=220,
                 AdjustmentLow=180, AdjustmentClose=210,
                 AdjustmentVolume=500, AdjustmentFactor=2)
    ingest(tmp_path, MappingClient(quote=quote))
    with connect(tmp_path) as database:
        actual = database.execute(
            'SELECT open,high,low,close,volume,adj_factor FROM prices_daily').fetchone()
    assert actual == (200, 220, 180, 210, 500, 2)


def test_raw_ohlc_volume_and_default_factor_remain_supported(tmp_path):
    quote = dict(MappingClient().quote)
    quote.pop('AdjustmentFactor')
    ingest(tmp_path, MappingClient(quote=quote))
    with connect(tmp_path) as database:
        actual = database.execute(
            'SELECT open,high,low,close,volume,adj_factor FROM prices_daily').fetchone()
    assert actual == (100, 110, 90, 105, 1000, 1)
