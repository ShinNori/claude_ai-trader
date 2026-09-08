"""ハードリミット（共通仕様 フェーズ2 §4、設計書 6.2）

初期版の既定値: 最大 5 銘柄、1銘柄 25%、1日の新規候補 2 件、日次損失 2%、DD 10%、決算 ±2営業日。
評価はすべて Decimal で行い、境界（ちょうど）の判定が float 誤差で揺れないようにする。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Limits:
    max_positions: int = 5
    max_weight_per_name: float = 0.25
    max_new_per_day: int = 2
    daily_loss_stop: float = 0.02
    drawdown_stop: float = 0.10
    earnings_blackout_days: int = 2
    fee_margin: float = 0.002


def D(value) -> Decimal:
    return Decimal(str(value))


def drawdown_hit(equity, peak_equity, limits: Limits) -> bool:
    """equity / peak - 1 <= -drawdown_stop  ⇔  equity <= peak × (1 - drawdown_stop)"""
    if peak_equity is None or D(peak_equity) <= 0:
        return False
    return D(equity) <= D(peak_equity) * (Decimal(1) - D(limits.drawdown_stop))


def daily_loss_hit(daily_pnl, day_start_equity, limits: Limits) -> bool:
    """daily_pnl / day_start_equity <= -daily_loss_stop"""
    if day_start_equity is None or D(day_start_equity) <= 0:
        return False
    return D(daily_pnl) <= -D(limits.daily_loss_stop) * D(day_start_equity)


def weight_exceeded(existing_value, reserve, equity, limits: Limits) -> bool:
    """(既存保有額 + 予約額) / equity > max_weight  ⇔  existing + reserve > max_weight × equity"""
    if equity is None or D(equity) <= 0:
        return True
    return D(existing_value) + D(reserve) > D(limits.max_weight_per_name) * D(equity)
