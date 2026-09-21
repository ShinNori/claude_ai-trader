"""Ingestion keeps the original failure when best-effort rollback also fails."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pytest

from aitrader import api, jquants


class _Connection:
    def __init__(self, original, *, fail_insert):
        self.original = original
        self.fail_insert = fail_insert

    def execute(self, sql, params=None):
        if sql == "ROLLBACK":
            raise RuntimeError("secondary rollback failure")
        if self.fail_insert and sql.startswith("INSERT"):
            raise self.original
        return self

    def register(self, name, frame):
        pass

    def unregister(self, name):
        pass


def _connections(original):
    calls = 0

    @contextmanager
    def connect(home):
        nonlocal calls
        calls += 1
        yield _Connection(original, fail_insert=calls == 2)

    return connect


def test_load_synthetic_preserves_original_failure(monkeypatch, tmp_path):
    original = RuntimeError("original synthetic failure")
    monkeypatch.setattr(api, "init_db", lambda home: None)
    monkeypatch.setattr(api.db, "require_data_mode", lambda con, mode: None)
    monkeypatch.setattr(api.db, "connect", _connections(original))

    with pytest.raises(RuntimeError) as found:
        api.load_synthetic(tmp_path)
    assert found.value is original


class _Client:
    def rows(self, endpoint, key, **params):
        return {
            "daily_quotes": [{"Code": "1", "Date": "2026-09-11", "Open": 1,
                              "High": 1, "Low": 1, "Close": 1,
                              "Volume": 1, "TurnoverValue": 1}],
            "weekly_margin_interest": [{"Code": "1", "Date": "2026-09-07",
                                        "PublishedDate": "2026-09-11",
                                        "LongMarginTradeVolume": 1,
                                        "ShortMarginTradeVolume": 1}],
            "info": [{"Code": "1", "CompanyName": "fixture",
                      "MarketCode": "0111", "ListedDate": "2020-01-01"}],
            "topix": [{"Date": "2026-09-11", "Close": 1}],
            "trading_calendar": [{"Date": "2026-09-11", "HolidayDivision": "1"}],
        }[key]


def test_jquants_fetch_preserves_original_failure(monkeypatch, tmp_path):
    original = RuntimeError("original jquants failure")
    monkeypatch.setattr(jquants, "init", lambda home: None)
    monkeypatch.setattr(jquants, "require_data_mode", lambda con, mode: None)
    monkeypatch.setattr(jquants, "connect", _connections(original))

    with pytest.raises(RuntimeError) as found:
        jquants.fetch(tmp_path, date(2026, 9, 1), date(2026, 9, 11), _Client())
    assert found.value is original
