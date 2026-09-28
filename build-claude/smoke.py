"""他 PC での動作確認（合成データで一連の流れを 1 回通す。ネットワーク・LLM・発注なし）

  python smoke.py [--cash 3000000] [--home DIR]

やること: init-db → load-synthetic → packets → events.json 生成 → ledger-init（未初期化なら）→ daily（dry-run）→ daily --execute（リハーサル時刻）→ ledger-status
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import warnings
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aitrader.__main__ import main as cli  # noqa: E402

AS_OF = "2025-06-06"
NOW = "2025-06-09T08:30:00"


def run(*args) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf), warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        rc = cli(list(args))
    if w:
        print(f"      （予算不足で除外された銘柄 {len(w)} 件）")
    if rc not in (0, None):
        print(buf.getvalue()); raise SystemExit(f"失敗: aitrader {' '.join(args)} (exit {rc})")
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--cash", type=int, default=3000000); ap.add_argument("--home")
    a = ap.parse_args()
    if a.home:
        os.environ["AI_TRADER_HOME"] = a.home
    from aitrader.db import default_home
    home = default_home(); home.mkdir(parents=True, exist_ok=True)
    print(f"[1/6] AI_TRADER_HOME = {home}")
    if not (home / "market.duckdb").exists():
        run("init-db"); run("load-synthetic", "--seed", "42"); print("[2/6] 合成データを投入（seed=42）")
    else:
        print("[2/6] 既存の market.duckdb を使用")
    packets = json.loads(run("packets", "--strategy", "margin_bucket_long", "--as-of", AS_OF))
    events = {p["code"]: {"next_earnings_date": "2025-07-31", "margin_regulated": False} for p in packets}
    ev_path = home / "events.json"; ev_path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/6] パケット {len(packets)} 件、events.json（ダミーの決算日）を {ev_path} に生成")
    from aitrader.ledger import Ledger
    if not Ledger(home / "ledger.sqlite").is_initialized():
        run("ledger-init", "--cash", str(a.cash)); print(f"[4/6] 台帳を初期化（現金 {a.cash:,} 円）")
    else:
        print("[4/6] 既存の台帳を使用")
    dry = json.loads(run("daily", "--strategy", "margin_bucket_long", "--as-of", AS_OF, "--events", str(ev_path), "--now", NOW))
    print(f"[5/6] dry-run: 送信候補 {len(dry['sent'])} 件 / 遮断 {len(dry['blocked'])} 件")
    ex = json.loads(run("daily", "--strategy", "margin_bucket_long", "--as-of", AS_OF, "--events", str(ev_path), "--now", NOW, "--execute"))
    st = json.loads(run("ledger-status", "--now", NOW))
    print(f"[6/6] execute: 送信 {len(ex['sent'])} 件、台帳 cash={st['cash']:,} reserved={st['reserved']:,} available={st['available']:,}")
    print(f"      outbox: {home / 'outbox' / AS_OF}   receipt: {home / 'receipts' / (AS_OF + '.json')}")
    print("OK: 一連の流れが通りました（発注は行っていません）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
