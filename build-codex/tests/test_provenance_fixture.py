"""Independent contracts for the isolated legacy provenance fixture prototype."""
import json
import socket
from datetime import date, datetime, timezone

import pytest

import aitrader.provenance_fixture as provenance_fixture
from aitrader.db import connect, init
from aitrader.provenance_fixture import ingest_legacy_fixture_with_provenance


START = END = date(2026, 8, 31)
RECORDED = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def fixture(close=105, *, volume=10000):
    return {
        "daily_quotes": [dict(Code="1300", Date="2026-08-31", Open=100,
                              High=110, Low=90, Close=close, Volume=volume,
                              TurnoverValue=1000000, AdjustmentFactor=1)],
        "weekly_margin_interest": [dict(Code="1300", Date="2026-08-28",
                                         LongMarginTradeVolume=100,
                                         ShortMarginTradeVolume=50)],
        "info": [dict(Code="1300", CompanyName="Fixture", MarketCode="0111",
                      Sector33Code="10", ListedDate="2020-01-01")],
        "topix": [dict(Date="2026-08-31", Close=2000)],
        "trading_calendar": [dict(Date="2026-08-31", HolidayDivision="1")],
    }


def ingest(home, value):
    return ingest_legacy_fixture_with_provenance(
        home, start=START, end=END, recorded_at=RECORDED, fixture=value)


def snapshot(home):
    with connect(home) as con:
        tables = [row[0] for row in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY table_name"
        ).fetchall()]
        result = {}
        for table in tables:
            columns = con.execute(f"PRAGMA table_info('{table}')").fetchall()
            projection = ",".join(
                f'CAST("{name}" AS VARCHAR)' if "TIMESTAMP" in kind.upper()
                else f'"{name}"'
                for _, name, kind, *_ in columns
            )
            result[table] = con.execute(
                f"SELECT {projection} FROM {table} ORDER BY ALL"
            ).fetchall()
        return result


def test_first_ingest_records_canonical_rows_and_current_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network access is forbidden")))
    home = tmp_path / "fixture-home"
    result = ingest(home, fixture())
    assert result["result"] == "COMPLETED"
    assert result["run_id"].startswith("mprov1:")
    assert set(result["tables"]) == {
        "listed", "prices_daily", "margin_weekly", "index_daily", "calendar"
    }
    assert set(result["tables"].values()) == {1}

    with connect(home) as con:
        assert dict(con.execute("SELECT key,value FROM provenance").fetchall()) == {
            "data_mode": "provenance_fixture",
            "market_provenance_schema": "1",
            "market_input_contract": "legacy-v1-unverified-fixture",
        }
        rows = con.execute(
            "SELECT table_name,row_key_json,normalized_row_sha256,normalized_row_json "
            "FROM market_ingest_run_rows ORDER BY table_name"
        ).fetchall()
        assert len(rows) == 5
        for table, key, digest, raw in rows:
            assert json.loads(key)
            decoded = json.loads(raw)
            assert decoded["table"] == table
            assert len(digest) == 64
        current = con.execute(
            "SELECT table_name,run_id FROM market_row_provenance ORDER BY table_name"
        ).fetchall()
        assert len(current) == 5
        assert {run_id for _, run_id in current} == {result["run_id"]}


def test_same_normalized_batch_is_deterministic_no_op(tmp_path):
    home = tmp_path / "fixture-home"
    first = ingest(home, fixture())
    before = snapshot(home)
    second = ingest(home, fixture())
    assert second["result"] == "NO_OP"
    assert second["run_id"] == first["run_id"]
    assert snapshot(home) == before


def test_correction_creates_new_run_and_old_batch_cannot_roll_it_back(tmp_path):
    home = tmp_path / "fixture-home"
    old = ingest(home, fixture(close=105))
    corrected = ingest(home, fixture(close=107))
    assert corrected["result"] == "COMPLETED"
    assert corrected["run_id"] != old["run_id"]
    with connect(home) as con:
        assert con.execute("SELECT close FROM prices_daily").fetchone()[0] == 107
        assert con.execute("SELECT count(*) FROM market_ingest_runs").fetchone()[0] == 2
    replay = ingest(home, fixture(close=105))
    assert replay["result"] == "NO_OP"
    with connect(home) as con:
        assert con.execute("SELECT close FROM prices_daily").fetchone()[0] == 107
        current = con.execute(
            "SELECT DISTINCT run_id FROM market_row_provenance"
        ).fetchall()
        assert current == [(corrected["run_id"],)]


def test_mid_write_failure_rolls_back_every_table_and_provenance(tmp_path, monkeypatch):
    home = tmp_path / "fixture-home"
    ingest(home, fixture())
    before = snapshot(home)
    original = provenance_fixture._insert_rows

    def fail_after_write(con, table, rows):
        original(con, table, rows)
        if table == "margin_weekly":
            raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(provenance_fixture, "_insert_rows", fail_after_write)
    with pytest.raises(RuntimeError, match="injected transaction failure"):
        ingest(home, fixture(close=109))
    assert snapshot(home) == before


@pytest.mark.parametrize("mutation", [
    lambda value: value.pop("topix"),
    lambda value: value.__setitem__("topix", []),
    lambda value: value["daily_quotes"][0].update(High=float("inf")),
    lambda value: value["daily_quotes"][0].update(AdjustmentOpen=1),
    lambda value: value["daily_quotes"][0].update(Close=True),
])
def test_invalid_fixture_is_rejected_before_fresh_home_side_effect(tmp_path, mutation):
    home = tmp_path / "fixture-home"
    value = fixture()
    mutation(value)
    with pytest.raises((ValueError, KeyError)):
        ingest(home, value)
    assert not home.exists()


def test_finite_numeric_strings_and_nullable_volume_are_preserved(tmp_path):
    home = tmp_path / "fixture-home"
    value = fixture(close="105.5", volume=None)
    value["weekly_margin_interest"][0]["LongMarginTradeVolume"] = "100.5"
    value["topix"][0]["Close"] = "2000.5"
    ingest(home, value)
    with connect(home) as con:
        assert con.execute(
            "SELECT close,volume FROM prices_daily"
        ).fetchone() == (105.5, None)
        assert con.execute(
            "SELECT long_balance FROM margin_weekly"
        ).fetchone()[0] == 100.5
        assert con.execute("SELECT close FROM index_daily").fetchone()[0] == 2000.5


def test_ordinary_or_unknown_existing_home_is_rejected_unchanged(tmp_path):
    ordinary = tmp_path / "ordinary"
    init(ordinary)
    before = snapshot(ordinary)
    with pytest.raises(ValueError):
        ingest(ordinary, fixture())
    assert snapshot(ordinary) == before

    unknown = tmp_path / "unknown"
    unknown.mkdir()
    sentinel = unknown / "keep.txt"
    sentinel.write_bytes(b"keep")
    with pytest.raises(ValueError):
        ingest(unknown, fixture())
    assert sentinel.read_bytes() == b"keep"
