"""日次オーケストレーション（フェーズ2）— Claude 単独ビルド

候補生成 → パケット → 審査 → ゲート → 台帳/送信箱 → 受領記録。
外部送信・発注は一切しない（出力はローカルファイルのみ）。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from .gate import evaluate
from .models import JST, Limits
from .packet import build_proposals


@dataclass
class RunConfig:
    strategy: str = "margin_bucket_long"
    budget_per_name: int = 250000
    policy_version: str = "solo-v1"
    snapshot_id: str | None = None
    lot_sizes: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)
    limits: Limits = field(default_factory=Limits)
    stop_flag: bool = False
    dry_run: bool = True


def load_json_file(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def prev_closes(con, codes: list[str], as_of: date) -> dict[str, float]:
    out: dict[str, float] = {}
    for code in codes:
        r = con.execute(
            "SELECT close FROM prices_daily WHERE code = ? AND date <= ? AND close IS NOT NULL "
            "ORDER BY date DESC LIMIT 1", [str(code), as_of]).fetchone()
        if r is not None:
            out[str(code)] = float(r[0])
    return out


def _stored_peak(receipts_dir: Path) -> int | None:
    peak = None
    if receipts_dir.exists():
        for f in receipts_dir.glob("*.json"):
            try:
                v = json.loads(f.read_text(encoding="utf-8")).get("peak_equity")
            except (OSError, ValueError):
                continue
            if isinstance(v, (int, float)) and (peak is None or v > peak):
                peak = int(v)
    return peak


def _verdict_json(v) -> dict:
    d = asdict(v)
    d["risks"] = list(d.get("risks") or ())
    if isinstance(d.get("received_at"), datetime):
        d["received_at"] = d["received_at"].isoformat()
    return d


def run_daily(home: Path | None, as_of: date, cfg: RunConfig, judges: list, *,
              now: datetime | None = None, candidates: list[dict] | None = None,
              prev_close: dict | None = None, ledger=None, outbox_dir: Path | None = None) -> dict:
    from . import db as _db
    from .judges import run_judges
    from .notify import Outbox

    now = now or datetime.now(JST)
    home_dir = Path(home) if home else _db.default_home()
    outbox_dir = Path(outbox_dir) if outbox_dir else home_dir / "outbox"
    receipts_dir = (outbox_dir.parent if outbox_dir is not None else home_dir) / "receipts"

    # (1) 候補・前日終値
    if candidates is None:
        from . import api
        candidates = api.run_signals(home, cfg.strategy, as_of)
    if prev_close is None:
        con = _db.connect(home, read_only=True)
        try:
            prev_close = prev_closes(con, [str(c["code"]) for c in candidates], as_of)
        finally:
            con.close()
    snapshot_id = cfg.snapshot_id or f"db-{as_of}"
    receipt = {"as_of": as_of.isoformat(), "now": now.isoformat(), "strategy": cfg.strategy,
               "dry_run": cfg.dry_run, "candidates": len(candidates), "proposals": [],
               "sent": [], "blocked": [], "unresolved_unconfirmed": False}

    # (3) 台帳
    own_ledger = ledger is None
    if own_ledger:
        from .ledger import Ledger
        ledger = Ledger(home_dir / "ledger.sqlite", fee_margin=cfg.limits.fee_margin)
    try:
        if not ledger.is_initialized():
            receipt["status"] = "LEDGER_NOT_INITIALIZED"
            return receipt

        # (2) パケット（価格の無い候補は除外して記録）
        missing = [str(c["code"]) for c in candidates if str(c["code"]) not in prev_close]
        usable = [c for c in candidates if str(c["code"]) in prev_close]
        receipt["missing_prev_close"] = missing
        proposals = build_proposals(usable, as_of, prev_close, cfg.lot_sizes, cfg.events,
                                    snapshot_id, cfg.policy_version, cfg.budget_per_name)

        # (4)
        ledger.expire_notices(now)
        unresolved = bool(ledger.unconfirmed(now))
        receipt["unresolved_unconfirmed"] = unresolved

        outbox = Outbox(outbox_dir / "dry-run" if cfg.dry_run else outbox_dir)
        stored_peak = _stored_peak(receipts_dir)
        peak_seen = None
        sent_this_run = 0
        base_sent = ledger.new_sent_today(now.astimezone(JST).date())

        # (5)
        for p in proposals:
            verdicts = run_judges(p, judges, now)
            positions = ledger.positions()
            equity = int(ledger.cash() + sum(pos.qty * pos.avg_price for pos in positions.values()))
            peak = max(equity, stored_peak) if stored_peak is not None else equity
            peak_seen = peak if peak_seen is None else max(peak_seen, peak)
            res = evaluate(p, verdicts, now, ledger.view(), cfg.limits, equity, peak,
                           base_sent + sent_this_run, cfg.stop_flag, unresolved)
            if res.allowed:
                if not cfg.dry_run:
                    ledger.create_notice(p, at=now)
                    ledger.set_notice_state(p.proposal_id, "APPROVED", now)
                    ledger.set_notice_state(p.proposal_id, "SENT", now)
                outbox.enqueue(p, res, now=now)
                if res.category == "NEW":
                    sent_this_run += 1
                receipt["sent"].append(p.proposal_id)
            else:
                receipt["blocked"].append(p.proposal_id)
            receipt["proposals"].append({
                **p.to_json(),
                "verdicts": [_verdict_json(v) for v in verdicts],
                "judge_summary": {v.judge: v.decision for v in verdicts},
                "allowed": res.allowed, "category": res.category,
                "reasons": list(res.reasons), "reserve_amount": res.reserve_amount,
            })
        if peak_seen is None:
            peak_seen = stored_peak
        receipt["peak_equity"] = peak_seen
        receipt["status"] = "OK"

        # (6)
        receipts_dir.mkdir(parents=True, exist_ok=True)
        (receipts_dir / f"{as_of}.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return receipt
    finally:
        if own_ledger:
            ledger.close()
