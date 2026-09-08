"""A1 信用買残減少バケット・ロング（共通仕様 §4.1）

- ユニバース: prime/standard、上場1年以上、直近60営業日平均売買代金 >= 1億円、margin_weekly あり
- 指標: chg4w = long_balance(最新) / long_balance(4週前) - 1  （publish_date <= as_of の中で）
- 5分位の最下位（減少最大）を等金額 BUY、chg4w 昇順で最大 20 銘柄
- すべて as_of 以前のデータのみ使用
"""
from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd

from .base import Candidate

NAME = "margin_bucket_long"
VERSION = "v1"

DEFAULT_PARAMS = {
    "markets": ["prime", "standard"],
    "min_listed_days": 365,
    "turnover_window": 60,
    "min_avg_turnover": 100_000_000,
    "lookback_weeks": 4,
    "n_buckets": 5,
    "max_names": 20,
    "holding_days": 20,
    "limit_pct": 0.005,
}


class MarginBucketLong:
    name = NAME
    version = VERSION
    rebalance = "weekly_mon"

    def __init__(self, params: dict | None = None):
        self.p = {**DEFAULT_PARAMS, **(params or {})}
        self.holding_days = int(self.p["holding_days"])
        self.limit_pct = float(self.p["limit_pct"])

    # ---- universe -------------------------------------------------------
    def universe(self, as_of: date, con: duckdb.DuckDBPyConnection) -> list[str]:
        return self._universe_df(as_of, con)["code"].tolist()

    def _universe_df(self, as_of: date, con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
        p = self.p
        markets = ",".join(f"'{m}'" for m in p["markets"])
        sql = f"""
        WITH recent AS (
          SELECT code, turnover,
                 ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
          FROM prices_daily WHERE date <= ?
        ),
        liq AS (
          SELECT code, AVG(turnover) AS avg_turnover, COUNT(*) AS n
          FROM recent WHERE rn <= ? GROUP BY code
        )
        SELECT l.code
        FROM listed l
        JOIN liq ON liq.code = l.code
        WHERE l.market IN ({markets})
          AND l.listed_date <= ? - INTERVAL {int(p["min_listed_days"])} DAY
          AND liq.n = ?
          AND liq.avg_turnover >= ?
          AND EXISTS (SELECT 1 FROM margin_weekly m WHERE m.code = l.code AND m.publish_date <= ?)
        ORDER BY l.code
        """
        return con.execute(
            sql, [as_of, p["turnover_window"], as_of, p["turnover_window"], p["min_avg_turnover"], as_of]
        ).df()

    # ---- signal ---------------------------------------------------------
    def features(self, as_of: date, con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
        """ユニバース各銘柄の chg4w。as_of 時点で公表済みの週次残高のみ使用"""
        k = int(self.p["lookback_weeks"])
        uni = self._universe_df(as_of, con)
        if uni.empty:
            return pd.DataFrame(columns=["code", "chg4w"])
        con.register("_uni", uni)
        sql = f"""
        WITH m AS (
          SELECT code, date, long_balance,
                 ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
          FROM margin_weekly
          WHERE publish_date <= ? AND code IN (SELECT code FROM _uni)
        )
        SELECT a.code, a.long_balance / NULLIF(b.long_balance, 0) - 1 AS chg4w
        FROM m a JOIN m b ON a.code = b.code AND a.rn = 1 AND b.rn = {k + 1}
        WHERE a.long_balance IS NOT NULL AND b.long_balance IS NOT NULL
        """
        df = con.execute(sql, [as_of]).df()
        con.unregister("_uni")
        return df.dropna()

    def generate(self, as_of: date, con: duckdb.DuckDBPyConnection) -> list[Candidate]:
        f = self.features(as_of, con)
        if len(f) < self.p["n_buckets"]:
            return []
        # 5分位: 最下位バケット = chg4w が小さい方から 1/n
        f = f.sort_values(["chg4w", "code"]).reset_index(drop=True)
        n_bottom = len(f) // int(self.p["n_buckets"])
        bottom = f.iloc[:n_bottom].head(int(self.p["max_names"]))
        if bottom.empty:
            return []
        w = 1.0 / len(bottom)
        out = []
        for _, r in bottom.iterrows():
            out.append(Candidate(
                code=str(r["code"]), side="BUY", weight=w, score=float(-r["chg4w"]),
                reason=f"信用買残 {self.p['lookback_weeks']}週変化 {r['chg4w']*100:+.1f}%（最下位バケット）",
                strategy=self.name, strategy_version=self.version, as_of=as_of,
                holding_days=self.holding_days, limit_pct=self.limit_pct,
            ))
        return out
