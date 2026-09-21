"""Existing provenance homes must retain the exact base and lineage schemas."""
from datetime import date, datetime, timezone

import pytest

import aitrader.provenance_fixture as module
from aitrader.db import connect
from test_provenance_fixture import fixture


def _ingest(home, value=None):
    return module.ingest_legacy_fixture_with_provenance(
        home, start=date(2026, 8, 31), end=date(2026, 8, 31),
        recorded_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        fixture=value or fixture(),
    )


def _snapshot(home):
    with connect(home) as con:
        objects = con.execute(
            "SELECT table_name,table_type FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()
        columns = con.execute(
            "SELECT table_name,column_name,data_type,ordinal_position "
            "FROM information_schema.columns WHERE table_schema='main' "
            "ORDER BY table_name,ordinal_position"
        ).fetchall()
        constraints = con.execute(
            "SELECT table_name,constraint_type,constraint_column_names "
            "FROM duckdb_constraints() WHERE schema_name='main' "
            "ORDER BY table_name,constraint_type"
        ).fetchall()
        rows = {}
        for name, kind in objects:
            if kind != "BASE TABLE":
                continue
            info = con.execute(f"PRAGMA table_info('{name}')").fetchall()
            projection = ",".join(
                f'CAST("{column}" AS VARCHAR)' if "TIMESTAMP" in dtype.upper()
                else f'"{column}"'
                for _, column, dtype, *_ in info
            )
            rows[name] = con.execute(
                f"SELECT {projection} FROM {name} ORDER BY ALL"
            ).fetchall()
        return objects, columns, constraints, rows


def _listed_view(con):
    con.execute("CREATE TABLE listed_saved AS SELECT * FROM listed")
    con.execute("DROP TABLE listed")
    con.execute("CREATE VIEW listed AS SELECT * FROM listed_saved")


def _listed_without_primary_key(con):
    con.execute("CREATE TABLE listed_replacement AS SELECT * FROM listed")
    con.execute("DROP TABLE listed")
    con.execute("ALTER TABLE listed_replacement RENAME TO listed")


def _listed_wrong_column_type(con):
    con.execute("ALTER TABLE listed ALTER COLUMN name TYPE INTEGER USING 1")


def _provenance_view(con):
    con.execute("CREATE TABLE provenance_saved AS SELECT * FROM provenance")
    con.execute("DROP TABLE provenance")
    con.execute("CREATE VIEW provenance AS SELECT * FROM provenance_saved")


@pytest.mark.parametrize("mutate", [
    _listed_view,
    _listed_without_primary_key,
    _listed_wrong_column_type,
    _provenance_view,
], ids=["base-view", "base-no-primary-key", "base-wrong-type", "marker-view"])
def test_existing_schema_change_is_rejected_before_market_writes(
    tmp_path, monkeypatch, mutate
):
    home = tmp_path / "fixture-home"
    _ingest(home)
    with connect(home) as con:
        mutate(con)
    before = _snapshot(home)
    reached = False

    def forbidden_write(*args, **kwargs):
        nonlocal reached
        reached = True
        raise AssertionError("market writes must not precede schema validation")

    monkeypatch.setattr(module, "_insert_rows", forbidden_write)
    with pytest.raises(ValueError):
        _ingest(home, fixture(close=107))
    assert reached is False
    assert _snapshot(home) == before

