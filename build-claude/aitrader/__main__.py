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
