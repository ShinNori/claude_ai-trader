"""受入テスト（Claude / Codex 共通・編集禁止）

各ビルドのフォルダで実行:  pytest ../common/tests -q
前提: カレントに `aitrader` パッケージ（aitrader.api を提供）がある。
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path.cwd()))
from aitrader.api import init_db, load_synthetic, run_backtest, run_signals  # noqa: E402

STRATEGY = "margin_bucket_long"
AS_OF = date(2025, 6, 6)          # 金曜。翌営業日 6/9(月) がシグナル日
BT_FROM, BT_TO = date(2023, 1, 4), date(2024, 12, 30)


@pytest.fixture(scope="module")
def home(tmp_path_factory) -> Path:
    h = tmp_path_factory.mktemp("aitrader_home")
    init_db(h)
    load_synthetic(h, seed=42)
    return h


def _con(home: Path):
    return duckdb.connect(str(home / "market.duckdb"))


def test_01_schema(home):
    con = _con(home)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert {"listed", "prices_daily", "margin_weekly", "index_daily", "calendar"} <= tables
    cols = {r[0] for r in con.execute("DESCRIBE prices_daily").fetchall()}
    assert {"code", "date", "open", "high", "low", "close", "volume", "turnover", "adj_factor"} <= cols
    cols = {r[0] for r in con.execute("DESCRIBE margin_weekly").fetchall()}
    assert {"code", "date", "publish_date", "long_balance", "short_balance"} <= cols
    con.close()


def test_02_determinism(home, tmp_path):
    s1 = run_backtest(home, STRATEGY, BT_FROM, BT_TO, tmp_path / "a")
    s2 = run_backtest(home, STRATEGY, BT_FROM, BT_TO, tmp_path / "b")
    assert s1["final_equity"] == s2["final_equity"]
    assert s1["trades"] == s2["trades"]


def test_03_no_lookahead(home, tmp_path):
    base = run_signals(home, STRATEGY, AS_OF)
    # 未来データを改変したコピーで同じシグナルが出るか
    h2 = tmp_path / "future_mutated"
    shutil.copytree(home, h2)
    con = _con(h2)
    con.execute("UPDATE prices_daily SET close = close * 3, open = open * 3 WHERE date > ?", [AS_OF])
    con.execute("UPDATE margin_weekly SET long_balance = long_balance * 5 WHERE date > ?", [AS_OF])
    con.close()
    mutated = run_signals(h2, STRATEGY, AS_OF)
    assert [c["code"] for c in base] == [c["code"] for c in mutated]


def test_04_respects_publish_date(home, tmp_path):
    base = run_signals(home, STRATEGY, AS_OF)
    h2 = tmp_path / "unpublished_removed"
    shutil.copytree(home, h2)
    con = _con(h2)
    con.execute("DELETE FROM margin_weekly WHERE publish_date > ?", [AS_OF])
    con.close()
    assert [c["code"] for c in base] == [c["code"] for c in run_signals(h2, STRATEGY, AS_OF)]


def test_05_limit_rule(home, tmp_path):
    out = tmp_path / "lim"
    run_backtest(home, STRATEGY, BT_FROM, BT_TO, out)
    trades = pd.read_csv(next(out.rglob("trades.csv")), parse_dates=["entry_date"], dtype={"code": str})
    assert len(trades) > 0
    con = _con(home)
    px = con.execute("SELECT code, date, open, close FROM prices_daily").df()
    con.close()
    px["date"] = pd.to_datetime(px["date"])
    px = px.sort_values(["code", "date"])
    px["prev_close"] = px.groupby("code")["close"].shift(1)
    m = trades.merge(px, left_on=["code", "entry_date"], right_on=["code", "date"], how="left")
    assert m["open"].notna().all(), "エントリー日が取引日でない"
    assert (m["open"] <= m["prev_close"] * 1.005 + 1e-6).all(), "指値を超えた寄り値で約定している"


def test_06_summary_keys(home, tmp_path):
    out = tmp_path / "keys"
    s = run_backtest(home, STRATEGY, BT_FROM, BT_TO, out)
    required = {
        "strategy", "version", "period", "initial_capital", "final_equity", "cagr", "sharpe",
        "max_drawdown", "trades", "skipped_by_limit", "win_rate", "profit_factor",
        "avg_holding_days", "benchmark_cagr", "benchmark_max_drawdown", "corr_to_benchmark",
        "yearly", "data_mode", "generated_at",
    }
    assert required <= set(s.keys()), required - set(s.keys())
    f = next(out.rglob("summary.json"))
    assert json.loads(f.read_text(encoding="utf-8"))["final_equity"] == s["final_equity"]


def test_07_equity_consistency(home, tmp_path):
    out = tmp_path / "eq"
    s = run_backtest(home, STRATEGY, BT_FROM, BT_TO, out)
    eq = pd.read_csv(next(out.rglob("equity.csv")))
    assert {"date", "equity", "cash", "positions_value", "benchmark"} <= set(eq.columns)
    assert abs(eq["equity"].iloc[-1] - s["final_equity"]) < 1.0
    assert ((eq["cash"] + eq["positions_value"] - eq["equity"]).abs() < 1.0).all()


def test_08_a1_spec(home):
    cands = run_signals(home, STRATEGY, AS_OF)
    assert 0 < len(cands) <= 20
    assert all(c["side"] == "BUY" for c in cands)
    # chg4w が（ユニバース差を許容して）全銘柄中の下位30%以内に入っているか
    con = _con(home)
    mw = con.execute(
        "SELECT code, date, long_balance FROM margin_weekly WHERE publish_date <= ? ORDER BY code, date", [AS_OF]
    ).df()
    con.close()
    last = mw.groupby("code").tail(5)
    g = last.groupby("code")["long_balance"]
    chg = (g.last() / g.first() - 1).dropna()
    cutoff = chg.quantile(0.3)
    picked = chg.reindex([c["code"] for c in cands])
    assert picked.notna().all()
    assert (picked <= cutoff + 1e-9).all(), "候補が買残減少の下位30%に入っていない"
