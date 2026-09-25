"""フェーズ2 データ型（共通仕様_フェーズ2 §2, §4）— Claude 単独ビルド

Proposal / Verdict / Limits / GateResult と、packet_hash の正規化ルール。
ops/aitrader_ops とは独立した実装だが、正規化ルールは共通仕様 §5 に従うので
ハッシュ値は他ビルドと一致する。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING

JST = timezone(timedelta(hours=9), "Asia/Tokyo")

HASH_FIELDS = (
    "proposal_id", "code", "side", "qty", "lot_size", "limit_price",
    "exec_condition", "account_type", "strategy", "strategy_version",
    "as_of", "snapshot_id", "policy_version", "expires_at",
)
DECISIONS = ("APPROVE", "REJECT", "ABSTAIN", "INVALID")
NOTICE_STATES = ("CREATED", "APPROVED", "REJECTED", "SENT", "EXPIRED")
TRADE_STATES = ("UNCONFIRMED", "ORDERED", "PARTIAL", "FILLED", "CANCELLED", "SKIPPED")


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    packet_hash: str
    code: str
    side: str
    qty: int
    lot_size: int
    limit_price: float
    exec_condition: str
    account_type: str
    strategy: str
    strategy_version: str
    as_of: date
    snapshot_id: str
    policy_version: str
    expires_at: datetime
    reason: str
    events: dict

    def to_json(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["as_of"] = self.as_of.isoformat()
        d["expires_at"] = self.expires_at.isoformat()
        d["events"] = _events_json(self.events)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Proposal":
        d = dict(d)
        d["as_of"] = date.fromisoformat(d["as_of"])
        d["expires_at"] = datetime.fromisoformat(d["expires_at"])
        ev = dict(d.get("events") or {})
        ne = ev.get("next_earnings_date", "UNKNOWN")
        if isinstance(ne, str) and ne != "UNKNOWN":
            ev["next_earnings_date"] = date.fromisoformat(ne)
        d["events"] = ev
        return cls(**d)


def _events_json(ev: dict) -> dict:
    out = {}
    for k, v in (ev or {}).items():
        out[k] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return out


@dataclass(frozen=True)
class Verdict:
    judge: str
    proposal_id: str
    packet_hash: str
    decision: str
    risks: tuple[str, ...]
    reason: str
    confidence: float | None
    received_at: datetime
    model: str
    cli_version: str
    run_id: str


@dataclass
class Limits:
    max_positions: int = 5
    max_weight_per_name: float = 0.25
    max_new_per_day: int = 2
    daily_loss_stop: float = 0.02
    drawdown_stop: float = 0.10
    earnings_blackout_days: int = 2
    fee_margin: float = 0.002


@dataclass
class GateResult:
    allowed: bool
    category: str                      # "NEW" | "EXIT"
    reasons: list[str] = field(default_factory=list)
    reserve_amount: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class Position:
    code: str
    qty: int
    avg_price: float
    stop_order: bool = False
    stop_price: float | None = None


@dataclass
class LedgerView:
    cash: int
    reserved: int
    available: int
    positions: dict
    reserved_positions: dict
    daily_pnl: int = 0
    day_start_equity: int | None = None


def packet_hash(p) -> str:
    """共通仕様 §5 の正規化: 14 フィールドを sort_keys / ensure_ascii=False / (",",":") で JSON 化し sha256。"""
    fields = {}
    for k in HASH_FIELDS:
        v = getattr(p, k)
        if isinstance(v, (date, datetime)):
            v = v.isoformat()
        fields[k] = v
    s = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def reserve_amount(qty: int, limit_price: float, fee_margin: float) -> int:
    """予約額 = qty × 指値 × (1 + fee_margin) を円で切り上げ（Decimal で計算）"""
    if qty <= 0:
        return 0
    v = Decimal(qty) * Decimal(str(limit_price)) * (Decimal(1) + Decimal(str(fee_margin)))
    return int(v.to_integral_value(rounding=ROUND_CEILING))
