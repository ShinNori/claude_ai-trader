"""Crash-boundary contracts for the mock runner's cross-database writes."""
import sqlite3

import pytest

from test_runner import DAY, Ledger, RunError, connect, setup


def _leave_intent_without_notice(execute, monkeypatch):
    original = Ledger.create_notice

    def fail_before_notice(*args, **kwargs):
        raise RuntimeError("injected before ledger notice")

    monkeypatch.setattr(Ledger, "create_notice", fail_before_notice)
    with pytest.raises(RuntimeError, match="injected before ledger notice"):
        execute()
    monkeypatch.setattr(Ledger, "create_notice", original)
    return original


def _ledger_counts(home):
    with sqlite3.connect(home / "ledger.sqlite") as con:
        return con.execute(
            "SELECT kind, count(*) FROM ledger_events GROUP BY kind ORDER BY kind"
        ).fetchall()


def test_same_input_resumes_intent_without_notice_exactly_once(setup, monkeypatch):
    home, execute = setup
    _leave_intent_without_notice(execute, monkeypatch)

    with sqlite3.connect(home / "orchestration.sqlite") as con:
        assert con.execute(
            "SELECT state, owner FROM candidates WHERE pid='buy1'"
        ).fetchone() == ("INTENT", "run1")
    ledger = Ledger(home / "ledger.sqlite")
    with pytest.raises(KeyError):
        ledger.notice("buy1")
    ledger.close()

    result = execute()
    assert result["candidates"][0]["status"] == "APPROVED"
    after_first_success = _ledger_counts(home)
    assert execute() == result
    assert _ledger_counts(home) == after_first_success

    ledger = Ledger(home / "ledger.sqlite")
    assert ledger.notice("buy1")["notice_state"] == "APPROVED"
    assert ledger.reserved() == 100200
    ledger.close()


def test_ledger_approved_journal_intent_stops_and_preserves_reservation(setup, monkeypatch):
    home, execute = setup
    original = Ledger.set_notice_state

    def fail_after_approved(self, proposal_id, state, at):
        value = original(self, proposal_id, state, at)
        raise RuntimeError("injected after ledger approval")

    monkeypatch.setattr(Ledger, "set_notice_state", fail_after_approved)
    with pytest.raises(RuntimeError, match="injected after ledger approval"):
        execute()
    monkeypatch.setattr(Ledger, "set_notice_state", original)

    ledger = Ledger(home / "ledger.sqlite")
    seq = ledger.seq()
    reserve = ledger.reserved()
    assert ledger.notice("buy1")["notice_state"] == "APPROVED"
    ledger.close()
    with sqlite3.connect(home / "orchestration.sqlite") as con:
        assert con.execute(
            "SELECT state FROM candidates WHERE pid='buy1'"
        ).fetchone()[0] == "INTENT"

    with pytest.raises(RunError, match="台帳だけ"):
        execute()
    ledger = Ledger(home / "ledger.sqlite")
    assert ledger.seq() == seq
    assert ledger.reserved() == reserve == 100200
    assert ledger.notice("buy1")["notice_state"] == "APPROVED"
    ledger.close()


@pytest.mark.parametrize("conflict", ["owner", "hash", "calendar"])
def test_intent_without_notice_rejects_changed_identity_or_market(
    setup, monkeypatch, conflict
):
    home, execute = setup
    _leave_intent_without_notice(execute, monkeypatch)

    if conflict in {"owner", "hash"}:
        column = "owner" if conflict == "owner" else "hash"
        value = "other-run" if conflict == "owner" else "0" * 64
        with sqlite3.connect(home / "orchestration.sqlite") as con:
            con.execute(
                f"UPDATE candidates SET {column}=? WHERE pid='buy1'", [value]
            )
            con.commit()
    else:
        with connect(home) as con:
            con.execute(
                "UPDATE calendar SET is_business_day=false WHERE date=?", [DAY]
            )

    expected = {
        "owner": "別run",
        "hash": "内容が変わっています",
        "calendar": "営業日ではありません",
    }[conflict]
    with pytest.raises(RunError, match=expected):
        execute()

    ledger = Ledger(home / "ledger.sqlite")
    with pytest.raises(KeyError):
        ledger.notice("buy1")
    assert ledger.reserved() == 0
    ledger.close()

