"""aitrader.ledger の単体テスト（共通仕様_フェーズ2 §3.3 の不変条件＋境界）"""
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace as R

import pytest

from aitrader.ledger import Ledger, LedgerError, LedgerNotInitialized
from aitrader.models import JST, Proposal, packet_hash

AT = datetime(2026, 9, 8, 8, 0, tzinfo=JST)
PID = "A1-20260908-6857-01"


def proposal(pid=PID, code="6857", side="BUY", qty=100, price=1000.0):
    p = Proposal(proposal_id=pid, packet_hash="", code=code, side=side, qty=qty, lot_size=100,
                 limit_price=price, exec_condition="OPENING_LIMIT", account_type="CASH",
                 strategy="s", strategy_version="v1", as_of=date(2026, 9, 7), snapshot_id="s1",
                 policy_version="p1", expires_at=AT.replace(minute=59), reason="テスト",
                 events={"next_earnings_date": None, "margin_regulated": False})
    return replace(p, packet_hash=packet_hash(p))


def ev(kind="FILLED", id="e1", qty=100, price=1000, fee=0, pid=PID, **kw):
    return R(event_id=id, proposal_id=pid, kind=kind, qty=qty, price=price, fee=fee,
             at=AT + timedelta(hours=1), source="line", broker_order_id="b1", **kw)


def bal(l):
    return l.cash(), l.reserved(), l.available(), {c: (p.qty, p.avg_price) for c, p in l.positions().items()}


def sent(l, p):
    l.create_notice(p, at=AT)
    l.set_notice_state(p.proposal_id, "APPROVED", AT)
    l.set_notice_state(p.proposal_id, "SENT", AT)


@pytest.fixture
def lg(tmp_path):
    l = Ledger(tmp_path / "l.sqlite")
    l.init_snapshot(1_000_000, [], [], AT - timedelta(days=1))
    sent(l, proposal())
    yield l
    l.close()


def test_inv1_idempotent(lg):
    r = lg.report(ev()); assert r.applied and r.trade_state == "FILLED"
    once = bal(lg)
    r2 = lg.report(ev(price=999)); assert r2.duplicate and not r2.applied
    assert bal(lg) == once and lg.cash() == 900_000 and lg.reserved() == 0


def test_inv3_partial_40_cancel_60(lg):
    assert lg.report(ev("PARTIAL", qty=40)).applied
    assert lg.reserved() == 60 * 1002
    r = lg.report(ev("CANCELLED", id="e2", qty=60, price=0))
    assert r.applied and r.trade_state == "FILLED" and r.warnings
    assert lg.cash() == 960_000 and lg.positions()["6857"].qty == 40 and lg.reserved() == 0


def test_cancel_without_fill(lg):
    assert lg.report(ev("CANCELLED", qty=100, price=0)).trade_state == "CANCELLED"
    assert lg.reserved() == 0 and lg.cash() == 1_000_000


def test_inv4_expire_keeps_reservation(lg):
    before = bal(lg)
    assert lg.expire_notices(AT.replace(minute=59)) == []
    assert lg.expire_notices(AT.replace(minute=59) + timedelta(seconds=1)) == [PID]
    assert bal(lg) == before and lg.reserved() == 100_200
    assert lg.notices()[0]["trade_state"] == "UNCONFIRMED"


def test_inv5_unconfirmed_listing(lg):
    assert lg.unconfirmed(AT + timedelta(days=1)) == [PID]
    lg.expire_notices(AT + timedelta(hours=2))
    assert lg.unconfirmed(AT + timedelta(days=2)) == [PID]
    lg.report(ev("ORDERED", qty=100)); assert lg.unconfirmed(AT) == [PID]
    lg.report(ev("PARTIAL", id="e2", qty=40)); assert lg.unconfirmed(AT) == [PID]
    lg.report(ev(id="e3", qty=60)); assert lg.unconfirmed(AT) == []


def test_created_not_unconfirmed(tmp_path):
    l = Ledger(tmp_path / "x.sqlite"); l.init_snapshot(10**6, [], [], AT)
    l.create_notice(proposal()); assert l.unconfirmed(AT) == []


def test_inv6_correction(lg, tmp_path):
    lg.report(ev())
    r = lg.report(ev("CORRECTION", id="fix", price=900))
    assert r.applied and lg.cash() == 910_000 and lg.positions()["6857"].avg_price == 900
    once = bal(lg); assert lg.report(ev("CORRECTION", id="fix", price=900)).duplicate
    assert bal(lg) == once
    kinds = [e["kind"] for e in lg.events()]
    assert "REVERSAL" in kinds and kinds.count("REPORT") == 2
    assert Ledger(tmp_path / "l.sqlite").cash() == 910_000


def test_correction_by_replaces_id(lg):
    lg.report(ev("PARTIAL", id="a", qty=40)); lg.report(ev("PARTIAL", id="b", qty=60, price=1010))
    lg.report(ev("CORRECTION", id="c", qty=40, price=990, replaces_event_id="a"))
    assert lg.positions()["6857"].qty == 100
    assert lg.cash() == 1_000_000 - 40 * 990 - 60 * 1010 and lg.reserved() == 0


def test_correction_without_target_refused(lg):
    assert lg.report(ev("CORRECTION", id="c")).error


def test_inv7_snapshot_required(tmp_path):
    l = Ledger(tmp_path / "x.sqlite"); assert not l.is_initialized()
    with pytest.raises(LedgerNotInitialized): l.create_notice(proposal())
    with pytest.raises(LedgerNotInitialized): l.report(ev())
    with pytest.raises(LedgerNotInitialized): l.cash()


def test_inv8_negative_available_refused(lg):
    before = bal(lg); r = lg.report(ev(price=20000))
    assert r.error and not r.applied and bal(lg) == before and lg.available() >= 0
    assert not lg.report(ev(price=20000)).duplicate  # 拒否は記録されず再送可能


@pytest.mark.parametrize("qty,price,fee", [(101, 1000, 0), (-1, 1000, 0), (100, -1, 0), (100, 1000, -1)])
def test_invalid_fill_atomic(lg, qty, price, fee):
    before = bal(lg); assert lg.report(ev(qty=qty, price=price, fee=fee)).error; assert bal(lg) == before


def test_inv9_events_append_only(lg, tmp_path):
    def rows():
        with sqlite3.connect(tmp_path / "l.sqlite") as c:
            return c.execute("SELECT * FROM ledger_events").fetchall()
    a = rows(); lg.report(ev()); b = rows(); lg.report(ev("CORRECTION", id="f", price=900)); c = rows()
    assert len(c) > len(b) > len(a) and all(r in c for r in b)
    kinds = [e["kind"] for e in lg.events()]
    assert kinds[:4] == ["SNAPSHOT", "NOTICE_CREATED", "NOTICE_STATE", "NOTICE_STATE"]


def test_filled_with_remaining_is_partial(lg):
    r = lg.report(ev(qty=40)); assert r.trade_state == "PARTIAL" and r.warnings
    assert lg.reserved() == 60 * 1002


def test_ordered_then_ignored_after_fill(lg):
    assert lg.report(ev("ORDERED", id="o")).trade_state == "ORDERED"
    lg.report(ev()); r = lg.report(ev("ORDERED", id="o2"))
    assert not r.applied and r.ignored_reason and r.trade_state == "FILLED"


def test_skipped(lg):
    assert lg.report(ev("SKIPPED", qty=0, price=0)).trade_state == "SKIPPED"
    assert lg.reserved() == 0


def test_skipped_after_fill_refused(lg):
    lg.report(ev("PARTIAL", qty=40)); assert lg.report(ev("SKIPPED", id="s", qty=0)).error


def test_sell_flow(tmp_path):
    l = Ledger(tmp_path / "s.sqlite")
    l.init_snapshot(0, [R(code="7203", qty=100, avg_price=2000)], [], AT)
    sent(l, proposal(pid="S1", code="7203", side="SELL", qty=100, price=2100))
    assert l.reserved() == 0
    assert l.report(ev(pid="S1", price=2100, fee=100)).applied
    assert l.cash() == 209_900 and "7203" not in l.positions()


def test_sell_insufficient(tmp_path):
    l = Ledger(tmp_path / "s.sqlite"); l.init_snapshot(0, [], [], AT)
    sent(l, proposal(pid="S1", side="SELL")); assert l.report(ev(pid="S1")).error


def test_reject_releases_and_transitions(tmp_path):
    l = Ledger(tmp_path / "x.sqlite"); l.init_snapshot(10**6, [], [], AT)
    l.create_notice(proposal()); assert l.reserved() == 100_200
    with pytest.raises(LedgerError): l.set_notice_state(PID, "SENT", AT)
    with pytest.raises(LedgerError): l.create_notice(proposal())
    l.set_notice_state(PID, "REJECTED", AT)
    assert l.reserved() == 0 and l.notices()[0]["trade_state"] == "SKIPPED"


def test_external_open_order(tmp_path):
    l = Ledger(tmp_path / "x.sqlite")
    l.init_snapshot(10**6, [], [R(proposal_id="X1", code="1234", side="BUY", qty=100, limit_price=500)], AT)
    assert l.reserved() == 50_100 and l.reserved_positions() == {"1234": 50_100}
    assert l.notices()[0]["notice_state"] == "EXTERNAL"


def test_new_sent_today(lg):
    assert lg.new_sent_today(date(2026, 9, 8)) == 1 and lg.new_sent_today(date(2026, 9, 7)) == 0


def test_stop_adjust_view(lg):
    lg.report(ev())
    lg.set_stop_order("6857", True, 930, AT)
    lg.adjust("DEPOSIT", 10_000, None, None, AT, "入金")
    lg.adjust("SPLIT", None, "6857", 2, AT, "分割")
    v = lg.view(day_start_equity=1_000_000)
    assert v.cash == 910_000 and v.positions["6857"].qty == 200 and v.positions["6857"].stop_order
    assert v.positions["6857"].avg_price == 500
    with pytest.raises(LedgerError): lg.adjust("WITHDRAW", 10**7, None, None, AT, "")
