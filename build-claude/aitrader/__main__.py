"""CLI（共通仕様 §7）

python -m aitrader init-db
python -m aitrader load-synthetic --seed 42
python -m aitrader fetch --from 2024-01-01 --to 2026-08-31
python -m aitrader signals --strategy margin_bucket_long --as-of 2026-08-29
python -m aitrader backtest --strategy margin_bucket_long --from 2016-01-04 --to 2026-08-31 --out results/
python -m aitrader report --results results/margin_bucket_long_v1/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path


def _load_dotenv() -> None:
    p = Path(".env")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _home(args) -> Path | None:
    return Path(args.home) if args.home else None


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows コンソール対策
    _load_dotenv()
    ap = argparse.ArgumentParser(prog="aitrader")
    ap.add_argument("--home", help="実行時データの置き場（既定: AI_TRADER_HOME か ~/.ai-trader）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db")
    s = sub.add_parser("load-synthetic"); s.add_argument("--seed", type=int, default=42)
    s = sub.add_parser("fetch"); s.add_argument("--from", dest="start", required=True); s.add_argument("--to", dest="end", required=True)
    s = sub.add_parser("signals"); s.add_argument("--strategy", required=True); s.add_argument("--as-of", dest="as_of", required=True)
    s = sub.add_parser("backtest"); s.add_argument("--strategy", required=True)
    s.add_argument("--from", dest="start", required=True); s.add_argument("--to", dest="end", required=True)
    s.add_argument("--out", default="results"); s.add_argument("--no-report", action="store_true")
    s = sub.add_parser("report"); s.add_argument("--results", required=True)
    for name in ("packets", "daily"):
        s = sub.add_parser(name); s.add_argument("--strategy", required=True); s.add_argument("--as-of", dest="as_of", required=True)
        s.add_argument("--budget-per-name", type=int, default=250000); s.add_argument("--events"); s.add_argument("--lot-sizes")
        s.add_argument("--policy-version", default="solo-v1")
        if name == "daily":
            s.add_argument("--judge", choices=["mock", "cmd"], default="mock")
            s.add_argument("--claude-cmd"); s.add_argument("--codex-cmd")
            s.add_argument("--execute", action="store_true", help="台帳に通知を記録する（既定は dry-run）")
            s.add_argument("--now", help="リハーサル用に現在時刻を上書き（ISO8601、tz 省略時は JST）")
    s = sub.add_parser("ledger-init"); s.add_argument("--cash", type=int, required=True); s.add_argument("--positions")

    s = sub.add_parser("ledger-import-csv"); s.add_argument("--file", required=True)

    s = sub.add_parser("ledger-report")
    s.add_argument("--proposal-id", dest="proposal_id", required=True)
    s.add_argument("--kind", required=True, choices=["ORDERED", "PARTIAL", "FILLED", "CANCELLED", "SKIPPED"])
    s.add_argument("--qty", type=int, required=True)
    s.add_argument("--price", type=float, default=0)
    s.add_argument("--fee", type=float, default=0)
    s.add_argument("--broker-order-id", dest="broker_order_id")
    s.add_argument("--event-id", dest="event_id")
    s.add_argument("--at")

    s = sub.add_parser("ledger-status"); s.add_argument("--now")

    s = sub.add_parser("ledger-resolve")
    s.add_argument("--event-id", dest="event_id", required=True)
    s.add_argument("--apply", action="store_true")
    s.add_argument("--discard", action="store_true")
    s.add_argument("--proposal-id", dest="proposal_id")
    s.add_argument("--at")

    args = ap.parse_args(argv)
    from . import api

    if args.cmd == "init-db":
        api.init_db(_home(args)); print("ok: schema created")
    elif args.cmd == "load-synthetic":
        api.load_synthetic(_home(args), seed=args.seed); print(f"ok: synthetic data loaded (seed={args.seed})")
    elif args.cmd == "fetch":
        from .jquants import fetch_to_db
        counts = fetch_to_db(_home(args), date.fromisoformat(args.start), date.fromisoformat(args.end))
        print(json.dumps(counts, ensure_ascii=False))
    elif args.cmd == "signals":
        cands = api.run_signals(_home(args), args.strategy, date.fromisoformat(args.as_of))
        print(json.dumps({"as_of": args.as_of, "strategy": args.strategy, "candidates": cands}, ensure_ascii=False, indent=2))
    elif args.cmd == "backtest":
        s = api.run_backtest(_home(args), args.strategy, date.fromisoformat(args.start), date.fromisoformat(args.end), Path(args.out))
        print(json.dumps({k: v for k, v in s.items() if k != "yearly"}, ensure_ascii=False, indent=2))
        if not args.no_report:
            from .report import build_report
            print("report:", build_report(Path(args.out) / f"{s['strategy']}_{s['version']}"))
    elif args.cmd == "report":
        from .report import build_report
        print("report:", build_report(Path(args.results)))
    elif args.cmd in ("packets", "daily"):
        return _phase2(args)
    elif args.cmd == "ledger-init":
        from datetime import datetime
        from .db import default_home
        from .ledger import Ledger
        from .models import JST
        from .runner import load_json_file
        home = _home(args) or default_home(); home.mkdir(parents=True, exist_ok=True)
        positions = load_json_file(args.positions) if args.positions else []
        led = Ledger(home / "ledger.sqlite")
        try:
            led.init_snapshot(args.cash, positions, [], datetime.now(JST))
        finally:
            led.close()
        print(f"ok: ledger initialized (cash={args.cash}, positions={len(positions)})")
    elif args.cmd == "ledger-import-csv":
        return _ledger_import_csv(args)
    elif args.cmd == "ledger-report":
        return _ledger_report(args)
    elif args.cmd == "ledger-status":
        return _ledger_status(args)
    elif args.cmd == "ledger-resolve":
        return _ledger_resolve(args)
    return 0


def _open_ledger(args):
    from .db import default_home
    from .ledger import Ledger
    home = _home(args) or default_home()
    return Ledger(home / "ledger.sqlite")


def _ledger_import_csv(args) -> int:
    from .csvfills import parse_fills_csv
    led = _open_ledger(args)
    try:
        rows = parse_fills_csv(Path(args.file))
        result = led.import_csv_fills(rows)
        out = {"applied": len(result.applied), "skipped": len(result.skipped),
               "pending": len(result.pending), "errors": result.errors}
    finally:
        led.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if out.get("errors") else 0


def _ledger_report(args) -> int:
    import hashlib
    from datetime import datetime as _dt
    from dataclasses import dataclass
    from .models import JST

    at = None
    if args.at:
        at = _dt.fromisoformat(args.at)
        if at.tzinfo is None:
            at = at.replace(tzinfo=JST)
    else:
        at = _dt.now(JST)

    event_id = args.event_id
    if not event_id:
        basis = f"{args.proposal_id}|{args.kind}|{args.qty}|{args.price}|{args.fee}|{at.isoformat()}"
        event_id = "manual-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()

    @dataclass
    class _Ev:
        event_id: str
        proposal_id: str
        kind: str
        qty: int
        price: float
        fee: float
        at: object
        source: str = "manual"
        broker_order_id: str | None = None
        replaces_event_id: str | None = None

    ev = _Ev(event_id=event_id, proposal_id=args.proposal_id, kind=args.kind, qty=args.qty,
              price=args.price, fee=args.fee, at=at, broker_order_id=args.broker_order_id)

    led = _open_ledger(args)
    try:
        result = led.report(ev)
    finally:
        led.close()
    out = {"event_id": result.event_id, "applied": result.applied, "error": result.error,
           "duplicate": result.duplicate, "ignored_reason": result.ignored_reason,
           "trade_state": result.trade_state, "warnings": result.warnings}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if result.applied or result.duplicate or result.ignored_reason else 1


def _ledger_status(args) -> int:
    from datetime import datetime as _dt
    from .models import JST
    now = _dt.now(JST)
    if args.now:
        now = _dt.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=JST)

    led = _open_ledger(args)
    try:
        cash = led.cash()
        reserved = led.reserved()
        available = led.available()
        positions = {code: vars(p) for code, p in led.positions().items()}
        notices = led.notices()
        unconfirmed = led.unconfirmed(now)
        pending = {}
        if hasattr(led, "pending_rows"):
            pending = led.pending_rows()
    finally:
        led.close()
    out = {"cash": cash, "reserved": reserved, "available": available, "positions": positions,
           "notices": notices, "unconfirmed": unconfirmed, "pending_rows": pending}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def _ledger_resolve(args) -> int:
    from datetime import datetime as _dt
    from .models import JST
    if args.apply == args.discard:
        print("error: --apply か --discard のどちらか一方を指定", file=sys.stderr)
        return 2
    if args.apply and not args.proposal_id:
        print("error: --apply には --proposal-id が必要", file=sys.stderr)
        return 2
    at = _dt.now(JST)
    if args.at:
        at = _dt.fromisoformat(args.at)
        if at.tzinfo is None:
            at = at.replace(tzinfo=JST)
    action = "APPLY" if args.apply else "DISCARD"
    led = _open_ledger(args)
    try:
        result = led.resolve_pending(args.event_id, args.proposal_id if args.apply else None, at, action=action)
    finally:
        led.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _phase2(args) -> int:
    from .runner import RunConfig, load_json_file
    as_of = date.fromisoformat(args.as_of)
    cfg = RunConfig(strategy=args.strategy, budget_per_name=args.budget_per_name, policy_version=args.policy_version,
                    events=load_json_file(args.events) if args.events else {},
                    lot_sizes=load_json_file(args.lot_sizes) if args.lot_sizes else {})
    if args.cmd == "packets":
        from . import api, db
        from .packet import build_proposals
        from .runner import prev_closes
        cands = api.run_signals(_home(args), cfg.strategy, as_of)
        con = db.connect(_home(args), read_only=True)
        try:
            pc = prev_closes(con, [str(c["code"]) for c in cands], as_of)
        finally:
            con.close()
        cands = [c for c in cands if str(c["code"]) in pc]
        ps = build_proposals(cands, as_of, pc, cfg.lot_sizes, cfg.events, f"db-{as_of}", cfg.policy_version, cfg.budget_per_name)
        print(json.dumps([p.to_json() for p in ps], ensure_ascii=False, indent=2))
        return 0
    import shlex
    from . import judges as J
    from .runner import run_daily
    cfg.dry_run = not args.execute
    if args.judge == "cmd":
        if not (args.claude_cmd and args.codex_cmd):
            print("error: --judge cmd には --claude-cmd と --codex-cmd が必要", file=sys.stderr); return 2
        js = [J.CommandJudge("claude", shlex.split(args.claude_cmd)), J.CommandJudge("codex", shlex.split(args.codex_cmd))]
    else:
        js = [J.MockJudge("claude"), J.MockJudge("codex")]
    now = None
    if getattr(args, "now", None):
        from datetime import datetime as _dt
        from .models import JST
        now = _dt.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=JST)
    r = run_daily(_home(args), as_of, cfg, js, now=now)
    summary = {"status": r["status"], "dry_run": r["dry_run"], "candidates": r["candidates"],
               "sent": r["sent"],
               "blocked": {p["proposal_id"]: p["reasons"] for p in r["proposals"] if not p["allowed"]},
               "unresolved_unconfirmed": r["unresolved_unconfirmed"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if r["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
