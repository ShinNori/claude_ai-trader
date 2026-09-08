"""データ型（共通仕様 フェーズ2 §2 ＋ ISSUES.md #1 の入力型を正式化）

- Proposal / Verdict: 仕様どおり（frozen dataclass）
- 入力型（PositionIn, OpenOrderIn, TradeEvent, CsvFill）はダックタイピングで受け付ける。
  テストは SimpleNamespace を渡すため、ここでは「必要な属性名」だけを定義し、
  _coerce_* で読み取る。正式な dataclass も提供する（呼び出し側が使いたい場合）。
- packet_hash の正規化ルールは build-codex/aitrader/packet.py と同一（14フィールド、
  sort_keys、ensure_ascii=False、separators=(",", ":")、date/datetime は isoformat）。
  ゲートはこの関数で再計算し、Proposal が審査後に改変されていないことを確認する。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Any

JST = timezone(timedelta(hours=9), "Asia/Tokyo")

HASH_FIELDS = (
    "proposal_id", "code", "side", "qty", "lot_size", "limit_price",
    "exec_condition", "account_type", "strategy", "strategy_version",
    "as_of", "snapshot_id", "policy_version", "expires_at",
)

NOTICE_STATES = ("CREATED", "APPROVED", "REJECTED", "SENT", "EXPIRED")
TRADE_STATES = ("UNCONFIRMED", "ORDERED", "PARTIAL", "FILLED", "CANCELLED", "SKIPPED")
TRADE_EVENT_KINDS = ("ORDERED", "PARTIAL", "FILLED", "CANCELLED", "SKIPPED", "CORRECTION")


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


# ---- 入力型（正式定義。属性互換の任意オブジェクトも可） ----------------------

@dataclass(frozen=True)
class PositionIn:
    code: str
    qty: int
    avg_price: float
    stop_order: bool = False
    stop_price: float | None = None


@dataclass(frozen=True)
class OpenOrderIn:
    proposal_id: str | None
    code: str
    side: str
    qty: int
    limit_price: float
    broker_order_id: str | None = None


@dataclass(frozen=True)
class TradeEvent:
    event_id: str
    proposal_id: str
    kind: str                     # ORDERED | PARTIAL | FILLED | CANCELLED | SKIPPED | CORRECTION
    qty: int
    price: float
    fee: float
    at: datetime
    source: str                   # "line" | "csv" | "manual"
    broker_order_id: str | None = None
    replaces_event_id: str | None = None   # CORRECTION の対象（省略時は直前の約定報告）


@dataclass(frozen=True)
class CsvFill:
    event_id: str
    proposal_id: str | None
    code: str
    side: str
    qty: int
    price: float
    fee: float
    at: datetime
    source: str = "csv"
    broker_order_id: str | None = None


# ---- 出力型 ------------------------------------------------------------------

@dataclass
class Position:
    code: str
    qty: int
    avg_price: float
    stop_order: bool = False
    stop_price: float | None = None


@dataclass
class ReportResult:
    event_id: str
    applied: bool
    error: str | None = None
    duplicate: bool = False
    ignored_reason: str | None = None
    trade_state: str | None = None
    warnings: list[str] = field(default_factory=list)
    pending: bool = False


@dataclass
class ImportResult:
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)      # 既存約定と一致（重複）
    pending: list[str] = field(default_factory=list)      # 曖昧な一致。人間の確認待ち
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class LedgerView:
    """ゲートに渡す台帳の読み取りビュー（ISSUES #1, #5）"""
    cash: int
    reserved: int
    available: int
    positions: dict[str, Position]
    reserved_positions: dict[str, int]      # code -> 予約金額（未確認・発注済・一部約定）
    daily_pnl: int = 0
    day_start_equity: int | None = None
    reserved_shares: dict[str, int] = field(default_factory=dict)   # code -> 売却予約株数（R19）


# ---- ユーティリティ ------------------------------------------------------------

def _json_default(value: Any):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"unsupported: {type(value).__name__}")


def compute_packet_hash(p: Any) -> str:
    """packet.py と同一の正規化。p は Proposal 互換オブジェクト。"""
    if not isinstance(getattr(p, "expires_at", None), datetime) or p.expires_at.utcoffset() is None:
        raise ValueError("expires_at requires a timezone")
    payload = {name: getattr(p, name) for name in HASH_FIELDS}
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=_json_default)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def reserve_amount(qty: int, limit_price: float, fee_margin: float) -> int:
    """予約額 = qty × 指値 × (1 + fee_margin) を円単位で切り上げ（Decimal で計算し float 誤差を避ける）"""
    amount = Decimal(int(qty)) * Decimal(str(limit_price)) * (Decimal(1) + Decimal(str(fee_margin)))
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def yen(value: float | int | Decimal) -> int:
    """金額を円の整数へ（四捨五入ではなく最近接偶数丸めを避けるため Decimal→ROUND_HALF_UP）"""
    from decimal import ROUND_HALF_UP
    return int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))


def as_aware(dt: datetime) -> datetime:
    if not isinstance(dt, datetime):
        raise ValueError("datetime required")
    if dt.tzinfo is None:
        raise ValueError("naive datetime is not allowed (timezone required)")
    return dt
