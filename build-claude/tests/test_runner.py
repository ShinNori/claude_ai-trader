import json
from datetime import date, datetime

import pytest

from aitrader.judges import MockJudge
from aitrader.ledger import Ledger
from aitrader.models import JST, Limits
from aitrader.runner import RunConfig, run_daily

AS_OF = date(2026, 9, 24)  # Thu -> exec Fri 9/25
NOW = datetime(2026, 9, 24, 18, 0, tzinfo=JST)
SAFE = {"next_earnings_date": "2026-11-10", "margin_regulated": False}
CODES = ["1111", "2222", "3333"]


def cands(n):
    return [{"code": c, "strategy": "margin_bucket_long", "strategy_version": "v1", "reason": "t"} for c in CODES[:n]]


PC = {"1111": 1000.0, "2222": 2000.0, "3333": 500.0}


@pytest.fixture
def env(tmp_path):
    led = Ledger(tmp_path / "ledger.sqlite")
    led.init_snapshot(cash=5_000_000, positions=[], open_orders=[], at=NOW)
    yield tmp_path, led
    led.close()


def run(tmp_path, led, n=2, dry=True, events=None, judges=None, limits=None):
    events = {c: SAFE for c in CODES} if events is None else events
    cfg = RunConfig(events=events, dry_run=dry, limits=limits or Limits())
    judges = judges or [MockJudge("claude"), MockJudge("codex")]
    return run_daily(tmp_path, AS_OF, cfg, judges, now=NOW, candidates=cands(n),
                     prev_close=PC, ledger=led, outbox_dir=tmp_path / "outbox")


def files(d):
    return [f for f in d.rglob("*") if f.is_file()] if d.exists() else []


def test_dry_run(env):
    tmp, led = env
    r = run(tmp, led)
    assert r["status"] == "OK" and len(r["sent"]) == 2, r
    assert len(files(tmp / "outbox" / "dry-run")) == 2
    assert led.notices() == [] and led.reserved() == 0
    assert r["proposals"][0]["judge_summary"] == {"claude": "APPROVE", "codex": "APPROVE"}


def test_execute_limit(env):
    tmp, led = env
    r = run(tmp, led, n=3, dry=False)
    assert len(r["sent"]) == 2 and len(r["blocked"]) == 1
    assert any("上限" in x for x in r["proposals"][2]["reasons"])
    states = [n.get("notice_state") for n in led.notices()]
    assert states.count("SENT") == 2
    assert led.reserved() == sum(p["reserve_amount"] for p in r["proposals"][:2]) > 0


def test_events_unknown(env):
    tmp, led = env
    r = run(tmp, led, events={})
    assert r["sent"] == [] and len(r["blocked"]) == 2
    assert all(any("UNKNOWN" in x for x in p["reasons"]) for p in r["proposals"])


def test_reject(env):
    tmp, led = env
    r = run(tmp, led, judges=[MockJudge("claude"), MockJudge("codex", decision="REJECT")])
    assert r["sent"] == [] and len(r["blocked"]) == 2
    assert r["proposals"][0]["judge_summary"]["codex"] == "REJECT"


def test_not_initialized(tmp_path):
    led = Ledger(tmp_path / "l.sqlite")
    try:
        r = run(tmp_path, led)
    finally:
        led.close()
    assert r["status"] == "LEDGER_NOT_INITIALIZED"
    assert files(tmp_path / "outbox") == [] and not (tmp_path / "receipts").exists()


def test_receipt(env):
    tmp, led = env
    r = run(tmp, led)
    d = json.loads((tmp / "receipts" / f"{AS_OF}.json").read_text(encoding="utf-8"))
    assert d["status"] == "OK" and d["sent"] == r["sent"] and d["candidates"] == 2
