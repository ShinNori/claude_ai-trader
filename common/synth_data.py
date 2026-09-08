"""合成データ生成（Claude / Codex 共通・編集禁止）

build(seed=42) は 共通仕様 §3 のテーブルに対応する DataFrame を返す。
- 300 銘柄、2016-01-04 〜 2026-08-31、月〜金（年末年始・簡易祝日を除く）
- 価格は業種ファクター＋固有ノイズの幾何ブラウン運動
- 信用残は「株価が下がると買残が増え、上がると減る」傾向を弱く持つ（個人の逆張り）
  ＋ 買残が減った後に株価が上がりやすい弱い効果（戦略A1が拾う対象）を意図的に入れてある。
  ※ 実市場でこの効果が存在する保証はない。基盤の動作確認用。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

START = "2016-01-04"
END = "2026-08-31"
N_STOCKS = 300
SECTORS = [
    "情報・通信業", "電気機器", "機械", "化学", "医薬品", "小売業", "銀行業",
    "不動産業", "輸送用機器", "サービス業", "卸売業", "建設業",
]


def _business_days() -> pd.DatetimeIndex:
    days = pd.bdate_range(START, END)
    holidays = set()
    for y in range(2016, 2027):
        for md in ["01-01", "01-02", "01-03", "12-31", "05-03", "05-04", "05-05", "08-11", "11-03", "11-23"]:
            holidays.add(pd.Timestamp(f"{y}-{md}"))
    return days[~days.isin(holidays)]


def build(seed: int = 42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    days = _business_days()
    n_days = len(days)

    codes = [f"{1300 + i * 27 % 8000:04d}" for i in range(N_STOCKS)]
    codes = [c if c not in codes[:i] else f"{9000 + i:04d}" for i, c in enumerate(codes)]
    sectors = rng.choice(SECTORS, size=N_STOCKS)
    markets = rng.choice(["prime", "standard", "growth"], size=N_STOCKS, p=[0.6, 0.3, 0.1])
    listed_dates = pd.to_datetime(
        rng.choice(pd.date_range("1990-01-01", "2018-06-30").values, size=N_STOCKS)
    )

    # 市場・業種ファクター
    mkt = rng.normal(0.00025, 0.008, n_days)
    sec_f = {s: rng.normal(0.0, 0.006, n_days) for s in SECTORS}

    # 信用残（週次）の準備
    fridays = days[days.weekday == 4]
    n_weeks = len(fridays)

    prices_rows = []
    margin_rows = []
    base_prices = rng.lognormal(mean=np.log(2500), sigma=0.8, size=N_STOCKS)
    base_vols = rng.lognormal(mean=np.log(300_000), sigma=1.2, size=N_STOCKS)

    for i, code in enumerate(codes):
        idio = rng.normal(0.0, 0.015, n_days)
        # 買残の状態（対数）と、買残減少→翌月上昇の弱い効果
        long_bal = np.empty(n_weeks)
        short_bal = np.empty(n_weeks)
        long_bal[0] = base_vols[i] * rng.uniform(2, 8)
        short_bal[0] = long_bal[0] * rng.uniform(0.1, 0.6)
        week_idx = np.searchsorted(days.values, fridays.values)  # 各金曜の日インデックス
        effect = np.zeros(n_days)
        r = np.zeros(n_days)
        for w in range(1, n_weeks):
            lo, hi = week_idx[w - 1], week_idx[w]
            past_ret = float(np.sum(r[max(0, lo - 5):lo])) if lo > 0 else 0.0
            # 個人の逆張り: 直近下落で買残増
            long_bal[w] = long_bal[w - 1] * np.exp(-1.5 * past_ret + rng.normal(0, 0.06))
            short_bal[w] = short_bal[w - 1] * np.exp(0.8 * past_ret + rng.normal(0, 0.08))
            if w >= 4:
                chg4w = long_bal[w] / long_bal[w - 4] - 1
                # 買残が減った銘柄は翌4週に弱いプラス（年率換算で数%程度）
                effect[hi:hi + 20] += -0.00025 * np.tanh(chg4w * 3)
            r[lo:hi] = mkt[lo:hi] + sec_f[sectors[i]][lo:hi] + idio[lo:hi] + effect[lo:hi]
        r[week_idx[-1]:] = mkt[week_idx[-1]:] + sec_f[sectors[i]][week_idx[-1]:] + idio[week_idx[-1]:]

        close = base_prices[i] * np.exp(np.cumsum(r))
        gap = rng.normal(0, 0.006, n_days)
        open_ = close * np.exp(gap - r)  # 前日終値からのギャップ
        open_[0] = close[0]
        intr = np.abs(rng.normal(0, 0.008, n_days))
        high = np.maximum(open_, close) * (1 + intr)
        low = np.minimum(open_, close) * (1 - intr)
        vol = base_vols[i] * np.exp(rng.normal(0, 0.5, n_days)) * (1 + 3 * np.abs(r))
        vol = np.round(vol / 100) * 100
        turnover = vol * close
        prices_rows.append(pd.DataFrame({
            "code": code, "date": days, "open": np.round(open_, 1), "high": np.round(high, 1),
            "low": np.round(low, 1), "close": np.round(close, 1), "volume": vol,
            "turnover": np.round(turnover), "adj_factor": 1.0,
        }))
        margin_rows.append(pd.DataFrame({
            "code": code, "date": fridays,
            "publish_date": fridays + pd.Timedelta(days=4),
            "long_balance": np.round(long_bal / 100) * 100,
            "short_balance": np.round(short_bal / 100) * 100,
        }))

    prices = pd.concat(prices_rows, ignore_index=True)
    margin = pd.concat(margin_rows, ignore_index=True)
    listed = pd.DataFrame({
        "code": codes, "name": [f"合成{c}" for c in codes], "market": markets,
        "sector33": sectors, "listed_date": listed_dates,
    })
    # 等金額指数（各銘柄の日次リターン平均の累積）
    ret = prices.pivot(index="date", columns="code", values="close").pct_change().mean(axis=1).fillna(0)
    index = pd.DataFrame({"name": "TOPIX", "date": ret.index, "close": 1000 * np.cumprod(1 + ret.values)})
    cal_all = pd.date_range(START, END)
    calendar = pd.DataFrame({"date": cal_all, "is_business_day": cal_all.isin(days)})
    return {
        "listed": listed, "prices_daily": prices, "margin_weekly": margin,
        "index_daily": index, "calendar": calendar,
    }


if __name__ == "__main__":
    d = build()
    for k, v in d.items():
        print(k, v.shape)
