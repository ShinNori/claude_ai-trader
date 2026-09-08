"""バックテスト基盤（共通仕様 §5, §6）

解釈メモ（README にも記載）:
- シグナル日 = 各週の最初の営業日（通常は月曜）。as_of = その直前の営業日。エントリーはシグナル日の寄り付き
- 「利用可能資金」= その日の寄り付き時点の現金（当日終値で決済する建玉の代金は含めない。
  市場時刻に合わせ、寄り付きの買付を先に、終値の売却を後に処理する）。候補の weight（等分）を掛けて金額を決める。
  指値で未約定になった分は現金に残る（次週に回る）
- 同一銘柄をすでに保有している場合はエントリーしない（skipped_duplicate として集計）
- イグジットはエントリー日から holding_days 営業日後の終値。期間末に残った建玉は最終日終値で強制決済
- 株数は金額ベース（端株無視）。約定は 買い open*(1+0.001)、売り close*(1-0.001)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .db import connect
from .strategies import get_strategy

SLIPPAGE = 0.001
INITIAL_CAPITAL = 10_000_000.0


@dataclass
class Position:
    code: str
    entry_idx: int
    entry_date: date
    entry_price: float   # スリッページ込み
    shares: float
    qty_yen: float
    exit_idx: int
    reason: str


def _business_days(con, start: date, end: date) -> pd.DatetimeIndex:
    df = con.execute(
        "SELECT DISTINCT date FROM prices_daily WHERE date >= ? AND date <= ? ORDER BY date", [start, end]
    ).df()
    return pd.DatetimeIndex(pd.to_datetime(df["date"]))


def _prev_business_day(con, d: date) -> date | None:
    r = con.execute("SELECT MAX(date) FROM prices_daily WHERE date < ?", [d]).fetchone()[0]
    return r


def _signal_days(days: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """各 ISO 週の最初の営業日"""
    weeks = pd.Series(days).dt.isocalendar()
    key = weeks["year"].astype(str) + "-" + weeks["week"].astype(str)
    first = pd.Series(days).groupby(key.values).min()
    return sorted(first.tolist())


def run(home: Path | None, strategy_name: str, start: date, end: date, out_dir: Path,
        initial_capital: float = INITIAL_CAPITAL, data_mode: str | None = None) -> dict:
    t0 = time.time()
    con = connect(home, read_only=True)
    strat = get_strategy(strategy_name)
    days = _business_days(con, start, end)
    if len(days) < 2:
        raise ValueError("期間内に営業日がありません")
    idx_of = {d: i for i, d in enumerate(days)}

    # 価格テーブル（期間内のみ）
    px = con.execute(
        "SELECT code, date, open, close FROM prices_daily WHERE date >= ? AND date <= ?", [start, end]
    ).df()
    px["date"] = pd.to_datetime(px["date"])
    open_ = px.pivot(index="date", columns="code", values="open").reindex(days)
    close = px.pivot(index="date", columns="code", values="close").reindex(days)
    bench = con.execute(
        "SELECT date, close FROM index_daily WHERE name='TOPIX' AND date >= ? AND date <= ? ORDER BY date",
        [start, end],
    ).df()
    bench["date"] = pd.to_datetime(bench["date"])
    bench = bench.set_index("date")["close"].reindex(days).ffill()
    bench = bench / bench.iloc[0] * initial_capital if bench.notna().any() else pd.Series(np.nan, index=days)

    if data_mode is None:
        data_mode = "synthetic" if con.execute("SELECT COUNT(*) FROM listed WHERE name LIKE '合成%'").fetchone()[0] > 0 else "jquants"

    signal_days = set(_signal_days(days))
    cash = float(initial_capital)
    positions: dict[str, Position] = {}
    trades: list[dict] = []
    equity_rows: list[dict] = []
    skipped_by_limit = 0
    skipped_duplicate = 0

    for i, d in enumerate(days):
        today = d.date()
        # --- entries (寄り付き) ---
        if d in signal_days:
            as_of = _prev_business_day(con, today)
            if as_of is not None:
                cands = strat.generate(as_of, con)
                budget = cash
                prev_close = con.execute(
                    "SELECT code, close FROM prices_daily WHERE date = ?", [as_of]
                ).df().set_index("code")["close"]
                for c in cands:
                    if c.code in positions:
                        skipped_duplicate += 1
                        continue
                    o = open_.at[d, c.code] if c.code in open_.columns else np.nan
                    pc = prev_close.get(c.code, np.nan)
                    if pd.isna(o) or pd.isna(pc):
                        continue
                    limit = pc * (1 + c.limit_pct)
                    if o > limit:
                        skipped_by_limit += 1
                        continue
                    qty_yen = budget * c.weight
                    if qty_yen <= 0 or qty_yen > cash + 1e-6:
                        continue
                    fill = float(o) * (1 + SLIPPAGE)
                    positions[c.code] = Position(
                        code=c.code, entry_idx=i, entry_date=today, entry_price=fill,
                        shares=qty_yen / fill, qty_yen=qty_yen,
                        exit_idx=min(i + c.holding_days, len(days) - 1), reason=c.reason,
                    )
                    cash -= qty_yen
        # --- exits (終値) ---
        for code in [c for c, p in positions.items() if p.exit_idx == i or i == len(days) - 1]:
            p = positions.pop(code)
            c_px = close.at[d, code]
            if pd.isna(c_px):
                c_px = close[code].iloc[: i + 1].dropna().iloc[-1]
            exit_price = float(c_px) * (1 - SLIPPAGE)
            proceeds = p.shares * exit_price
            cash += proceeds
            trades.append({
                "code": code, "entry_date": p.entry_date.isoformat(), "entry_price": round(p.entry_price, 2),
                "exit_date": today.isoformat(), "exit_price": round(exit_price, 2),
                "qty_yen": round(p.qty_yen), "pnl_yen": round(proceeds - p.qty_yen),
                "pnl_pct": round((proceeds / p.qty_yen - 1), 6), "holding_days": i - p.entry_idx,
                "strategy": f"{strat.name}_{strat.version}", "reason": p.reason,
            })
        # --- mark to market ---
        pos_val = 0.0
        for code, p in positions.items():
            c_px = close.at[d, code]
            if pd.isna(c_px):
                c_px = close[code].iloc[: i + 1].dropna().iloc[-1]
            pos_val += p.shares * float(c_px)
        equity_rows.append({
            "date": today.isoformat(), "equity": round(cash + pos_val, 2), "cash": round(cash, 2),
            "positions_value": round(pos_val, 2), "benchmark": round(float(bench.iloc[i]), 2) if not pd.isna(bench.iloc[i]) else "",
        })
    con.close()

    eq = pd.DataFrame(equity_rows)
    tr = pd.DataFrame(trades, columns=[
        "code", "entry_date", "entry_price", "exit_date", "exit_price", "qty_yen", "pnl_yen",
        "pnl_pct", "holding_days", "strategy", "reason",
    ])
    summary = _summarize(strat, start, end, initial_capital, eq, tr, bench, skipped_by_limit, skipped_duplicate, data_mode)
    summary["elapsed_sec"] = round(time.time() - t0, 1)

    out = Path(out_dir) / f"{strat.name}_{strat.version}"
    out.mkdir(parents=True, exist_ok=True)
    eq.to_csv(out / "equity.csv", index=False)
    tr.to_csv(out / "trades.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _max_drawdown(series: pd.Series) -> float:
    s = series.astype(float)
    peak = s.cummax()
    dd = s / peak - 1
    return float(dd.min()) if len(dd) else 0.0


def _summarize(strat, start, end, initial, eq: pd.DataFrame, tr: pd.DataFrame, bench: pd.Series,
               skipped_by_limit: int, skipped_duplicate: int, data_mode: str) -> dict:
    equity = eq["equity"].astype(float)
    n_days = len(equity)
    final = float(equity.iloc[-1])
    daily = equity.pct_change().dropna()
    cagr = (final / initial) ** (252 / max(n_days, 1)) - 1
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0
    b = bench.astype(float).reset_index(drop=True)
    b_daily = b.pct_change().dropna()
    b_cagr = (float(b.iloc[-1]) / float(b.iloc[0])) ** (252 / max(n_days, 1)) - 1 if b.notna().all() else 0.0
    corr = float(np.corrcoef(daily.values, b_daily.values)[0, 1]) if len(daily) == len(b_daily) and len(daily) > 2 else 0.0

    wins = tr[tr["pnl_yen"] > 0]["pnl_yen"].sum() if len(tr) else 0.0
    losses = -tr[tr["pnl_yen"] < 0]["pnl_yen"].sum() if len(tr) else 0.0
    yearly = {}
    eq_y = eq.copy()
    eq_y["year"] = eq_y["date"].str[:4]
    for y, g in eq_y.groupby("year"):
        first = float(g["equity"].iloc[0])
        prev_rows = eq_y[eq_y["date"] < g["date"].iloc[0]]
        base = float(prev_rows["equity"].iloc[-1]) if len(prev_rows) else initial
        yearly[y] = {
            "return": round(float(g["equity"].iloc[-1]) / base - 1, 6),
            "trades": int((tr["entry_date"].str[:4] == y).sum()) if len(tr) else 0,
        }
        _ = first
    return {
        "strategy": strat.name, "version": strat.version,
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "initial_capital": initial, "final_equity": round(final, 2),
        "cagr": round(float(cagr), 6), "sharpe": round(sharpe, 4),
        "max_drawdown": round(_max_drawdown(equity), 6),
        "trades": int(len(tr)), "skipped_by_limit": int(skipped_by_limit),
        "skipped_duplicate": int(skipped_duplicate),
        "win_rate": round(float((tr["pnl_yen"] > 0).mean()), 4) if len(tr) else 0.0,
        "profit_factor": round(float(wins / losses), 4) if losses > 0 else None,
        "avg_holding_days": round(float(tr["holding_days"].mean()), 2) if len(tr) else 0.0,
        "benchmark_cagr": round(float(b_cagr), 6),
        "benchmark_max_drawdown": round(_max_drawdown(b), 6) if b.notna().all() else 0.0,
        "corr_to_benchmark": round(corr, 4),
        "yearly": yearly, "data_mode": data_mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
