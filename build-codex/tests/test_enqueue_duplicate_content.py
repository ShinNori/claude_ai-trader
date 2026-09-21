"""W02 contract: candidate dedupe distinguishes a suppressed content change."""
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "ops")]

from aitrader_ops.ledger import Ledger  # noqa: E402
from aitrader_ops.models import PositionIn, Proposal, compute_packet_hash  # noqa: E402
from aitrader_ops.notify import Notifier  # noqa: E402

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 9, 7, 0, tzinfo=JST)
SETTINGS = {
    "broker": {"link_template": "https://www.rakuten-sec.co.jp/dummy?code={code}"},
    "line": {"allowed_user_id": "U-w02-offline", "monthly_budget": 200},
}


def proposal(kind: str) -> Proposal:
    value = Proposal(
        proposal_id=f"w02-{kind.lower()}", packet_hash="", code="6857",
        side="BUY" if kind == "NEW" else "SELL", qty=100, lot_size=100,
        limit_price=1000.0, exec_condition="OPENING_LIMIT", account_type="CASH",
        strategy="synthetic", strategy_version="v1", as_of=date(2026, 9, 8),
        snapshot_id="s1", policy_version="p1",
        expires_at=NOW.replace(hour=8, minute=59), reason="synthetic-only",
        events={"next_earnings_date": None, "margin_regulated": False},
    )
    return replace(value, packet_hash=compute_packet_hash(value))


@pytest.fixture(params=["NEW", "EXIT"])
def candidate(tmp_path, request):
    kind = request.param
    ledger = Ledger(tmp_path / "ledger.sqlite")
    positions = [PositionIn("6857", 100, 900.0)] if kind == "EXIT" else []
    ledger.init_snapshot(1_000_000, positions, [], NOW - timedelta(days=1))
    item = proposal(kind)
    ledger.create_notice(item, at=NOW)
    ledger.set_notice_state(item.proposal_id, "APPROVED", NOW)
    notifier = Notifier(
        ledger=ledger, state_path=tmp_path / "notify.sqlite",
        settings=SETTINGS, transport="stub", stub_results=[],
    )
    yield kind, item, notifier
    notifier.close()
    ledger.close()


def enqueue(notifier, kind, item, *, key, text):
    return notifier.enqueue(
        key=key, kind=kind, message={"type": "text", "text": text},
        proposal_id=item.proposal_id, now=NOW,
    )


def persisted_row(notifier):
    with closing(sqlite3.connect(notifier.path)) as con:
        return con.execute(
            "SELECT key, kind, proposal_id, message, content_hash, recipient, retry_key, "
            "state, month, expires_at, created_at, updated_at, attempts, seq, claimed_by, claimed_at "
            "FROM outbox"
        ).fetchall()


def test_same_key_changed_content_still_raises(candidate):
    kind, item, notifier = candidate
    enqueue(notifier, kind, item, key="original-key", text="original")
    before = persisted_row(notifier)
    with pytest.raises(ValueError, match="同じ key"):
        enqueue(notifier, kind, item, key="original-key", text="corrected")
    assert persisted_row(notifier) == before


def test_different_key_exact_duplicate_keeps_legacy_response_shape(candidate):
    kind, item, notifier = candidate
    first = enqueue(notifier, kind, item, key="original-key", text="original")
    before = persisted_row(notifier)
    duplicate = enqueue(notifier, kind, item, key="alternate-key", text="original")
    assert first == {"key": "original-key", "state": "PENDING", "duplicate": False}
    assert duplicate == {"key": "original-key", "state": "PENDING", "duplicate": True}
    assert persisted_row(notifier) == before


def test_different_key_changed_content_is_flagged_without_mutation(candidate):
    kind, item, notifier = candidate
    enqueue(notifier, kind, item, key="original-key", text="original")
    before = persisted_row(notifier)
    result = enqueue(notifier, kind, item, key="correction-key", text="corrected")

    assert result == {
        "key": "original-key", "state": "PENDING", "duplicate": True, "changed": True,
    }
    assert persisted_row(notifier) == before
    assert notifier.get_entry("original-key")["message"] == {"type": "text", "text": "original"}
    with pytest.raises(KeyError):
        notifier.get_entry("correction-key")
