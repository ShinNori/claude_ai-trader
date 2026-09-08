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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
