"""台帳（共通仕様_フェーズ2 §3）— Claude 単独ビルド

イベントソーシング: すべての変更を ledger_events に追記し（更新・削除しない）、
現在状態は meta / notices / positions / fills の実体化テーブルに保持する。
1 操作 = 1 トランザクション。検証に失敗した操作は何も書かない。
"""
from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .models import JST, LedgerView, Position, reserve_amount

OPEN_TRADE = ("UNCONFIRMED", "ORDERED", "PARTIAL")
NOTICE_TRANSITIONS = {
    "CREATED": ("APPROVED", "REJECTED"),
    "APPROVED": ("SENT", "REJECTED"),
    "SENT": ("EXPIRED",),
}
REPORT_KINDS = ("ORDERED", "PARTIAL", "FILLED", "CANCELLED", "SKIPPED", "CORRECTION")
ADJUST_KINDS = ("DEPOSIT", "WITHDRAW", "DIVIDEND", "SPLIT")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  event_id TEXT,
  proposal_id TEXT,
  at TEXT,
  recorded_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_report_event ON ledger_events(event_id) WHERE event_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notices (
  proposal_id TEXT PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
  code TEXT PRIMARY KEY, data TEXT NOT NULL
);
"""


class LedgerError(Exception):
    pass


class LedgerNotInitialized(LedgerError):
    pass


@dataclass
class ReportResult:
    event_id: str | None
    applied: bool
    error: str | None = None
    duplicate: bool = False
    ignored_reason: str | None = None
    trade_state: str | None = None
    warnings: list[str] = field(default_factory=list)


def _iso(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _dt(s):
    return datetime.fromisoformat(s) if s else None


def _as_jst(d: datetime) -> datetime:
    if d.tzinfo is None:
        return d.replace(tzinfo=JST)
    return d.astimezone(JST)


def _dec(v) -> Decimal:
    return Decimal(str(v))


def _yen(v: Decimal) -> int:
    return int(v.to_integral_value(rounding=ROUND_HALF_UP))


class _Refused(Exception):
    pass


class Ledger:
    def __init__(self, path: Path, fee_margin: float = 0.002):
        self.path = Path(path)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None)
        self._conn.executescript(SCHEMA)
        self._fee_margin_default = fee_margin

    # ------------------------------------------------------------ state I/O
    def is_initialized(self) -> bool:
        r = self._conn.execute("SELECT value FROM meta WHERE key='initialized'").fetchone()
        return bool(r and r[0] == "1")

    def _require(self):
        if not self.is_initialized():
            raise LedgerNotInitialized("初期スナップショット未登録")

    def _load(self) -> dict:
        meta = dict(self._conn.execute("SELECT key, value FROM meta").fetchall())
        st = {
            "cash": int(meta.get("cash", "0")),
            "fee_margin": float(meta.get("fee_margin", self._fee_margin_default)),
            "notices": {k: json.loads(v) for k, v in self._conn.execute("SELECT proposal_id, data FROM notices")},
            "positions": {k: json.loads(v) for k, v in self._conn.execute("SELECT code, data FROM positions")},
        }
        return st

    def _save(self, st: dict, events: list[dict]):
        """state 全体と追記イベントを 1 トランザクションで書く。"""
        c = self._conn
        now = datetime.now(JST).isoformat()
        try:
            c.execute("BEGIN IMMEDIATE")
            for e in events:
                c.execute(
                    "INSERT INTO ledger_events(kind, event_id, proposal_id, at, recorded_at, payload) VALUES (?,?,?,?,?,?)",
                    (e["kind"], e.get("event_id"), e.get("proposal_id"), _iso(e.get("at")), now,
                     json.dumps(e, ensure_ascii=False, default=_iso, sort_keys=True)),
                )
            c.execute("INSERT OR REPLACE INTO meta VALUES ('cash', ?)", (str(st["cash"]),))
            c.execute("INSERT OR REPLACE INTO meta VALUES ('fee_margin', ?)", (str(st["fee_margin"]),))
            c.execute("INSERT OR REPLACE INTO meta VALUES ('initialized', '1')")
            c.execute("DELETE FROM notices")
            c.executemany("INSERT INTO notices VALUES (?,?)",
                          [(k, json.dumps(v, ensure_ascii=False)) for k, v in st["notices"].items()])
            c.execute("DELETE FROM positions")
            c.executemany("INSERT INTO positions VALUES (?,?)",
                          [(k, json.dumps(v, ensure_ascii=False)) for k, v in st["positions"].items() if v["qty"] > 0])
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    @staticmethod
    def _reserved(st) -> int:
        return sum(n["reserved"] for n in st["notices"].values())

    def _reserve_for(self, st, n) -> int:
        p = n["proposal"]
        if p["side"] != "BUY" or n["trade_state"] not in OPEN_TRADE:
            return 0
        return reserve_amount(p["qty"] - n["filled_qty"], p["limit_price"], st["fee_margin"])

    # ------------------------------------------------------------ snapshot
    def init_snapshot(self, cash: int, positions: list, open_orders: list, at: datetime) -> None:
        if self.is_initialized():
            raise LedgerError("スナップショットは登録済み")
        st = {"cash": int(cash), "fee_margin": self._fee_margin_default, "notices": {}, "positions": {}}
        for p in positions:
            qty, avg = int(getattr(p, "qty")), float(getattr(p, "avg_price"))
            st["positions"][p.code] = {
                "qty": qty, "cost": str(_dec(avg) * qty),
                "stop_order": bool(getattr(p, "stop_order", False)),
                "stop_price": getattr(p, "stop_price", None)}
        for o in open_orders:
            pj = {"proposal_id": o.proposal_id, "code": o.code, "side": o.side, "qty": int(o.qty),
                  "limit_price": float(o.limit_price), "broker_order_id": getattr(o, "broker_order_id", None)}
            n = {"proposal_id": o.proposal_id, "notice_state": "EXTERNAL", "trade_state": "ORDERED",
                 "filled_qty": 0, "reserved": 0, "proposal": pj, "sent_at": None, "fills": []}
            n["reserved"] = self._reserve_for(st, n)
            st["notices"][o.proposal_id] = n
        snap = {"kind": "SNAPSHOT", "at": at, "cash": int(cash), "fee_margin": st["fee_margin"],
                "positions": {k: v for k, v in st["positions"].items()},
                "open_orders": [n["proposal"] for n in st["notices"].values()]}
        if st["cash"] - self._reserved(st) < 0:
            raise LedgerError("スナップショットの余力が負")
        self._save(st, [snap])

    # ------------------------------------------------------------ notices
    def create_notice(self, p, at: datetime | None = None) -> None:
        self._require()
        st = self._load()
        if p.proposal_id in st["notices"]:
            raise LedgerError(f"proposal_id 重複: {p.proposal_id}")
        pj = p.to_json() if hasattr(p, "to_json") else {k: _iso(v) for k, v in vars(p).items()}
        n = {"proposal_id": p.proposal_id, "notice_state": "CREATED", "trade_state": "UNCONFIRMED",
             "filled_qty": 0, "reserved": 0, "proposal": pj, "sent_at": None, "fills": []}
        n["reserved"] = self._reserve_for(st, n)
        st["notices"][p.proposal_id] = n
        self._save(st, [{"kind": "NOTICE_CREATED", "proposal_id": p.proposal_id, "at": at,
                         "proposal": pj, "reserved": n["reserved"]}])

    def set_notice_state(self, proposal_id: str, state: str, at: datetime) -> None:
        self._require()
        st = self._load()
        n = st["notices"].get(proposal_id)
        if n is None:
            raise LedgerError(f"未知の proposal_id: {proposal_id}")
        cur = n["notice_state"]
        if state not in NOTICE_TRANSITIONS.get(cur, ()):
            raise LedgerError(f"不正な通知状態遷移: {cur} -> {state}")
        n["notice_state"] = state
        if state == "SENT":
            n["sent_at"] = _iso(at)
        if state == "REJECTED":
            n["trade_state"] = "SKIPPED"
            n["reserved"] = 0
        self._save(st, [{"kind": "NOTICE_STATE", "proposal_id": proposal_id, "at": at,
                         "from": cur, "to": state}])

    def expire_notices(self, now: datetime) -> list[str]:
        self._require()
        st = self._load()
        out, evs = [], []
        for pid, n in st["notices"].items():
            if n["notice_state"] != "SENT":
                continue
            exp = n["proposal"].get("expires_at")
            if exp and _as_jst(_dt(exp)) < _as_jst(now):
                n["notice_state"] = "EXPIRED"
                out.append(pid)
                evs.append({"kind": "NOTICE_STATE", "proposal_id": pid, "at": now, "from": "SENT", "to": "EXPIRED"})
        if evs:
            self._save(st, evs)
        return out

    # ------------------------------------------------------------ reports
    def report(self, ev) -> ReportResult:
        self._require()
        eid = getattr(ev, "event_id", None)
        kind = getattr(ev, "kind", None)
        if not eid:
            return ReportResult(eid, False, error="event_id がない")
        if self._conn.execute("SELECT 1 FROM ledger_events WHERE event_id=?", (eid,)).fetchone():
            return ReportResult(eid, False, duplicate=True)
        st = self._load()
        pid = getattr(ev, "proposal_id", None)
        n = st["notices"].get(pid)
        if n is None:
            return ReportResult(eid, False, error=f"未知の proposal_id: {pid}")
        if kind not in REPORT_KINDS:
            return ReportResult(eid, False, error=f"未知の kind: {kind}")
        warnings: list[str] = []
        try:
            qty = int(getattr(ev, "qty", 0) or 0)
            price = _dec(getattr(ev, "price", 0) or 0)
            fee = _dec(getattr(ev, "fee", 0) or 0)
            if qty < 0 or price < 0 or fee < 0:
                raise _Refused("数量・単価・手数料は負にできない")
            extra = []
            if kind == "ORDERED":
                if n["trade_state"] != "UNCONFIRMED":
                    return self._ignore(st, ev, n, f"状態 {n['trade_state']} では ORDERED を無視")
                n["trade_state"] = "ORDERED"
            elif kind in ("PARTIAL", "FILLED"):
                self._apply_fill(st, n, eid, kind, qty, price, fee, warnings)
            elif kind == "CANCELLED":
                if n["trade_state"] not in OPEN_TRADE:
                    raise _Refused(f"状態 {n['trade_state']} は取消できない")
                remaining = n["proposal"]["qty"] - n["filled_qty"]
                if qty > remaining:
                    raise _Refused(f"取消株数 {qty} が残数 {remaining} を超える")
                if n["filled_qty"] > 0:
                    n["trade_state"] = "FILLED"
                    warnings.append(f"一部約定 {n['filled_qty']} 株後の取消: FILLED として確定")
                else:
                    n["trade_state"] = "CANCELLED"
                n["reserved"] = 0
            elif kind == "SKIPPED":
                if n["filled_qty"] > 0:
                    raise _Refused("約定済みの候補は SKIPPED にできない")
                if n["trade_state"] not in OPEN_TRADE:
                    raise _Refused(f"状態 {n['trade_state']} は SKIPPED にできない")
                n["trade_state"] = "SKIPPED"
                n["reserved"] = 0
            else:  # CORRECTION
                rep = getattr(ev, "replaces_event_id", None)
                live = [f for f in n["fills"] if not f["reversed"]]
                target = next((f for f in live if f["event_id"] == rep), None) if rep else (live[-1] if live else None)
                if target is None:
                    raise _Refused("訂正対象の約定がない")
                self._reverse_fill(st, n, target)
                extra.append({"kind": "REVERSAL", "proposal_id": pid, "at": getattr(ev, "at", None),
                              "reverses_event_id": target["event_id"], "by_event_id": eid})
                if qty > 0:
                    k = "FILLED" if qty >= n["proposal"]["qty"] - n["filled_qty"] else "PARTIAL"
                    self._apply_fill(st, n, eid, k, qty, price, fee, warnings)
                elif n["filled_qty"] == 0 and n["trade_state"] in ("PARTIAL", "FILLED"):
                    n["trade_state"] = "ORDERED"
                    n["reserved"] = self._reserve_for(st, n)
            if st["cash"] - self._reserved(st) < 0:
                raise _Refused("余力が負になる報告は拒否")
        except _Refused as e:
            return ReportResult(eid, False, error=str(e))
        rec = {"kind": "REPORT", "event_id": eid, "proposal_id": pid, "at": getattr(ev, "at", None),
               "report_kind": kind, "qty": qty, "price": str(price), "fee": str(fee),
               "source": getattr(ev, "source", None), "broker_order_id": getattr(ev, "broker_order_id", None),
               "replaces_event_id": getattr(ev, "replaces_event_id", None), "trade_state": n["trade_state"],
               "warnings": warnings}
        self._save(st, [rec] + extra)
        return ReportResult(eid, True, trade_state=n["trade_state"], warnings=warnings)

    def _ignore(self, st, ev, n, reason) -> ReportResult:
        eid = ev.event_id
        self._save(st, [{"kind": "REPORT_IGNORED", "event_id": eid, "proposal_id": ev.proposal_id,
                         "at": getattr(ev, "at", None), "report_kind": ev.kind, "reason": reason}])
        return ReportResult(eid, False, ignored_reason=reason, trade_state=n["trade_state"])

    def _apply_fill(self, st, n, eid, kind, qty, price, fee, warnings):
        p = n["proposal"]
        if n["trade_state"] not in OPEN_TRADE:
            raise _Refused(f"状態 {n['trade_state']} に約定は計上できない")
        if qty <= 0:
            raise _Refused("約定株数は正")
        remaining = p["qty"] - n["filled_qty"]
        if qty > remaining:
            raise _Refused(f"約定株数 {qty} が残数 {remaining} を超える")
        code = p["code"]
        pos = st["positions"].setdefault(code, {"qty": 0, "cost": "0", "stop_order": False, "stop_price": None})
        gross = price * qty
        if p["side"] == "BUY":
            st["cash"] -= _yen(gross + fee)
            pos["qty"] += qty
            pos["cost"] = str(_dec(pos["cost"]) + gross)
        else:
            if pos["qty"] < qty:
                raise _Refused(f"保有 {pos['qty']} 株では {qty} 株売れない")
            avg = _dec(pos["cost"]) / pos["qty"]
            pos["cost"] = str(_dec(pos["cost"]) - avg * qty)
            pos["qty"] -= qty
            st["cash"] += _yen(gross - fee)
        n["filled_qty"] += qty
        n["fills"].append({"event_id": eid, "qty": qty, "price": str(price), "fee": str(fee), "reversed": False})
        if n["filled_qty"] == p["qty"]:
            n["trade_state"] = "FILLED"
        else:
            if kind == "FILLED":
                warnings.append(f"FILLED だが残 {p['qty'] - n['filled_qty']} 株: PARTIAL として記録")
            n["trade_state"] = "PARTIAL"
        n["reserved"] = self._reserve_for(st, n)

    def _reverse_fill(self, st, n, f):
        p = n["proposal"]
        qty, price, fee = f["qty"], _dec(f["price"]), _dec(f["fee"])
        pos = st["positions"].setdefault(p["code"], {"qty": 0, "cost": "0", "stop_order": False, "stop_price": None})
        if p["side"] == "BUY":
            if pos["qty"] < qty:
                raise _Refused("訂正対象の株数が保有にない")
            st["cash"] += _yen(price * qty + fee)
            pos["qty"] -= qty
            pos["cost"] = str(_dec(pos["cost"]) - price * qty) if pos["qty"] else "0"
        else:
            avg = _dec(pos["cost"]) / pos["qty"] if pos["qty"] else price
            st["cash"] -= _yen(price * qty - fee)
            pos["qty"] += qty
            pos["cost"] = str(_dec(pos["cost"]) + avg * qty)
        n["filled_qty"] -= qty
        f["reversed"] = True
        if n["trade_state"] == "FILLED" and n["filled_qty"] < p["qty"]:
            n["trade_state"] = "PARTIAL" if n["filled_qty"] else "ORDERED"
        n["reserved"] = self._reserve_for(st, n)

    # ------------------------------------------------------------ queries
    def unconfirmed(self, as_of: datetime) -> list[str]:
        self._require()
        st = self._load()
        return [pid for pid, n in st["notices"].items()
                if n["notice_state"] in ("SENT", "EXPIRED") and n["trade_state"] in OPEN_TRADE]

    def cash(self) -> int:
        self._require()
        return self._load()["cash"]

    def reserved(self) -> int:
        self._require()
        return self._reserved(self._load())

    def available(self) -> int:
        self._require()
        st = self._load()
        return st["cash"] - self._reserved(st)

    def positions(self) -> dict[str, Position]:
        self._require()
        out = {}
        for code, v in self._load()["positions"].items():
            if v["qty"] <= 0:
                continue
            avg = float((_dec(v["cost"]) / v["qty"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            out[code] = Position(code, v["qty"], avg, bool(v["stop_order"]), v["stop_price"])
        return out

    def reserved_positions(self) -> dict[str, int]:
        self._require()
        out: dict[str, int] = {}
        for n in self._load()["notices"].values():
            if n["reserved"] > 0:
                c = n["proposal"]["code"]
                out[c] = out.get(c, 0) + n["reserved"]
        return out

    def set_stop_order(self, code: str, has_stop: bool, stop_price: float | None, at: datetime) -> None:
        self._require()
        st = self._load()
        pos = st["positions"].get(code)
        if not pos or pos["qty"] <= 0:
            raise LedgerError(f"保有なし: {code}")
        pos["stop_order"] = bool(has_stop)
        pos["stop_price"] = stop_price if has_stop else None
        self._save(st, [{"kind": "STOP_ORDER", "code": code, "has_stop": bool(has_stop),
                         "stop_price": stop_price, "at": at}])

    def adjust(self, kind: str, amount: int | None, code: str | None, ratio: float | None,
               at: datetime, note: str) -> None:
        self._require()
        if kind not in ADJUST_KINDS:
            raise LedgerError(f"未知の調整種別: {kind}")
        st = self._load()
        if kind in ("DEPOSIT", "WITHDRAW", "DIVIDEND"):
            if amount is None or int(amount) < 0:
                raise LedgerError("amount は 0 以上の整数")
            st["cash"] += -int(amount) if kind == "WITHDRAW" else int(amount)
            if st["cash"] - self._reserved(st) < 0:
                raise LedgerError("出金で余力が負になる")
        else:
            pos = st["positions"].get(code)
            if not pos or not ratio or ratio <= 0:
                raise LedgerError("分割には保有銘柄と正の ratio が必要")
            pos["qty"] = int(Decimal(pos["qty"]) * _dec(ratio))
        self._save(st, [{"kind": "ADJUST", "adjust_kind": kind, "amount": amount, "code": code,
                         "ratio": ratio, "at": at, "note": note}])

    def view(self, day_start_equity: int | None = None, daily_pnl: int = 0) -> LedgerView:
        self._require()
        c, r = self.cash(), self.reserved()
        return LedgerView(cash=c, reserved=r, available=c - r, positions=self.positions(),
                          reserved_positions=self.reserved_positions(), daily_pnl=daily_pnl,
                          day_start_equity=day_start_equity)

    def new_sent_today(self, day: date) -> int:
        self._require()
        cnt = 0
        for n in self._load()["notices"].values():
            if n["proposal"].get("side") == "BUY" and n.get("sent_at"):
                if _as_jst(_dt(n["sent_at"])).date() == day:
                    cnt += 1
        return cnt

    def notices(self) -> list[dict]:
        self._require()
        return [{k: n[k] for k in ("proposal_id", "notice_state", "trade_state", "filled_qty", "reserved", "proposal")}
                for n in self._load()["notices"].values()]

    def events(self) -> list[dict]:
        self._require()
        rows = self._conn.execute("SELECT seq, payload FROM ledger_events ORDER BY seq").fetchall()
        return [dict(json.loads(p), seq=s) for s, p in rows]

    def close(self) -> None:
        self._conn.close()
