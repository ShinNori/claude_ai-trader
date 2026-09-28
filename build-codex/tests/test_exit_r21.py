"""R21 EXIT derivation hardening regressions (offline only)."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

import pytest

from aitrader.exit_holdings import derive_holdings
from aitrader.packet import Proposal, packet_hash
from aitrader_ops.ledger import Ledger
from aitrader_ops.models import TradeEvent

JST = timezone(timedelta(hours=9))
AT = datetime(2026, 9, 1, 7, tzinfo=JST)
AS_OF = date(2026, 9, 29)


def proposal(pid, side="BUY", code="7203", qty=100):
    value = Proposal(
        pid, "", code, side, qty, 100, 1000.0, "OPENING_LIMIT", "CASH",
        "margin_bucket_long", "v1", AT.date(), "a" * 64, "v1",
        datetime(2026, 10, 1, 8, 59, tzinfo=JST), "", {},
    )
    return Proposal(**{**value.__dict__, "packet_hash": packet_hash(value)})


def journal(path, rows=()):
    with closing(sqlite3.connect(path)) as con:
        con.execute(
            "CREATE TABLE candidates(pid TEXT PRIMARY KEY, hash TEXT, day TEXT, "
            "side TEXT, state TEXT, result TEXT, owner TEXT)"
        )
        con.executemany(
            "INSERT INTO candidates VALUES(?,?,?,?,?,?,?)",
            [(pid, "", "2026-09-29", side, state, "", "")
             for pid, side, state in rows],
        )
        con.commit()


def snapshot(positions=()):
    return {"positions": list(positions), "open_orders": [], "at": AT.isoformat()}


class NoLedgerAccess:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name in {"seq", "view", "unconfirmed", "proposal", "notice"}:
            def forbidden(*args, **kwargs):
                self.calls.append(name)
                raise AssertionError(f"ledger.{name} must not be called")
            return forbidden
        raise AttributeError(name)


@pytest.mark.parametrize("journal_kind", ["missing", "no_candidates_table"])
def test_r21_02_unreadable_journal_stops_before_any_ledger_access(tmp_path, journal_kind):
    path = tmp_path / "journal.sqlite"
    if journal_kind == "no_candidates_table":
        with closing(sqlite3.connect(path)) as con:
            con.execute("CREATE TABLE unrelated(value TEXT)")
            con.commit()
    ledger = NoLedgerAccess()

    result = derive_holdings(ledger, path, snapshot(), [], AS_OF)

    assert result.reason_codes == ["JOURNAL_UNREADABLE"]
    assert result.holdings == [] and result.observed_seq is None
    assert result.candidate_ids == () and ledger.calls == []
    if journal_kind == "missing":
        assert not path.exists()


@pytest.mark.parametrize("notice_state", ["CREATED", "APPROVED"])
def test_46_unknown_created_or_approved_sell_reservation_is_inconsistent(tmp_path, notice_state):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.init_snapshot(1_000_000, [{"code": "7203", "qty": 200, "avg_price": 900}], [], AT)
    ledger.create_notice(proposal("unknown-sell", "SELL"), at=AT)
    if notice_state == "APPROVED":
        ledger.set_notice_state("unknown-sell", "APPROVED", AT)
    path = tmp_path / "journal.sqlite"
    journal(path)

    result = derive_holdings(
        ledger, path, snapshot([{"code": "7203", "qty": 200}]), [], AS_OF
    )

    assert result.reason_codes == ["LEDGER_INCONSISTENT"]
    assert result.holdings == []
    ledger.close()


@pytest.mark.parametrize("notice_state", ["CREATED", "APPROVED"])
def test_unknown_created_or_approved_buy_reservation_is_inconsistent(tmp_path, notice_state):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.init_snapshot(1_000_000, [], [], AT)
    ledger.create_notice(proposal("unknown-buy"), at=AT)
    if notice_state == "APPROVED":
        ledger.set_notice_state("unknown-buy", "APPROVED", AT)
    path = tmp_path / "journal.sqlite"
    journal(path)

    result = derive_holdings(ledger, path, snapshot(), [], AS_OF)

    assert result.reason_codes == ["LEDGER_INCONSISTENT"]
    assert result.holdings == []
    ledger.close()


def test_known_sell_partial_and_cancel_reservations_match_view(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.init_snapshot(1_000_000, [{"code": "7203", "qty": 200, "avg_price": 900}], [], AT)
    ledger.create_notice(proposal("sell", "SELL"), at=AT)
    ledger.set_notice_state("sell", "APPROVED", AT)
    ledger.set_notice_state("sell", "SENT", AT)
    assert ledger.report(TradeEvent("partial", "sell", "PARTIAL", 40, 1000, 0, AT,
                                    "manual")).applied
    path = tmp_path / "journal.sqlite"
    journal(path, [("sell", "SELL", "APPROVED")])
    source = snapshot([{"code": "7203", "qty": 200}])

    partial = derive_holdings(ledger, path, source, [], AS_OF)
    assert partial.reason_codes == []
    assert ledger.notice("sell")["reserved_shares"] == 60
    assert ledger.view().reserved_shares == {"7203": 60}

    assert ledger.report(TradeEvent("cancel", "sell", "CANCELLED", 60, 1000, 0,
                                    AT + timedelta(hours=1), "manual")).applied
    cancelled = derive_holdings(ledger, path, source, [], AS_OF)
    assert cancelled.reason_codes == []
    assert ledger.notice("sell")["reserved_shares"] == 0
    assert ledger.view().reserved_shares == {}
    ledger.close()


def test_known_buy_partial_and_cancel_reservations_match_view(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.init_snapshot(1_000_000, [], [], AT)
    ledger.create_notice(proposal("buy"), at=AT)
    ledger.set_notice_state("buy", "APPROVED", AT)
    ledger.set_notice_state("buy", "SENT", AT)
    assert ledger.report(TradeEvent("partial", "buy", "PARTIAL", 40, 1000, 0, AT,
                                    "manual")).applied
    path = tmp_path / "journal.sqlite"
    journal(path, [("buy", "BUY", "APPROVED")])

    partial = derive_holdings(ledger, path, snapshot(), [], AS_OF)
    assert partial.reason_codes == [] and partial.holdings[0]["lot_qty"] == 40
    assert ledger.notice("buy")["reserve"] == ledger.view().reserved_positions["7203"]

    assert ledger.report(TradeEvent("cancel", "buy", "CANCELLED", 60, 1000, 0,
                                    AT + timedelta(hours=1), "manual")).applied
    cancelled = derive_holdings(ledger, path, snapshot(), [], AS_OF)
    assert cancelled.reason_codes == [] and cancelled.holdings[0]["lot_qty"] == 40
    assert ledger.notice("buy")["reserve"] == 0
    assert ledger.view().reserved_positions == {}
    ledger.close()


def test_r21_01_non_split_is_ignored_and_absent_kind_remains_split(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.init_snapshot(1_000_000, [], [], AT)
    ledger.create_notice(proposal("buy"), at=AT)
    assert ledger.report(TradeEvent("fill", "buy", "FILLED", 100, 1000, 0, AT,
                                    "manual")).applied
    path = tmp_path / "journal.sqlite"
    journal(path, [("buy", "BUY", "APPROVED")])

    dividend = derive_holdings(
        ledger, path, snapshot(),
        [{"kind": "DIVIDEND", "code": "7203", "amount": 500}], AS_OF,
    )
    assert dividend.reason_codes == [] and dividend.holdings[0]["lot_qty"] == 100

    split_at = AT + timedelta(days=1)
    ledger.adjust("SPLIT", None, "7203", 2, split_at, "split")
    legacy_split = derive_holdings(
        ledger, path, snapshot(),
        [{"code": "7203", "ratio": 2, "at": split_at.isoformat()}], AS_OF,
    )
    assert legacy_split.reason_codes == [] and legacy_split.holdings[0]["lot_qty"] == 200
    ledger.close()


@pytest.mark.parametrize("ratio", [None, "bad", 0, -1, "NaN", "Infinity"])
def test_r21_01_malformed_split_ratio_is_ledger_inconsistent(tmp_path, ratio):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.init_snapshot(1_000_000, [], [], AT)
    ledger.create_notice(proposal("buy"), at=AT)
    assert ledger.report(TradeEvent("fill", "buy", "FILLED", 100, 1000, 0, AT,
                                    "manual")).applied
    path = tmp_path / "journal.sqlite"
    journal(path, [("buy", "BUY", "APPROVED")])

    result = derive_holdings(
        ledger, path, snapshot(),
        [{"kind": "SPLIT", "code": "7203", "ratio": ratio,
          "at": (AT + timedelta(days=1)).isoformat()}], AS_OF,
    )

    assert result.reason_codes == ["LEDGER_INCONSISTENT"]
    assert result.holdings == []
    ledger.close()
