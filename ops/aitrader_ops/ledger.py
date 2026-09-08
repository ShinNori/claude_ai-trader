"""運用台帳（共通仕様 フェーズ2 §3）— イベントソーシング実装（v0.3: S番号修正）

v0.3: 残高と監査を同時コミットし、SELL同株数訂正の取得原価を固定する。
業務時点replayは明示参照の通知識別情報を補う限定的再生。詳細な制限はREADME v0.3参照。

設計:
- 永続化は SQLite。残高を動かすのは追記専用の `ledger_events` だけ（起動時に全再生）。
  受信した報告は適用・重複・無視・保留・拒否のすべてを `ingest_attempts` に監査行として追記する
  （残高イベントとしては再適用しない。レビュー回答3）。
- すべての変更は「状態のコピーに適用 → 成功したらイベント追記 → 状態を置換」。検証に失敗した報告は残高を変えない。
- 別ハンドル（別プロセス）が同じ DB に追記していたら、公開メソッドの入口で seq を比較して自動再生する（R15）。
- 予約率（fee_margin）や規則版は SNAPSHOT イベントに保存し、再オープン時はそれを使う（R16。起動引数は初期化時のみ有効）。
- 予約額は「未約定残数量 × 指値 × (1 + fee_margin)」。注文が完了（FILLED/CANCELLED/SKIPPED/REJECTED）したら 0 で固定し、
  訂正で約定を打ち消しても復活しない（R02）。
- 通知作成・約定・出金のいずれでも `cash − reserved`（余力）が負になる操作は拒否（R03〜R05）。
- 初期スナップショットの open_orders は外部注文として登録し、BUY は現金予約、SELL は株数予約に含める（R06/R07）。
- SELL の約定は取得原価（平均単価×株数）を取り崩し、訂正で戻すのも取得原価（売却額ではない）（R01）。
- CSV 照合: 全通知の約定を横断して「注文ID・数量・単価（・時刻）」が一致すれば重複。手数料だけ違えば pending（R12/R13）。
  明示 proposal_id でも銘柄・売買方向が違えば pending（R11）。proposal_id なしは同銘柄・同方向の未決済通知が1件のときだけ紐付け。
- replay(at) は「業務時刻（effective_at）が at 以前」のイベントを再生。通知作成にも実際の作成時刻を持たせる（R14）。
  `replay_known(seq)` は「記録順で seq 以下」を再生（その朝に何を知っていたか）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import (
    JST, NOTICE_STATES, TRADE_EVENT_KINDS, ImportResult, LedgerView, Position, ReportResult,
    as_aware, reserve_amount, yen,
)

SCHEMA_VERSION = 3


class LedgerError(Exception):
    """検証エラー。残高は変更されない。"""


class LedgerNotInitialized(LedgerError):
    """初期スナップショット未登録。"""


class MigrationError(LedgerError):
    def __init__(self, seq, kind, reason):
        self.seq, self.kind, self.reason = seq, kind, reason
        super().__init__(f'履歴番号 {seq} ({kind}): {reason}')


_NOTICE_TRANSITIONS = {
    "CREATED": {"APPROVED", "REJECTED"},
    "APPROVED": {"SENT", "REJECTED"},
    "SENT": {"EXPIRED"},
    "REJECTED": set(),
    "EXPIRED": set(),
    "EXTERNAL": set(),          # 初期スナップショットの open_orders
}
_OPEN_TRADE_STATES = ("UNCONFIRMED", "ORDERED", "PARTIAL")


# ---- 直列化ユーティリティ ---------------------------------------------------------

def _to_plain(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_plain(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_plain(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if hasattr(obj, "__dict__") and not isinstance(obj, (str, int, float, bool)):
        return {k: _to_plain(v) for k, v in vars(obj).items()}
    return obj


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return as_aware(value)
    if isinstance(value, str):
        return as_aware(datetime.fromisoformat(value))
    raise LedgerError(f"datetime が必要: {value!r}")


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int_qty(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LedgerError(f"{label} は整数の株数が必要: {value!r}")
    return value


def _money(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise LedgerError(f"{label} は数値が必要: {value!r}")
    try:
        d = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise LedgerError(f"{label} を数値にできない: {value!r}") from exc
    if not d.is_finite():
        raise LedgerError(f"{label} が有限でない: {value!r}")
    return d


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _external_payload(value):
    """Internal names belong to replay/persistence, never an input producer."""
    if isinstance(value, dict):
        return {k: _external_payload(v) for k, v in value.items() if not k.startswith('_')}
    if isinstance(value, list):
        return [_external_payload(v) for v in value]
    return value


def _required_text(data, keys):
    for key in keys:
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise LedgerError(f'{key} は空でない文字列が必要です')


def _hash_field(data, key):
    value = data.get(key)
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdefABCDEF' for c in value):
        raise LedgerError(f'{key} はSHA256（64桁の16進数）が必要です')


def _contract_time(value, label):
    try:
        return _dt(value)
    except (ValueError, TypeError, LedgerError) as exc:
        raise LedgerError(f'{label} はタイムゾーン付き日時が必要です') from exc


def _validate_provenance(value):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('method') != 'RECONCILED_OPENING':
        raise LedgerError('provenance.method はRECONCILED_OPENINGが必要です')
    _required_text(value, ('source_path', 'reconciliation_id', 'reconciliation_record_path',
                           'actor', 'reason', 'previous_policy_version'))
    for key in ('source_sha256', 'reconciliation_record_sha256'):
        _hash_field(value, key)
    if type(value.get('source_last_seq')) is not int or value['source_last_seq'] < 1:
        raise LedgerError('source_last_seq は正の整数が必要です')
    _contract_time(value.get('reconciled_at'), 'reconciled_at')
    return _contract_time(value.get('cutover_at'), 'cutover_at')


def _settlement_key(pl):
    if not isinstance(pl.get('note'), str):
        return None
    try:
        note = json.loads(pl['note'])
    except ValueError:
        return None
    if not isinstance(note, dict) or note.get('category') != 'POST_EXIT_SETTLEMENT':
        return None
    if pl.get('kind') != 'DIVIDEND':
        raise LedgerError('category=POST_EXIT_SETTLEMENT の note は DIVIDEND でのみ記録できます（DEPOSIT は外部入金専用）')
    _required_text(note, ('settlement_type', 'code', 'source_event_id', 'reconciliation_id', 'actor', 'reason'))
    if note['settlement_type'] not in ('FRACTIONAL_CASHOUT', 'LIQUIDATION_CASH'):
        raise LedgerError('settlement_type が不正です')
    _hash_field(note, 'source_document_sha256')
    received = _contract_time(note.get('received_at'), 'received_at')
    if note['code'] != pl.get('code') or received != _contract_time(pl.get('at'), 'at'):
        raise LedgerError('精算noteのcode/received_atと引数が一致しません')
    if type(pl.get('amount')) is not int or pl['amount'] <= 0:
        raise LedgerError('精算金は正の整数円が必要です')
    return (note['category'], note['source_event_id'].strip())   # 前後空白は同一キーとして扱う（v0.3.5）


# ---- 状態 ---------------------------------------------------------------------

class _State:
    def __init__(self):
        self.rule_version = 3
        self.initialized = False
        self.snapshot_at: datetime | None = None
        self.cash = 0
        self.positions: dict[str, dict] = {}       # code -> {qty, cost(Decimal), stop_order, stop_price}
        self.notices: dict[str, dict] = {}         # proposal_id -> notice（外部注文も含む）
        self.seen: set[str] = set()                # 適用・保留として永続化した外部 event_id
        self.pending: dict[str, dict] = {}         # 照合待ちの CSV 行（event_id -> row）
        self.discarded: set[str] = set()           # DISCARD 済みの外部 event_id（再送は DUPLICATE/DISCARDED。v0.3.5）
        self.fee_margin = Decimal("0.002")
        self.policy: dict = {}
        self.cutover_at: datetime | None = None
        self.settlement_keys: set[tuple[str, str]] = set()

    # ---- 予約 ----
    def reserved(self) -> int:
        return sum(n["reserve"] for n in self.notices.values())

    def available(self) -> int:
        return self.cash - self.reserved()

    def reserved_positions(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.notices.values():
            if n["reserve"] > 0:
                out[n["proposal"]["code"]] = out.get(n["proposal"]["code"], 0) + n["reserve"]
        return out

    def reserved_shares(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.notices.values():
            if n["reserved_shares"] > 0:
                out[n["proposal"]["code"]] = out.get(n["proposal"]["code"], 0) + n["reserved_shares"]
        return out

    def _recalc(self, n: dict) -> None:
        """trade_state・予約額・予約株数を導出する（唯一の導出点）"""
        p = n["proposal"]
        total = int(p["qty"])
        if n["closed"]:
            n["trade_state"] = n["closed_state"]
            n["reserve"] = 0
            n["reserved_shares"] = 0
            return
        if n["filled_qty"] >= total:
            n["closed"], n["closed_state"] = True, "FILLED"
            n["trade_state"] = "FILLED"
            n["reserve"] = 0
            n["reserved_shares"] = 0
            return
        if n["filled_qty"] > 0:
            n["trade_state"] = "PARTIAL"
        elif n["ordered"]:
            n["trade_state"] = "ORDERED"
        else:
            n["trade_state"] = "UNCONFIRMED"
        remaining = total - n["filled_qty"]
        open_ = n["notice_state"] != "REJECTED" and not n.get("context_only", False)
        n["reserve"] = reserve_amount(remaining, p["limit_price"], self.fee_margin) if (p["side"] == "BUY" and open_) else 0
        n["reserved_shares"] = remaining if (p["side"] == "SELL" and open_) else 0

    def _assert_invariants(self) -> None:
        if self.cash < 0:
            raise LedgerError(f"現金が負になります（{self.cash:,} 円）")
        if self.available() < 0:
            raise LedgerError(f"余力が負になります（現金 {self.cash:,} − 予約 {self.reserved():,}）")
        for code, pos in self.positions.items():
            if pos["qty"] < 0:
                raise LedgerError(f"{code} の保有株数が負になります")
        rs = self.reserved_shares()
        for code, shares in rs.items():
            held = self.positions.get(code, {}).get("qty", 0)
            if shares > held:
                raise LedgerError(f"{code} の売却予約 {shares} 株が保有 {held} 株を超えます（二重売却）")

    # ---- イベント適用 ----
    def apply(self, kind: str, payload: dict) -> Any:
        result = getattr(self, f"_on_{kind.lower()}")(payload)
        self._assert_invariants()
        return result

    def _require_init(self):
        if not self.initialized:
            raise LedgerNotInitialized("初期スナップショット（init_snapshot）が未登録です")

    def _on_policy_upgrade(self, pl):
        self._require_init()
        if self.rule_version >= 3:
            raise LedgerError('既に新規則の台帳へ移行マーカーは追加できません')
        if pl.get('schema_version') != 3:
            raise LedgerError('未対応の移行先規則版です')
        self.rule_version = 3
        self.policy['schema_version'] = 3

    def _new_notice(self, proposal: dict, at: Any, notice_state: str) -> dict:
        return {
            "proposal": proposal, "notice_state": notice_state, "trade_state": "UNCONFIRMED",
            "created_at": at, "filled_qty": 0, "fills": [], "reserve": 0, "reserved_shares": 0,
            "ordered": notice_state == "EXTERNAL", "closed": False, "closed_state": None,
            "history": [(notice_state, at)],
        }

    def _on_snapshot(self, pl: dict):
        if self.initialized:
            raise LedgerError("スナップショットは既に登録済みです（再登録は adjust で調整してください）")
        cash = pl["cash"]
        if isinstance(cash, bool) or not isinstance(cash, int) or cash < 0:
            raise LedgerError("cash は 0 以上の整数（円）")
        self.fee_margin = Decimal(str(pl.get("fee_margin", "0.002")))
        self.policy = dict(pl.get("policy") or {})
        self.cash = cash
        for pos in pl.get("positions") or []:
            qty = _int_qty(pos["qty"], "qty")
            if qty <= 0:
                raise LedgerError("スナップショットの保有数は正")
            self.positions[str(pos["code"])] = {
                "qty": qty, "cost": _money(pos.get("avg_price", 0), "avg_price") * qty,
                "stop_order": bool(pos.get("stop_order", False)), "stop_price": pos.get("stop_price"),
            }
        self.snapshot_at = _dt(pl["at"])
        self.cutover_at = _validate_provenance(pl.get('provenance'))
        if self.cutover_at is not None and self.cutover_at > self.snapshot_at:
            raise LedgerError('provenance.cutover_at は開始残高の at 以前が必要です（旧台帳の停止時点 ≤ 開始残高の時点）')
        self.initialized = True
        # 外部注文（発注済み・未約定）を EXTERNAL 通知として登録し予約に含める（R06/R07）
        for i, o in enumerate(pl.get("open_orders") or []):
            pid = o.get("proposal_id") or f"external-{i+1}"
            if pid in self.notices:
                raise LedgerError(f"open_orders の ID が重複: {pid}")
            side = o.get("side")
            if side not in ("BUY", "SELL"):
                raise LedgerError("open_orders の side は BUY/SELL")
            qty = _int_qty(o.get("qty"), "open_orders.qty")
            if qty <= 0:
                raise LedgerError("open_orders の qty は正")
            proposal = {
                "proposal_id": pid, "packet_hash": "", "code": str(o["code"]), "side": side, "qty": qty,
                "lot_size": o.get("lot_size", 1), "limit_price": float(_money(o.get("limit_price", 0), "limit_price")),
                "exec_condition": "EXTERNAL", "account_type": "CASH", "strategy": "external",
                "strategy_version": "", "as_of": pl["at"][:10] if isinstance(pl["at"], str) else str(pl["at"]),
                "snapshot_id": "snapshot", "policy_version": "", "expires_at": None,
                "reason": "初期スナップショットの発注済み注文", "events": {}, "broker_order_id": o.get("broker_order_id"),
            }
            n = self._new_notice(proposal, pl["at"], "EXTERNAL")
            self.notices[pid] = n
            self._recalc(n)

    def _on_notice_created(self, pl: dict):
        self._require_init()
        p = pl["proposal"]
        pid = p["proposal_id"]
        if pid in self.notices:
            raise LedgerError(f"通知 {pid} は既に存在します（再作成で予約が二重になるのを防止）")
        if p["side"] not in ("BUY", "SELL"):
            raise LedgerError("現物限定: side は BUY/SELL")
        qty = _int_qty(p["qty"], "qty")
        if qty <= 0:
            raise LedgerError("qty は正")
        _money(p["limit_price"], "limit_price")
        n = self._new_notice(p, pl["at"], "CREATED")
        self.notices[pid] = n
        self._recalc(n)
        # 余力・売却可能株数は _assert_invariants で検査（R04 / R07）

    def _on_notice_state(self, pl: dict):
        self._require_init()
        n = self.notices.get(pl["proposal_id"])
        if n is None:
            raise LedgerError(f"通知 {pl['proposal_id']} がありません")
        new = pl["state"]
        if new not in NOTICE_STATES:
            raise LedgerError(f"不明な通知状態 {new}")
        if new not in _NOTICE_TRANSITIONS[n["notice_state"]]:
            raise LedgerError(f"通知状態の遷移が不正: {n['notice_state']} → {new}")
        n["notice_state"] = new
        n["history"].append((new, pl["at"]))
        if new == "REJECTED":
            n["closed"], n["closed_state"] = True, "SKIPPED"
        self._recalc(n)

    def _on_expire(self, pl: dict):
        for pid in pl["proposal_ids"]:
            n = self.notices[pid]
            if n["notice_state"] == "SENT":
                n["notice_state"] = "EXPIRED"
                n["history"].append(("EXPIRED", pl["at"]))

    # --- 約定の内部処理 ---
    @staticmethod
    def _fill_matches(f: dict, broker: str | None, qty: int, price: Decimal, at: datetime | None) -> bool:
        if f["reversed"] or broker is None or f["broker_order_id"] != broker:
            return False
        if f["qty"] != qty or Decimal(f["price"]) != price:
            return False
        if at is not None and f.get("at") and _dt(f["at"]) != at:
            return False
        return True

    def _find_fill_anywhere(self, broker: str | None, qty: int, price: Decimal, at: datetime | None,
                            code: str, side: str) -> tuple[dict, dict] | None:
        """全通知を横断して同じ約定キーを探す（R12）"""
        for n in self.notices.values():
            if self.rule_version >= 3 and (n['proposal']['code'] != code or n['proposal']['side'] != side):
                continue
            for f in n["fills"]:
                if self._fill_matches(f, broker, qty, price, at):
                    return n, f
        return None

    def _similar_fill_exists(self, n: dict, qty: int, price: Decimal) -> bool:
        return any(not f["reversed"] and f["qty"] == qty and Decimal(f["price"]) == price for f in n["fills"])

    def _apply_fill(self, n: dict, *, event_id: str, qty: int, price: Decimal, fee: Decimal,
                    at: Any, broker: str | None, source: str, kind: str,
                    original_sell_cost: Decimal | None = None) -> list[str]:
        warnings: list[str] = []
        p = n["proposal"]
        if n["closed"] and n["closed_state"] in ("CANCELLED", "SKIPPED"):
            raise LedgerError(f"{n['closed_state']} の注文に約定報告はできません")
        if qty <= 0:
            raise LedgerError("約定数量は正")
        if price <= 0:
            raise LedgerError("約定単価は正")
        if fee < 0:
            raise LedgerError("手数料は 0 以上")
        remaining = int(p["qty"]) - n["filled_qty"]
        if qty > remaining:
            raise LedgerError(f"約定数量 {qty} が残数量 {remaining} を超える")
        code = p["code"]
        if p["side"] == "BUY":
            cash_delta = -yen(Decimal(qty) * price + fee)
            pos = self.positions.setdefault(code, {"qty": 0, "cost": Decimal(0), "stop_order": False, "stop_price": None})
            cost_delta = Decimal(-cash_delta)
            pos["qty"] += qty
            pos["cost"] += cost_delta
            self.cash += cash_delta
        else:
            pos = self.positions.get(code)
            if pos is None or pos["qty"] < qty:
                raise LedgerError("保有数不足のため売却約定を記録できません")
            avg = pos["cost"] / pos["qty"]
            cost_delta = original_sell_cost if original_sell_cost is not None else -(avg * qty)
            pos["qty"] -= qty
            pos["cost"] += cost_delta
            if pos["qty"] == 0:
                del self.positions[code]
            cash_delta = yen(Decimal(qty) * price - fee)
            self.cash += cash_delta
        n["filled_qty"] += qty
        n["fills"].append({
            "event_id": event_id, "qty": qty, "price": str(price), "fee": str(fee), "at": at,
            "broker_order_id": broker, "source": source, "kind": kind, "reversed": False,
            "cash_delta": cash_delta, "cost_delta": str(cost_delta),
        })
        was_closed = n["closed"]
        self._recalc(n)
        if not was_closed and n["trade_state"] == "PARTIAL" and kind == "FILLED":
            warnings.append(f"FILLED 報告ですが残数量 {int(p['qty']) - n['filled_qty']} 株があるため PARTIAL として記録（予約は残します）")
        return warnings

    def _reverse_fill(self, n: dict, f: dict) -> None:
        p = n["proposal"]
        code = p["code"]
        qty = f["qty"]
        cost_delta = Decimal(f["cost_delta"])
        if p["side"] == "BUY":
            pos = self.positions.get(code)
            if pos is None or pos["qty"] < qty:
                raise LedgerError("訂正対象の株数が保有にありません（売却済み？）")
            pos["qty"] -= qty
            pos["cost"] -= cost_delta
            if pos["qty"] == 0:
                del self.positions[code]
        else:
            pos = self.positions.setdefault(code, {"qty": 0, "cost": Decimal(0), "stop_order": False, "stop_price": None})
            pos["qty"] += qty
            pos["cost"] -= cost_delta                      # 取得原価を戻す（売却額ではない。R01）
        self.cash -= f["cash_delta"]
        n["filled_qty"] -= qty
        f["reversed"] = True
        # closed（取消・完了）の注文は訂正で再開しない（R02）
        if n["closed"] and n["closed_state"] == "FILLED" and n["filled_qty"] < int(p["qty"]):
            pass  # 完了済みのまま。予約は復活させない
        self._recalc(n)

    def _on_trade(self, pl: dict) -> ReportResult:
        self._require_init()
        ev = pl
        event_id = str(ev["event_id"])
        res = ReportResult(event_id=event_id, applied=False)
        if event_id in self.seen or event_id in self.pending:
            res.duplicate = True
            res.ignored_reason = "同じ event_id を受信済み（冪等）"
            return res
        if event_id in self.discarded:
            res.duplicate = True
            res.ignored_reason = "DISCARD 済みの event_id の再送。適用も保留もしない（v0.3.5）"
            return res
        blocked = self._cutover_reason(ev.get('at'))
        if blocked:
            self.pending[event_id] = dict(ev, _reason=blocked, _cutover_block=True, _event_kind='TRADE')
            res.pending = True
            res.ignored_reason = blocked
            return res
        n = self.notices.get(ev.get("proposal_id"))
        if n is None:
            raise LedgerError(f"通知 {ev.get('proposal_id')} がありません")
        kind = ev.get("kind")
        if kind not in TRADE_EVENT_KINDS:
            raise LedgerError(f"不明な報告種別 {kind!r}")
        at = ev.get("at")
        at_dt = _dt(at) if at is not None else None
        qty = _int_qty(ev.get("qty", 0), "qty")
        price = _money(ev.get("price", 0), "price")
        fee = _money(ev.get("fee", 0), "fee")
        broker = ev.get("broker_order_id")
        source = ev.get("source") or "line"

        if kind == "ORDERED":
            if qty < 0 or price < 0 or fee < 0:
                raise LedgerError("負の値は受け付けません")
            if n["trade_state"] == "UNCONFIRMED":
                n["ordered"] = True
                n["history"].append(("ORDERED", at))
                self._recalc(n)
                res.applied = True
            else:
                res.ignored_reason = f"状態 {n['trade_state']} に対する発注報告は無視（遅延到着）"
        elif kind in ("PARTIAL", "FILLED"):
            hit = self._find_fill_anywhere(broker, qty, price, at_dt, n['proposal']['code'], n['proposal']['side'])
            if hit is not None:
                _, f = hit
                if self.rule_version >= 3 and (at_dt is None or not f.get('at')):
                    raise LedgerError('約定時刻が不足しており重複を確定できません。照合が必要です')
                res.duplicate = True
                res.ignored_reason = "同じ証券注文ID・数量・単価の約定を記録済み（CSV/LINE の重複）"
                if Decimal(f["fee"]) != fee:
                    res.warnings.append(f"手数料が記録 {f['fee']} と異なる（{fee}）。照合が必要")
            else:
                res.warnings += self._apply_fill(n, event_id=event_id, qty=qty, price=price, fee=fee, at=at,
                                                 broker=broker, source=source, kind=kind)
                res.applied = True
        elif kind == "CANCELLED":
            if qty < 0 or price < 0 or fee < 0:
                raise LedgerError("負の値は受け付けません")
            remaining = int(n["proposal"]["qty"]) - n["filled_qty"]
            if qty > remaining:
                raise LedgerError(f"取消数量 {qty} が残数量 {remaining} を超える")
            if n["closed"]:
                res.ignored_reason = f"状態 {n['trade_state']} に対する取消報告は無視"
            else:
                n["closed"], n["closed_state"] = True, ("FILLED" if n["filled_qty"] > 0 else "CANCELLED")
                if n["filled_qty"] > 0:
                    res.warnings.append(f"一部約定 {n['filled_qty']} 株のまま残り {remaining} 株を取消（完了扱い）")
                n["history"].append(("CANCELLED", at))
                self._recalc(n)
                res.applied = True
        elif kind == "SKIPPED":
            if n["filled_qty"] > 0:
                raise LedgerError("約定済みの注文を見送りにはできません（取消報告を使ってください）")
            if n["closed"]:
                res.ignored_reason = f"状態 {n['trade_state']} に対する見送り報告は無視"
            else:
                n["closed"], n["closed_state"] = True, "SKIPPED"
                n["history"].append(("SKIPPED", at))
                self._recalc(n)
                res.applied = True
        elif kind == "CORRECTION":
            target_id = ev.get("replaces_event_id")
            if target_id:
                target = next((f for f in n["fills"] if f["event_id"] == target_id and not f["reversed"]), None)
                if target is None:
                    raise LedgerError(f"訂正対象 {target_id} が見つからないか既に訂正済み")
            else:
                live = [f for f in n["fills"] if not f["reversed"]]
                if not live:
                    raise LedgerError("訂正対象となる約定報告がありません")
                target = live[-1]
            # Price/fee-only SELL corrections retain the original acquisition cost.
            original_sell_cost = None
            if self.rule_version >= 3 and n['proposal']['side'] == 'SELL':
                if (qty or target['qty']) != target['qty']:
                    raise LedgerError('SELL数量の訂正は後続取引の再計算契約が必要です')
                original_sell_cost = Decimal(target['cost_delta'])
            self._reverse_fill(n, target)
            res.warnings += self._apply_fill(n, event_id=event_id, qty=qty or target["qty"], price=price, fee=fee,
                                             at=at, broker=broker if broker is not None else target["broker_order_id"],
                                             source=source, kind=target["kind"], original_sell_cost=original_sell_cost)
            res.applied = True
        if res.applied:
            self.seen.add(event_id)
        res.trade_state = n["trade_state"]
        return res

    def _cutover_reason(self, at):
        if self.cutover_at is None:
            return None
        if at is None:
            return '約定時刻がないためcutover境界を照合できません'
        if _contract_time(at, 'at') < self.cutover_at:
            return 'cutover 前の約定。照合済み開始残高との確認が必要です'
        return None

    def _on_csv_fill(self, pl: dict) -> tuple[str, str]:
        """returns (bucket, message)。bucket ∈ applied / skipped / pending"""
        self._require_init()
        row = pl
        event_id = str(row["event_id"])
        if event_id in self.seen or event_id in self.pending:
            return "skipped", "同じ event_id を取込済み"
        if event_id in self.discarded:
            return "skipped", "DISCARD 済みの event_id の再送。適用も保留もしない（v0.3.5）"
        blocked = self._cutover_reason(row.get('at'))
        if blocked:
            self.pending[event_id] = dict(row, _reason=blocked, _cutover_block=True, _event_kind='CSV_FILL')
            return 'pending', blocked
        qty = _int_qty(row.get("qty", 0), "qty")
        price = _money(row.get("price", 0), "price")
        fee = _money(row.get("fee", 0), "fee")
        broker = row.get("broker_order_id")
        at = row.get("at")
        at_dt = _dt(at) if at is not None else None
        # 1) 全履歴を横断した重複判定（R12）
        if row.get('_applied_proposal_id'):
            n = self.notices.get(row['_applied_proposal_id'])
            if n is None or n['proposal']['code'] != row.get('code') or n['proposal']['side'] != row.get('side'):
                raise LedgerError('確定済みCSVの紐付け先が不正です')
            self._apply_fill(n, event_id=event_id, qty=qty, price=price, fee=fee, at=at,
                             broker=broker, source='csv', kind='FILLED')
            self.seen.add(event_id)
            return 'applied', ''
        if self.rule_version >= 3 and at_dt is None:
            self.pending[event_id] = dict(row, _reason='約定時刻がないため適用できません')
            return 'pending', '約定時刻不足'
        hit = self._find_fill_anywhere(broker, qty, price, at_dt, row.get('code'), row.get('side'))
        if hit is not None:
            _, f = hit
            if self.rule_version >= 3 and (at_dt is None or not f.get('at')):
                self.pending[event_id] = dict(row, _reason='約定時刻が不足しており同一約定と確定できません')
                return 'pending', '時刻精度不足'
            if Decimal(f["fee"]) != fee:
                self.pending[event_id] = dict(row, _reason="約定キーは一致するが手数料が異なる。照合が必要（R13）")
                return "pending", "手数料差あり。照合待ち"
            return "skipped", "同じ証券注文IDの約定を記録済み（LINE 報告と一致）"
        # 2) 紐付け先の決定
        pid = row.get("proposal_id")
        n = self.notices.get(pid) if pid else None
        if pid and n is None:
            self.pending[event_id] = dict(row, _reason=f"proposal_id {pid} が台帳にない")
            return "pending", "不明な proposal_id"
        if n is not None:
            if n["proposal"]["code"] != row.get("code") or n["proposal"]["side"] != row.get("side"):
                self.pending[event_id] = dict(row, _reason="proposal_id の銘柄・売買方向と CSV 行が一致しない（R11）")
                return "pending", "銘柄または売買方向が通知と不一致"
        else:
            candidates = [x for x in self.notices.values()
                          if x["proposal"]["code"] == row.get("code") and x["proposal"]["side"] == row.get("side")
                          and not x["closed"]]
            if len(candidates) != 1:
                self.pending[event_id] = dict(row, _reason="対応する未決済通知を一意に特定できない")
                return "pending", "対応する通知を一意に特定できない"
            n = candidates[0]
        if broker is None and self._similar_fill_exists(n, qty, price):
            self.pending[event_id] = dict(row, _reason="証券注文IDがなく既存約定と同じ数量・単価（曖昧一致）")
            return "pending", "曖昧一致。人間の確認待ち"
        self._apply_fill(n, event_id=event_id, qty=qty, price=price, fee=fee, at=at, broker=broker,
                         source="csv", kind="FILLED")
        self.seen.add(event_id)
        row['_applied_proposal_id'] = n['proposal']['proposal_id']
        return "applied", ""

    def _on_pending_resolved(self, pl: dict):
        """保留行を人間の判断で紐付けて適用、または破棄"""
        self._require_init()
        eid = str(pl["source_event_id"])
        row = self.pending.get(eid)
        if row is None:
            raise LedgerError(f"保留行 {eid} がありません")
        if self.rule_version >= 3 and pl.get('action') not in ('APPLY', 'DISCARD'):
            raise LedgerError('actionはAPPLY/DISCARDが必要です')
        if self.rule_version >= 3 and row.get('at') is not None and pl.get('at') is not None \
                and _dt(pl['at']) < _dt(row['at']):
            # 解決の業務時刻は保留行の業務時刻以後に限る（v0.3.6 / L03）。約定より前に人が照合することはなく、
            # これを許すと時点再生で「保留行がない時刻に解決だけが現れる」履歴になる。時刻なし保留行には制約なし。
            raise LedgerError('保留行の業務時刻より前の時刻では解決（APPLY/DISCARD）できません')
        if pl.get("action") == "DISCARD":
            del self.pending[eid]
            self.discarded.add(eid)
            return
        if row.get('_cutover_block'):
            raise LedgerError('cutover遮断行はAPPLYできません。旧台帳と照合しDISCARD、または別途訂正を検討してください')
        n = self.notices.get(pl["proposal_id"])
        if n is None:
            raise LedgerError(f"通知 {pl['proposal_id']} がありません")
        if self.rule_version >= 3 and (row.get('code') != n['proposal']['code'] or row.get('side') != n['proposal']['side']):
            raise LedgerError('保留行と通知の銘柄・売買方向が一致しません')
        hit = self._find_fill_anywhere(row.get('broker_order_id'), row['qty'], _money(row['price'], 'price'),
                                      _dt(row['at']) if row.get('at') else None, row['code'], row['side'])
        if self.rule_version >= 3 and hit is not None:
            raise LedgerError('既存約定に一致する保留行は追加適用できません。訂正または照合が必要です')
        self._apply_fill(n, event_id=eid, qty=_int_qty(row["qty"], "qty"), price=_money(row["price"], "price"),
                         fee=_money(row.get("fee", 0), "fee"), at=row.get("at"), broker=row.get("broker_order_id"),
                         source="csv", kind="FILLED")
        del self.pending[eid]
        self.seen.add(eid)

    def _on_stop_order(self, pl: dict):
        self._require_init()
        pos = self.positions.get(pl["code"])
        if pos is None:
            raise LedgerError(f"保有していない銘柄 {pl['code']} に逆指値は記録できません")
        pos["stop_order"] = bool(pl["has_stop"])
        pos["stop_price"] = pl.get("stop_price")

    def _on_adjust(self, pl: dict):
        self._require_init()
        settlement_key = _settlement_key(pl)
        if settlement_key is not None:
            if settlement_key in self.settlement_keys:
                raise LedgerError('同じ(category, source_event_id)の精算金を記録済みです')
            if pl.get('code') in self.positions:
                raise LedgerError('保有中の銘柄に POST_EXIT_SETTLEMENT は記録できません。端株精算は FRACTIONAL_CASHOUT を使ってください')
            self.settlement_keys.add(settlement_key)
        kind = pl["kind"]
        amount = pl.get("amount")
        if kind in ("DEPOSIT", "DIVIDEND", "WITHDRAW", "FEE", "FRACTIONAL_CASHOUT"):
            if kind == 'FRACTIONAL_CASHOUT' and (not pl.get('code') or pl['code'] not in self.positions):
                raise LedgerError('端株精算は保有銘柄のcodeが必要です')
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise LedgerError("amount は 0 以上の整数（円）")
            self.cash += amount if kind in ("DEPOSIT", "DIVIDEND", "FRACTIONAL_CASHOUT") else -amount
            # 余力が負になる出金は _assert_invariants で拒否（R03）
        elif kind == "SPLIT":
            if self.rule_version >= 3 and any(n['proposal']['code'] == pl.get('code') and not n['closed'] for n in self.notices.values()):
                raise LedgerError('注文中の分割は注文条件の照合が必要です')
            pos = self.positions.get(pl.get("code"))
            ratio = pl.get("ratio")
            if pos is None or not ratio or Decimal(str(ratio)) <= 0:
                raise LedgerError("SPLIT には保有銘柄と正の ratio が必要")
            exact_qty = Decimal(pos['qty']) * _money(ratio, 'ratio')
            if self.rule_version >= 3 and exact_qty != exact_qty.to_integral_value():
                raise LedgerError('端株が発生するSPLITは拒否します。株数・端株精算の照合が必要です')
            new_qty = int(exact_qty)
            if new_qty <= 0:
                raise LedgerError("分割後の株数が 0")
            pos["qty"] = new_qty
        else:
            raise LedgerError(f"不明な調整種別 {kind}")


# ---- 台帳 ---------------------------------------------------------------------

class Ledger:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS ledger_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        at TEXT,
        payload TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ingest_attempts (
        attempt_id TEXT PRIMARY KEY,
        source_event_id TEXT,
        source TEXT,
        received_at TEXT NOT NULL,
        business_at TEXT,
        payload_hash TEXT,
        proposal_id TEXT,
        outcome TEXT NOT NULL,
        reason_code TEXT,
        detail TEXT,
        ledger_seq INTEGER,
        schema_version INTEGER
    );
    """

    def __init__(self, path: Path | str, fee_margin: float = 0.002):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path), isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(self.SCHEMA)
        self._init_fee_margin = Decimal(str(fee_margin))
        self._state = _State()
        self._last_seq = -1
        try:
            self._replay_all()
        except Exception:
            self._con.close()
            raise

    # --- 永続化 ---
    def _max_seq(self) -> int:
        row = self._con.execute("SELECT COALESCE(MAX(seq), 0) FROM ledger_events").fetchone()
        return int(row[0])

    def _replay_all(self) -> None:
        rows = self._con.execute("SELECT seq, kind, payload FROM ledger_events ORDER BY seq").fetchall()
        self._state = self._rebuild(rows)
        self._last_seq = rows[-1][0] if rows else 0

    def _rebuild(self, rows, stop_seq=None, legacy=False):
        self._validate_markers(rows)
        state = _State()
        state.fee_margin = self._init_fee_margin
        if legacy or any(kind == 'POLICY_UPGRADE' for _, kind, _ in rows):
            state.rule_version = 2
        for seq, kind, payload in rows:
            if stop_seq is not None and seq > stop_seq:
                break
            try:
                state.apply(kind, json.loads(payload))
            except (LedgerError, ValueError) as exc:
                raise MigrationError(seq, kind, str(exc)) from exc
        return state

    @staticmethod
    def _validate_markers(rows):
        seen, previous = False, 0
        for seq, kind, payload in rows:
            if kind == 'POLICY_UPGRADE':
                try:
                    marker = json.loads(payload)
                    boundary = marker.get('legacy_last_seq')
                    if seen or type(boundary) is not int or boundary != previous:
                        raise ValueError('移行マーカーは1個限定で、legacy_last_seqは直前の履歴番号と一致する必要があります')
                    if marker.get('schema_version') != 3:
                        raise ValueError('未対応の移行先規則版です')
                except (ValueError, TypeError, AttributeError) as exc:
                    raise MigrationError(seq, kind, str(exc)) from exc
                seen = True
            previous = seq

    def _sync(self) -> None:
        """別ハンドルの追記を取り込む（R15）"""
        if self._max_seq() != self._last_seq:
            self._replay_all()

    def replay(self, at: datetime | None = None) -> LedgerView:
        """業務時刻（effective_at）が at 以前のイベントだけを再生した残高ビュー（R14）"""
        state = _State()
        state.fee_margin = self._init_fee_margin
        limit = _dt(at) if at is not None else None
        stored = self._con.execute("SELECT seq, kind, at, payload FROM ledger_events ORDER BY seq").fetchall()
        self._validate_markers([(seq, kind, payload) for seq, kind, _, payload in stored])
        rows = [(kind, at, payload) for _, kind, at, payload in stored]
        # 開始残高（SNAPSHOT）の業務時刻より前の時点は、この台帳が存在しない時点。後着した保留行や遅着通知があっても
        # 空の台帳（現金 0・保有/予約なし）を返し、旧台帳の残高を混入させない（契約 (c)、v0.3.6 / L02）。
        snapshot_at = next((_dt(ev_at) for kind, ev_at, _ in rows if kind == 'SNAPSHOT' and ev_at), None)
        if limit is not None and snapshot_at is not None and snapshot_at > limit:
            return self._view_of(state)
        if any(kind == 'POLICY_UPGRADE' for kind, _, _ in rows):
            state.rule_version = 2
        needed = {(json.loads(payload).get('_applied_proposal_id') if kind == 'CSV_FILL' else json.loads(payload).get('proposal_id')) for kind, ev_at, payload in rows
                  if kind in ('TRADE', 'CSV_FILL') and (limit is None or not ev_at or _dt(ev_at) <= limit)}
        for kind, ev_at, payload in rows:
            if kind == 'POLICY_UPGRADE':
                # Policy is a recorded-order boundary, not a monetary event.
                state.rule_version = json.loads(payload)['schema_version']
                continue
            if limit is not None and ev_at and _dt(ev_at) > limit:
                # Keep only the identity needed by an already-effective fill.
                # Its future notice reservation must not leak into this view.
                pl = json.loads(payload)
                if kind == 'NOTICE_CREATED' and pl['proposal']['proposal_id'] in needed:
                    state._on_notice_created(pl)
                    n = state.notices[pl['proposal']['proposal_id']]
                    n['context_only'] = True
                    state._recalc(n)
                continue
            state.apply(kind, json.loads(payload))
        return self._view_of(state)

    def replay_known(self, seq: int) -> LedgerView:
        """記録順で seq 以下のイベントを再生（「その時点でシステムが知っていた残高」）"""
        rows = self._con.execute('SELECT seq,kind,payload FROM ledger_events ORDER BY seq').fetchall()
        return self._view_of(self._rebuild(rows, stop_seq=seq))

    def seq(self) -> int:
        self._sync()
        return self._last_seq

    def _audit(self, *, source_event_id, source, business_at, payload, proposal_id, outcome, reason_code, detail, ledger_seq=None):
        self._con.execute(
            "INSERT INTO ingest_attempts(attempt_id, source_event_id, source, received_at, business_at, payload_hash, "
            "proposal_id, outcome, reason_code, detail, ledger_seq, schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, source_event_id, source, datetime.now(timezone.utc).isoformat(),
             _to_plain(business_at) if business_at is not None else None, _payload_hash(payload) if payload is not None else None,
             proposal_id, outcome, reason_code, detail, ledger_seq, SCHEMA_VERSION),
        )

    def _commit(self, kind: str, payload: dict, at: Any, event_id: str | None = None, persist_if=None) -> Any:
        """Validate under the write lock; event and ingestion audit commit together."""
        plain = _external_payload(_to_plain(payload))
        audit_plain = copy.deepcopy(plain)
        self._con.execute("BEGIN IMMEDIATE")
        try:
            self._sync()
            trial = copy.deepcopy(self._state)
            try:
                result = trial.apply(kind, plain)
            except (LedgerError, ValueError) as exc:
                try:
                    self._ingest_audit(kind, audit_plain, 'REJECTED', 'VALIDATION', str(exc))
                except Exception as audit_error:
                    raise audit_error from exc
                self._con.execute('COMMIT')
                if isinstance(exc, LedgerError):
                    raise
                raise LedgerError(str(exc)) from exc
            persist = persist_if is None or persist_if(result)
            seq = self._last_seq
            if persist:
                try:
                    cur = self._con.execute(
                        'INSERT INTO ledger_events(event_id, kind, at, payload, recorded_at) VALUES (?,?,?,?,?)',
                        (event_id or f'sys:{uuid.uuid4().hex}', kind, _to_plain(at), json.dumps(plain, ensure_ascii=False),
                         datetime.now(timezone.utc).isoformat()))
                except sqlite3.IntegrityError as exc:
                    # 履歴 ID の衝突は台帳の不変条件違反として LedgerError に畳む（生の DB 例外を呼出側へ漏らさない。v0.3.5）
                    raise LedgerError(f'履歴 ID {event_id} は既に記録されています: {exc}') from exc
                seq = cur.lastrowid
            if kind == 'TRADE':
                outcome = 'PENDING' if result.pending else ('APPLIED' if result.applied else ('DUPLICATE' if result.duplicate else 'IGNORED'))
                detail = result.ignored_reason or '; '.join(result.warnings)
            elif kind == 'CSV_FILL':
                outcome = {'applied': 'APPLIED', 'pending': 'PENDING', 'skipped': 'DUPLICATE'}[result[0]]
                detail = result[1]
            else:
                outcome, detail = 'APPLIED', ''
            code = None if outcome == 'APPLIED' else outcome
            if kind in ('TRADE', 'CSV_FILL') and outcome == 'DUPLICATE':
                if str(plain.get('event_id')) in trial.discarded:
                    code = 'DISCARDED'
                old = self._con.execute("SELECT payload_hash FROM ingest_attempts WHERE source_event_id=? AND outcome IN ('APPLIED','PENDING') ORDER BY rowid LIMIT 1",
                                        [plain.get('event_id')]).fetchone()
                if old and old[0] != _payload_hash(audit_plain):
                    code = 'ID_PAYLOAD_CONFLICT'
            self._ingest_audit(kind, audit_plain, outcome, code, detail, seq if persist else None)
            self._con.execute("COMMIT")
        except Exception:
            if self._con.in_transaction:
                self._con.execute("ROLLBACK")
            raise
        if persist:
            self._state = trial
            self._last_seq = seq
        return result

    def _ingest_audit(self, kind, payload, outcome, code, detail, seq=None):
        if kind not in ('TRADE', 'CSV_FILL', 'PENDING_RESOLVED'):
            return
        if kind == 'PENDING_RESOLVED':
            detail = json.dumps({'action': payload.get('action'), 'actor': payload.get('actor', 'unspecified'),
                                 'reason': payload.get('reason', ''), 'detail': detail}, ensure_ascii=False)
        self._audit(source_event_id=payload.get('event_id') or payload.get('source_event_id'),
                    source='csv' if kind == 'CSV_FILL' else payload.get('source', 'manual'),
                    business_at=payload.get('at'), payload=payload, proposal_id=payload.get('proposal_id'),
                    outcome=outcome, reason_code=code, detail=detail, ledger_seq=seq)

    # --- 公開API ---
    def init_snapshot(self, cash: int, positions: list, open_orders: list, at: datetime, provenance=None) -> None:
        at = as_aware(at)
        self._commit("SNAPSHOT", {"cash": cash, "positions": [_to_plain(p) for p in positions],
                                  "open_orders": [_to_plain(o) for o in open_orders], "at": at,
                                  "provenance": provenance,
                                  "fee_margin": str(self._init_fee_margin),
                                  "policy": {"schema_version": SCHEMA_VERSION, "reserve_rule": "remaining_qty*limit*(1+fee_margin)"}}, at)

    def create_notice(self, p, at: datetime | None = None) -> None:
        proposal = _to_plain(p)
        expires = _get(p, "expires_at")
        as_aware(expires) if isinstance(expires, datetime) else _dt(expires)
        created_at = as_aware(at) if at is not None else datetime.now(JST)
        self._commit("NOTICE_CREATED", {"proposal": proposal, "at": created_at}, created_at)

    def set_notice_state(self, proposal_id: str, state: str, at: datetime) -> None:
        at = as_aware(at)
        self._commit("NOTICE_STATE", {"proposal_id": proposal_id, "state": state, "at": at}, at)

    def expire_notices(self, now: datetime) -> list[str]:
        now = as_aware(now)
        self._sync()
        ids = [pid for pid, n in self._state.notices.items()
               if n["notice_state"] == "SENT" and n["proposal"].get("expires_at") and _dt(n["proposal"]["expires_at"]) < now]
        if ids:
            self._commit("EXPIRE", {"proposal_ids": ids, "at": now}, now)
        return ids

    def report(self, ev) -> ReportResult:
        payload = _to_plain(ev)
        eid = str(payload.get("event_id") or "")
        if not eid:
            self._ingest_audit('TRADE', payload, 'REJECTED', 'MISSING_EVENT_ID', 'event_idが空です')
            return ReportResult(event_id="", applied=False, error="event_id が空です")
        source = payload.get("source") or "line"
        try:
            res = self._commit("TRADE", payload, payload.get("at"), event_id=f"trade:{eid}",
                               persist_if=lambda r: r.applied or r.pending)
        except LedgerNotInitialized:
            raise
        except LedgerError as exc:
            return ReportResult(event_id=eid, applied=False, error=str(exc))
        return res

    def import_csv_fills(self, rows: list) -> ImportResult:
        out = ImportResult()
        for row in rows:
            payload = _to_plain(row)
            eid = str(payload.get("event_id") or "")
            if not eid:
                self._ingest_audit('CSV_FILL', payload, 'REJECTED', 'MISSING_EVENT_ID', 'event_idが空です')
                out.errors["<no id>"] = "event_id が空です"
                continue
            try:
                bucket, msg = self._commit("CSV_FILL", payload, payload.get("at"), event_id=f"csv:{eid}",
                                           persist_if=lambda r: r[0] in ("applied", "pending"))
            except LedgerNotInitialized:
                raise
            except LedgerError as exc:
                out.errors[eid] = str(exc)
                continue
            getattr(out, bucket).append(eid)
        return out

    def pending_rows(self) -> dict[str, dict]:
        self._sync()
        return {k: dict(v) for k, v in self._state.pending.items()}

    def resolve_pending(self, source_event_id: str, proposal_id: str | None, at: datetime, action: str = "APPLY",
                        *, actor: str = 'unspecified', reason: str = '') -> None:
        """保留中の CSV 行を人間の判断で紐付け（APPLY）または破棄（DISCARD）する"""
        at = as_aware(at)
        self._commit("PENDING_RESOLVED", {"source_event_id": source_event_id, "proposal_id": proposal_id,
                                          "action": action, "at": at, 'actor': actor, 'reason': reason}, at)

    def unconfirmed(self, as_of: datetime) -> list[str]:
        as_of = as_aware(as_of)
        self._sync()
        return [pid for pid, n in self._state.notices.items()
                if n["notice_state"] in ("SENT", "EXPIRED", "EXTERNAL") and n["trade_state"] in _OPEN_TRADE_STATES
                and _dt(n["created_at"]) <= as_of]

    def cash(self) -> int:
        self._sync()
        return int(self._state.cash)

    def reserved(self) -> int:
        self._sync()
        return int(self._state.reserved())

    def available(self) -> int:
        self._sync()
        return int(self._state.available())

    def positions(self) -> dict[str, Position]:
        self._sync()
        return self._view_of(self._state).positions

    def set_stop_order(self, code: str, has_stop: bool, stop_price: float | None, at: datetime) -> None:
        at = as_aware(at)
        self._commit("STOP_ORDER", {"code": code, "has_stop": has_stop, "stop_price": stop_price, "at": at}, at)

    def adjust(self, kind: str, amount: int | None, code: str | None, ratio: float | None, at: datetime, note: str) -> None:
        at = as_aware(at)
        self._commit("ADJUST", {"kind": kind, "amount": amount, "code": code, "ratio": ratio, "at": at, "note": note}, at)

    def notice(self, proposal_id: str) -> dict:
        self._sync()
        n = self._state.notices[proposal_id]
        return {"notice_state": n["notice_state"], "trade_state": n["trade_state"], "filled_qty": n["filled_qty"],
                "reserve": n["reserve"], "reserved_shares": n["reserved_shares"],
                "fills": [dict(f) for f in n["fills"]], "history": list(n["history"])}

    def view(self, daily_pnl: int = 0, day_start_equity: int | None = None) -> LedgerView:
        self._sync()
        v = self._view_of(self._state)
        v.daily_pnl = daily_pnl
        v.day_start_equity = day_start_equity
        return v

    def policy(self) -> dict:
        self._sync()
        return {"fee_margin": str(self._state.fee_margin), **self._state.policy}

    @staticmethod
    def _view_of(state: _State) -> LedgerView:
        positions = {
            code: Position(code=code, qty=pos["qty"], avg_price=float(pos["cost"] / pos["qty"]) if pos["qty"] else 0.0,
                           stop_order=bool(pos["stop_order"]), stop_price=pos.get("stop_price"))
            for code, pos in state.positions.items() if pos["qty"] > 0
        }
        return LedgerView(cash=int(state.cash), reserved=int(state.reserved()), available=int(state.available()),
                          positions=positions, reserved_positions=state.reserved_positions(),
                          reserved_shares=state.reserved_shares())

    def close(self) -> None:
        self._con.close()
