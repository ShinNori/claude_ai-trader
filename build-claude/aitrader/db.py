"""DuckDB スキーマと接続（共通仕様 §3）"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS listed (
  code TEXT PRIMARY KEY, name TEXT, market TEXT, sector33 TEXT, listed_date DATE
);
CREATE TABLE IF NOT EXISTS prices_daily (
  code TEXT, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
  volume DOUBLE, turnover DOUBLE, adj_factor DOUBLE, PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS margin_weekly (
  code TEXT, date DATE, publish_date DATE, long_balance DOUBLE, short_balance DOUBLE,
  PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS index_daily (
  name TEXT, date DATE, close DOUBLE, PRIMARY KEY (name, date)
);
CREATE TABLE IF NOT EXISTS calendar (
  date DATE PRIMARY KEY, is_business_day BOOLEAN
);
"""

TABLES = ["listed", "prices_daily", "margin_weekly", "index_daily", "calendar"]


def default_home() -> Path:
    return Path(os.environ.get("AI_TRADER_HOME", Path.home() / ".ai-trader"))


def db_path(home: Path | None = None) -> Path:
    home = Path(home) if home else default_home()
    home.mkdir(parents=True, exist_ok=True)
    return home / "market.duckdb"


def connect(home: Path | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path(home)), read_only=read_only)


def init_db(home: Path | None = None) -> None:
    con = connect(home)
    try:
        con.execute(SCHEMA)
    finally:
        con.close()


def replace_table(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> None:
    """テーブルを DataFrame で置き換える（列順はスキーマに合わせる）"""
    cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    con.register("_df", df[cols])
    con.execute(f"DELETE FROM {table}")
    con.execute(f"INSERT INTO {table} SELECT * FROM _df")
    con.unregister("_df")


def upsert_table(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> None:
    cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    con.register("_df", df[cols])
    con.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM _df")
    con.unregister("_df")
