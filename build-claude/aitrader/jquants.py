"""J-Quants API クライアント（最小実装）と DB 取り込み

- 認証: リフレッシュトークン（.env の JQUANTS_REFRESH_TOKEN）→ ID トークン
- エンドポイントは J-Quants v1 を想定。列名は変わることがあるので _norm_* で吸収する
- 無料プランでは 12 週遅延・直近2年のみ。プラン差はデータの有無として自然に現れる（コードは共通）
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from .db import connect, upsert_table

BASE = "https://api.jquants.com/v1"


class JQuantsClient:
    def __init__(self, refresh_token: str | None = None, session: requests.Session | None = None):
        self.refresh_token = refresh_token or os.environ.get("JQUANTS_REFRESH_TOKEN")
        if not self.refresh_token:
            raise RuntimeError("JQUANTS_REFRESH_TOKEN が設定されていません（.env を確認）")
        self.s = session or requests.Session()
        self.id_token: str | None = None

    def _auth(self) -> None:
        r = self.s.post(f"{BASE}/token/auth_refresh", params={"refreshtoken": self.refresh_token}, timeout=30)
        r.raise_for_status()
        self.id_token = r.json()["idToken"]

    def get(self, path: str, params: dict | None = None) -> list[dict]:
        if not self.id_token:
            self._auth()
        params = dict(params or {})
        rows: list[dict] = []
        while True:
            r = self.s.get(f"{BASE}{path}", params=params,
                           headers={"Authorization": f"Bearer {self.id_token}"}, timeout=60)
            if r.status_code == 401:
                self._auth()
                continue
            if r.status_code == 429:
                time.sleep(2)
                continue
            r.raise_for_status()
            j = r.json()
            key = next((k for k in j if isinstance(j[k], list)), None)
            if key:
                rows.extend(j[key])
            if j.get("pagination_key"):
                params["pagination_key"] = j["pagination_key"]
            else:
                return rows


# ---- 正規化 -------------------------------------------------------------

def _norm_listed(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    market_map = {"プライム": "prime", "スタンダード": "standard", "グロース": "growth"}
    mname = df.get("MarketCodeName", pd.Series([""] * len(df)))
    return pd.DataFrame({
        "code": df["Code"].astype(str).str[:4],
        "name": df.get("CompanyName", ""),
        "market": mname.map(lambda s: next((v for k, v in market_map.items() if k in str(s)), "other")),
        "sector33": df.get("Sector33CodeName", ""),
        "listed_date": pd.to_datetime(df.get("Date", pd.NaT)).dt.date if "Date" in df else pd.NaT,
    })


def _norm_prices(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    pick = lambda a, b: df[a] if a in df else df[b]
    out = pd.DataFrame({
        "code": df["Code"].astype(str).str[:4],
        "date": pd.to_datetime(df["Date"]).dt.date,
        "open": pick("AdjustmentOpen", "Open"), "high": pick("AdjustmentHigh", "High"),
        "low": pick("AdjustmentLow", "Low"), "close": pick("AdjustmentClose", "Close"),
        "volume": pick("AdjustmentVolume", "Volume"),
        "turnover": df.get("TurnoverValue", pd.NA),
        "adj_factor": df.get("AdjustmentFactor", 1.0),
    })
    return out.dropna(subset=["close"])


def _norm_margin(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    d = pd.to_datetime(df["Date"])
    pub = pd.to_datetime(df["PublishedDate"]) if "PublishedDate" in df else d + pd.Timedelta(days=4)
    return pd.DataFrame({
        "code": df["Code"].astype(str).str[:4], "date": d.dt.date, "publish_date": pub.dt.date,
        "long_balance": df.get("LongMarginTradeVolume", pd.NA),
        "short_balance": df.get("ShortMarginTradeVolume", pd.NA),
    })


# ---- 取り込み -----------------------------------------------------------

def fetch_to_db(home: Path | None, start: date, end: date, client: JQuantsClient | None = None) -> dict:
    client = client or JQuantsClient()
    con = connect(home)
    counts = {}
    try:
        listed = _norm_listed(client.get("/listed/info"))
        upsert_table(con, "listed", listed)
        counts["listed"] = len(listed)

        prices = []
        d = start
        while d <= end:
            prices.extend(client.get("/prices/daily_quotes", {"date": d.isoformat()}))
            d += timedelta(days=1)
        p = _norm_prices(prices)
        if len(p):
            upsert_table(con, "prices_daily", p)
        counts["prices_daily"] = len(p)

        margin = _norm_margin(client.get("/markets/weekly_margin_interest",
                                         {"from": start.isoformat(), "to": end.isoformat()}))
        if len(margin):
            upsert_table(con, "margin_weekly", margin)
        counts["margin_weekly"] = len(margin)

        idx = client.get("/indices/topix", {"from": start.isoformat(), "to": end.isoformat()})
        if idx:
            i = pd.DataFrame(idx)
            i = pd.DataFrame({"name": "TOPIX", "date": pd.to_datetime(i["Date"]).dt.date, "close": i["Close"]})
            upsert_table(con, "index_daily", i)
            counts["index_daily"] = len(i)

        cal = pd.DataFrame({"date": pd.date_range(start, end).date})
        bdays = set(p["date"]) if len(p) else set()
        cal["is_business_day"] = cal["date"].isin(bdays)
        upsert_table(con, "calendar", cal)
    finally:
        con.close()
    return counts
