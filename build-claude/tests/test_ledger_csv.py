"""aitrader.ledger の CSV 取込・照合待ち・balance_at（共通仕様_フェーズ2 §3.2 / §3.3 の 2, 9）"""
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace as R

import pytest

from aitrader.ledger import Ledger
from aitrader.models import JST, Proposal, packet_hash

AT = datetime(2026, 9, 8, 8, 0, tzinfo=JST)
PID = "A1-20260908-6857-01"
T1 = AT + timedelta(hours=1)


def proposal(pid=PID, code="6857", qty=100):
    p = Proposal(proposal_id=pid, packet_hash="", code=code, side="BUY", qty=qty, lot_size=100,
                 limit_price=1000.0, exec_condition="OPENING_LIMIT", account_type="CASH",
                 strategy="s", strategy_version="v1", as_of=date(2026, 9, 7), snapshot_id="s1",
                 policy_version="p1", expires_at=AT.replace(minute=59), reason="テスト",
                 events={"next_earnings_date": None, "margin_regulated": False})
    return replace(p, packet_hash=packet_hash(p))


def line(id="e1", qty=100, price=1000, broker="b1", at=T1, kind="FILLED"):
    return R(event_id=id, proposal_id=PID, kind=kind, qty=qty, price=price, fee=0, at=at,
             source="line", broker_order_id=broker)


def row(id="csv1", pid=PID, code="6857", qty=100, price=1000, broker="b1", at=T1):
    return R(event_id=id, proposal_id=pid, code=code, side="BUY", qty=qty, price=price, fee=0,
             at=at, source="csv", broker_order_id=broker)


def sent(l, p):
    l.create_notice(p, at=AT)
    l.set_notice_state(p.proposal_id, "APPROVED", AT)
    l.set_notice_state(p.proposal_id, "SENT", AT)


@pytest.fixture
def ledger(tmp_path):
    l = Ledger(tmp_path / "l.sqlite")
    l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    sent(l, proposal())
    return l


def bal(l):
    return l.cash(), l.reserved(), {c: (p.qty, p.avg_price) for c, p in l.positions().items()}


def test_repeat_import_idempotent(ledger):
    r1 = ledger.import_csv_fills([row()]); once = bal(ledger)
    r2 = ledger.import_csv_fills([row()])
    assert r1.applied == ["csv1"] and r2.skipped == ["csv1"] and bal(ledger) == once
    assert once == (900000, 0, {"6857": (100, 1000.0)})


@pytest.mark.parametrize("csv_first", [True, False])
@pytest.mark.parametrize("broker", ["b1", None])
def test_csv_line_count_once(ledger, csv_first, broker):
    if csv_first:
        ledger.import_csv_fills([row(broker=broker)]); ledger.report(line(broker=broker))
    else:
        ledger.report(line(broker=broker)); ledger.import_csv_fills([row(broker=broker)])
    assert bal(ledger)[:2] == (900000, 0) and ledger.positions()["6857"].qty == 100


def test_ambiguous_pending_then_resolve(ledger):
    ledger.report(line(broker=None)); once = bal(ledger)
    # 同じ注文の別約定ではない（全量約定済み）ので曖昧行は pending のまま
    r = ledger.import_csv_fills([row(broker=None)])
    assert r.pending == ["csv1"] and bal(ledger) == once
    pr = ledger.pending_rows()
    assert pr["csv1"]["reason"] and pr["csv1"]["qty"] == 100


def test_resolve_apply_once_and_discard(tmp_path):
    l = Ledger(tmp_path / "l.sqlite"); l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    sent(l, proposal(qty=200))
    l.report(R(event_id="e1", proposal_id=PID, kind="PARTIAL", qty=100, price=1000, fee=0, at=T1,
               source="line", broker_order_id=None))
    assert l.import_csv_fills([row(broker=None), row(id="csv2", broker=None)]).pending == ["csv1", "csv2"]
    before = l.cash()
    r = l.resolve_pending("csv1", PID, T1, "APPLY")
    assert r.applied and l.cash() == before - 100000 and l.positions()["6857"].qty == 200
    assert l.resolve_pending("csv1", PID, T1, "APPLY") is None and l.cash() == before - 100000
    assert l.resolve_pending("csv2", None, T1, "DISCARD") is not None
    assert l.cash() == before - 100000 and l.pending_rows() == {}
    assert l.import_csv_fills([row(id="csv2")]).skipped == ["csv2"]
    kinds = [e["kind"] for e in l.events()]
    assert kinds.count("PENDING_RESOLVED") == 2 and kinds.count("CSV_PENDING") == 2


def test_pid_code_mismatch_pending(ledger):
    r = ledger.import_csv_fills([row(code="7203")])
    assert r.pending == ["csv1"] and ledger.cash() == 1000000


def test_no_pid_binding(tmp_path):
    l = Ledger(tmp_path / "l.sqlite"); l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    sent(l, proposal())
    assert l.import_csv_fills([row(pid=None)]).applied == ["csv1"]
    sent(l, proposal(pid="P2")); sent(l, proposal(pid="P3"))
    r = l.import_csv_fills([row(id="csv2", pid=None, broker="b9")])
    assert r.pending == ["csv2"] and l.cash() == 900000


def test_errors(ledger):
    r = ledger.import_csv_fills([row(qty=200), R(event_id="", proposal_id=PID, code="6857", side="BUY",
                                                 qty=1, price=1, fee=0, at=T1)])
    assert "csv1" in r.errors and len(r.errors) == 2 and ledger.cash() == 1000000


def test_balance_at(ledger):
    ledger.import_csv_fills([row(qty=40, broker="b1", at=T1)])
    ledger.import_csv_fills([row(id="csv2", qty=60, broker="b1", at=T1 + timedelta(hours=2))])
    snap = ledger.balance_at(AT - timedelta(days=1))
    assert snap == {"cash": 1000000, "reserved": 0, "positions": {}}
    mid = ledger.balance_at(T1 + timedelta(hours=1))
    assert mid["cash"] == 960000 and mid["positions"]["6857"]["qty"] == 40
    now = ledger.balance_at(T1 + timedelta(days=1))
    assert now["cash"] == ledger.cash() == 900000 and now["reserved"] == ledger.reserved() == 0
    assert now["positions"] == {c: {"qty": p.qty, "avg_price": p.avg_price} for c, p in ledger.positions().items()}
