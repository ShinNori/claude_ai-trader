"""Claude 第21回 独立反証: EXIT 生成 v6（derive_holdings / build_exit_proposals）の、Codex の 33 試験が触れていない境界。

対象: aitrader/exit_holdings.py, aitrader/packet.py build_exit_proposals。製品・既存試験・例は変更しない。
R21-01（非 SPLIT の調整記録で例外）・R21-02（journal 不在で例外）は契約未確定のためここでは固定しない。
"""
from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from aitrader.exit_holdings import derive_holdings
from aitrader.packet import Proposal, build_exit_proposals, packet_hash
from aitrader_ops.ledger import Ledger, LedgerError
from aitrader_ops.models import TradeEvent

JST = timezone(timedelta(hours=9))
AS_OF = date(2026, 9, 29)
AT = datetime(2026, 9, 1, 7, tzinfo=JST)
CALENDAR = [date(2026, 9, 1) + timedelta(days=i) for i in range(61)
            if (date(2026, 9, 1) + timedelta(days=i)).weekday() < 5]
FIXTURE = json.loads((Path(__file__).with_name("fixtures") / "exit_v6_expected.json").read_text(encoding="utf-8"))


def lot(code="7203", pid="A1-20260901-7203-01", entry=date(2026, 9, 1), qty=100, held=100,
        reserved=0, unattributed=0, open_sell=False, version="v1", seq=42):
    return dict(code=code, observed_seq=seq, source_proposal_id=pid, strategy="margin_bucket_long",
                strategy_version=version, entry_date=entry, lot_qty=qty, held_qty=held,
                reserved_shares=reserved, unattributed_qty=unattributed, open_sell_notice=open_sell)


def build(rows, *, prices=None, lots=None, calendar=None, as_of=AS_OF, holding_days=20):
    codes = {r["code"] for r in rows}
    return build_exit_proposals(rows, as_of, prices if prices is not None else {c: 2000 for c in codes},
                                lots if lots is not None else {c: 100 for c in codes},
                                {c: {"next_earnings_date": None, "margin_regulated": False} for c in codes},
                                "a" * 64, "v1", holding_days, business_days=CALENDAR if calendar is None else calendar)


def proposal(pid, side="BUY", code="7203", qty=100, *, events=None, external=False):
    p = Proposal(pid, "", code, side, qty, 100, 1000.0, "EXTERNAL" if external else "OPENING_LIMIT",
                 "CASH", "margin_bucket_long", "v1", date(2026, 9, 1), "a" * 64, "v1",
                 datetime(2026, 10, 1, 8, 59, tzinfo=JST), "", events or {})
    return Proposal(**{**p.__dict__, "packet_hash": packet_hash(p)})


def journal(path, rows=()):
    with closing(sqlite3.connect(path)) as con:
        con.execute("CREATE TABLE candidates(pid TEXT PRIMARY KEY, hash TEXT, day TEXT, side TEXT, state TEXT, result TEXT, owner TEXT)")
        con.executemany("INSERT INTO candidates VALUES(?,?,?,?,?,?,?)",
                        [(pid, "", "2026-09-29", side, state, "", "") for pid, side, state in rows])
        con.commit()


def snapshot(at=AT, positions=(), open_orders=()):
    return {"positions": list(positions), "open_orders": list(open_orders), "at": at.isoformat()}


# ------------------------------------------------------------------ 固定例の独立再計算

def test_fixed_example_hash_is_reproduced_independently():
    rows = [lot(pid="A1-20260901-7203-01", held=300, unattributed=100),
            lot(pid="A1-20260901-7203-02", held=300, unattributed=100)]
    result = build(rows)
    assert result.excluded == []
    p = result.proposals[0]
    assert (p.proposal_id, p.qty, p.limit_price) == ("X1-20260930-7203-01", 200, 1990.0)
    assert p.events["exit_lots"] == [{"source_proposal_id": "A1-20260901-7203-01", "qty": 100},
                                      {"source_proposal_id": "A1-20260901-7203-02", "qty": 100}]
    assert p.packet_hash == FIXTURE["fixed_example"]["packet_hash"]
    assert packet_hash(p) == p.packet_hash


# ------------------------------------------------------------------ 生成器の境界

@pytest.mark.parametrize("prev_close, expected", [
    (1005.03, 1005.0),   # raw=1000.00485 → 刻み 5 → 切り上げ 1005
    (1004.0, 999.0),     # raw=998.98 → 刻み 1 → 999
    (5025.13, 5010.0),   # raw=5000.004 → 刻み 10（5000 超）→ 5010
    (5025.0, 5000.0),    # raw=4999.875 → 刻み 5 → 5000
    (999.0, 995.0),      # raw=994.005 → 刻み 1 → 995
])
def test_sell_limit_ceiling_at_each_tick_band_boundary(prev_close, expected):
    result = build([lot()], prices={"7203": prev_close})
    assert result.proposals[0].limit_price == expected


def test_sellable_cap_allocates_front_lots_only():
    rows = [lot(pid="A1-20260901-7203-01", held=200, reserved=50),
            lot(pid="A1-20260901-7203-02", held=200, reserved=50)]
    result = build(rows)
    p = result.proposals[0]
    assert p.qty == 100
    assert p.events["exit_lots"] == [{"source_proposal_id": "A1-20260901-7203-01", "qty": 100}]


def test_holding_days_one_is_due_on_next_business_day():
    rows = [lot(entry=date(2026, 9, 28))]
    assert build(rows, as_of=date(2026, 9, 29), holding_days=1).proposals[0].qty == 100
    assert build(rows, as_of=date(2026, 9, 28), holding_days=1).excluded[0]["reason_code"] == "NOT_DUE"


def test_two_codes_sequence_and_exclusions_interleave_deterministically():
    rows = [lot(code="9984", pid="A1-20260901-9984-01"), lot(code="7203"), lot(code="6857", pid="A1-20260908-6857-01", entry=date(2026, 9, 8))]
    result = build(rows)
    assert [p.proposal_id for p in result.proposals] == ["X1-20260930-7203-01", "X1-20260930-9984-02"]
    assert result.excluded == [{"code": "6857", "source_proposal_id": "A1-20260908-6857-01", "reason_code": "NOT_DUE"}]
    again = build(copy.deepcopy(rows))
    assert [p.packet_hash for p in again.proposals] == [p.packet_hash for p in result.proposals]


def test_exit_lots_do_not_affect_packet_hash():
    rows = [lot()]
    p = build(rows).proposals[0]
    q = Proposal(**{**p.__dict__, "events": {**p.events, "exit_lots": []}})
    assert packet_hash(q) == p.packet_hash


@pytest.mark.parametrize("bad", [True, -1, "42", 4.0])
def test_observed_seq_must_be_a_nonnegative_int(bad):
    with pytest.raises(ValueError):
        build([lot(seq=bad)])


def test_open_sell_row_is_excluded_even_when_other_rows_of_code_disagree():
    rows = [lot(pid="A1-20260901-7203-01", open_sell=True), lot(pid="A1-20260901-7203-02", open_sell=False)]
    with pytest.raises(ValueError):
        build(rows)


def test_inputs_are_not_mutated_and_no_calendar_after_as_of_excludes():
    rows = [lot()]
    before = copy.deepcopy(rows)
    result = build(rows, calendar=[d for d in CALENDAR if d <= AS_OF])
    assert rows == before
    assert result.proposals == [] and result.excluded[0]["reason_code"] == "INSUFFICIENT_CALENDAR"


# ------------------------------------------------------------------ 導出の境界（実 Ledger、Dropbox 外の一時 DB）

def _ledger_with_two_lots(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.init_snapshot(1_000_000, [], [], AT)
    for pid in ("buy-a", "buy-b"):
        ledger.create_notice(proposal(pid), at=AT)
        assert ledger.report(TradeEvent("fill-" + pid, pid, "FILLED", 100, 1000, 0, AT, "manual")).applied
    return ledger


def test_cancelled_sell_after_partial_fill_consumes_only_the_filled_part(tmp_path):
    ledger = _ledger_with_two_lots(tmp_path)
    sell = proposal("sell", "SELL", qty=200, events={"exit_lots": [
        {"source_proposal_id": "buy-a", "qty": 100}, {"source_proposal_id": "buy-b", "qty": 100}]})
    t = AT + timedelta(days=21)
    ledger.create_notice(sell, at=t)
    ledger.set_notice_state("sell", "APPROVED", t); ledger.set_notice_state("sell", "SENT", t)
    assert ledger.report(TradeEvent("s1", "sell", "PARTIAL", 50, 1000, 0, t, "manual")).applied
    assert ledger.report(TradeEvent("s2", "sell", "CANCELLED", 150, 1000, 0, t + timedelta(hours=1), "manual")).applied
    j = tmp_path / "j.sqlite"; journal(j, [("buy-a", "BUY", "APPROVED"), ("buy-b", "BUY", "APPROVED"), ("sell", "SELL", "APPROVED")])
    result = derive_holdings(ledger, j, snapshot(), [], AS_OF)
    assert result.reason_codes == []
    assert [(h["source_proposal_id"], h["lot_qty"], h["open_sell_notice"]) for h in result.holdings] == [
        ("buy-a", 50, False), ("buy-b", 100, False)]
    assert result.holdings[0]["reserved_shares"] == 0
    ledger.close()


def test_exit_lots_referencing_a_lot_already_consumed_is_inconsistent(tmp_path):
    ledger = _ledger_with_two_lots(tmp_path)
    t = AT + timedelta(days=10)
    ledger.create_notice(proposal("ext-sell", "SELL", qty=100, external=True), at=t)
    assert ledger.report(TradeEvent("e1", "ext-sell", "FILLED", 100, 1000, 0, t, "manual")).applied  # 紐づかない売却 → buy-a を消費
    sell = proposal("sell", "SELL", qty=100, events={"exit_lots": [{"source_proposal_id": "buy-a", "qty": 100}]})
    t2 = AT + timedelta(days=21)
    ledger.create_notice(sell, at=t2); ledger.set_notice_state("sell", "APPROVED", t2); ledger.set_notice_state("sell", "SENT", t2)
    assert ledger.report(TradeEvent("s1", "sell", "FILLED", 100, 1000, 0, t2, "manual")).applied
    j = tmp_path / "j.sqlite"; journal(j, [("buy-a", "BUY", "APPROVED"), ("buy-b", "BUY", "APPROVED"), ("sell", "SELL", "APPROVED")])
    snap = snapshot(open_orders=[{"proposal_id": "ext-sell", "code": "7203", "side": "SELL", "qty": 100, "limit_price": 1000.0}])
    # ext-sell は snapshot 由来でないと EXTERNAL 照合で拒否される → LEDGER_INCONSISTENT（どちらの理由でも生成は止まる）
    result = derive_holdings(ledger, j, snap, [], AS_OF)
    assert result.reason_codes == ["LEDGER_INCONSISTENT"] and result.holdings == []
    ledger.close()


def test_open_external_buy_does_not_flag_open_sell_and_keeps_balance(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    orders = [{"proposal_id": None, "code": "7203", "side": "BUY", "qty": 100, "limit_price": 1000.0}]
    from aitrader_ops.models import OpenOrderIn
    ledger.init_snapshot(1_000_000, [], [OpenOrderIn(None, "7203", "BUY", 100, 1000.0)], AT)
    ledger.create_notice(proposal("buy-a"), at=AT)
    assert ledger.report(TradeEvent("f", "buy-a", "FILLED", 100, 1000, 0, AT, "manual")).applied
    j = tmp_path / "j.sqlite"; journal(j, [("buy-a", "BUY", "APPROVED")])
    result = derive_holdings(ledger, j, snapshot(open_orders=orders), [], AS_OF)
    assert result.reason_codes == []
    assert result.holdings[0]["open_sell_notice"] is False and result.holdings[0]["held_qty"] == 100
    assert "external-1" in result.candidate_ids
    ledger.close()


def test_approved_candidate_without_notice_is_inconsistent(tmp_path):
    ledger = _ledger_with_two_lots(tmp_path)
    j = tmp_path / "j.sqlite"; journal(j, [("buy-a", "BUY", "APPROVED"), ("buy-b", "BUY", "APPROVED"), ("ghost", "BUY", "APPROVED")])
    assert derive_holdings(ledger, j, snapshot(), [], AS_OF).reason_codes == ["LEDGER_INCONSISTENT"]
    j2 = tmp_path / "j2.sqlite"; journal(j2, [("buy-a", "BUY", "APPROVED"), ("buy-b", "BUY", "APPROVED"), ("ghost", "BUY", "REJECTED")])
    assert derive_holdings(ledger, j2, snapshot(), [], AS_OF).reason_codes == []
    ledger.close()


def test_buy_correction_below_consumed_quantity_is_inconsistent(tmp_path):
    ledger = _ledger_with_two_lots(tmp_path)
    t = AT + timedelta(days=21)
    sell = proposal("sell", "SELL", qty=100, events={"exit_lots": [{"source_proposal_id": "buy-a", "qty": 100}]})
    ledger.create_notice(sell, at=t); ledger.set_notice_state("sell", "APPROVED", t); ledger.set_notice_state("sell", "SENT", t)
    assert ledger.report(TradeEvent("s1", "sell", "FILLED", 100, 1000, 0, t, "manual")).applied
    # buy-a の約定を 40 株へ訂正。台帳は銘柄合計（保有 100 ≥ 100）でしか守らないので受理される。
    # 導出側では exit_lots が buy-a から 100 株消費済みなのに残 40 → ロット単位の矛盾として拒否（D15-10）。
    assert ledger.report(TradeEvent("fix", "buy-a", "CORRECTION", 40, 1000, 0, t + timedelta(hours=1), "manual", replaces_event_id="fill-buy-a")).applied
    j = tmp_path / "j.sqlite"; journal(j, [("buy-a", "BUY", "APPROVED"), ("buy-b", "BUY", "APPROVED"), ("sell", "SELL", "APPROVED")])
    result = derive_holdings(ledger, j, snapshot(), [], AS_OF)
    assert result.reason_codes == ["LEDGER_INCONSISTENT"] and result.holdings == []
    ledger.close()


def test_derive_output_feeds_builder_and_generated_sell_reserves_shares(tmp_path):
    ledger = _ledger_with_two_lots(tmp_path)
    j = tmp_path / "j.sqlite"; journal(j, [("buy-a", "BUY", "APPROVED"), ("buy-b", "BUY", "APPROVED")])
    derived = derive_holdings(ledger, j, snapshot(), [], AS_OF)
    assert derived.reason_codes == []
    built = build(derived.holdings)
    p = built.proposals[0]
    assert p.qty == 200 and p.events["exit_lots"] == [{"source_proposal_id": "buy-a", "qty": 100},
                                                      {"source_proposal_id": "buy-b", "qty": 100}]
    t = AT + timedelta(days=29)
    ledger.create_notice(p, at=t)
    assert ledger.view().reserved_shares.get("7203") == 200
    # CREATED 状態の SELL が既知集合の外（journal 未登録）にある場合: unconfirmed は SENT/EXPIRED/EXTERNAL しか列挙しないため
    # 導出は通る（R21-03 の契約の穴）。ただし売却予約株数が保有と同数なので生成側は NO_SELLABLE_SHARES で候補を出さない（安全側の不変条件）。
    unknown = derive_holdings(ledger, j, snapshot(), [], AS_OF)
    assert unknown.reason_codes == [] and all(not h["open_sell_notice"] for h in unknown.holdings)
    assert unknown.holdings[0]["reserved_shares"] == 200
    rebuilt = build(unknown.holdings)
    assert rebuilt.proposals == [] and {e["reason_code"] for e in rebuilt.excluded} == {"NO_SELLABLE_SHARES"}
    # journal に登録されていれば OPEN_SELL_EXISTS で除外される
    j2 = tmp_path / "j2.sqlite"; journal(j2, [("buy-a", "BUY", "APPROVED"), ("buy-b", "BUY", "APPROVED"), (p.proposal_id, "SELL", "APPROVED")])
    again = derive_holdings(ledger, j2, snapshot(), [], AS_OF)
    assert again.reason_codes == [] and all(h["open_sell_notice"] for h in again.holdings)
    assert {e["reason_code"] for e in build(again.holdings).excluded} == {"OPEN_SELL_EXISTS"}
    ledger.close()


def test_derive_is_read_only_for_ledger_and_journal(tmp_path):
    ledger = _ledger_with_two_lots(tmp_path)
    j = tmp_path / "j.sqlite"; journal(j, [("buy-a", "BUY", "APPROVED"), ("buy-b", "BUY", "APPROVED")])
    seq = ledger.seq(); jbytes = j.read_bytes()
    derive_holdings(ledger, j, snapshot(), [], AS_OF)
    assert ledger.seq() == seq and j.read_bytes() == jbytes
    ledger.close()
