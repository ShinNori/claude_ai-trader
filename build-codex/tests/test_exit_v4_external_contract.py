"""Reproduce the v4 contract conflict; this is not an EXIT implementation."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3

from aitrader_ops.ledger import Ledger


def test_snapshot_external_notice_has_no_runner_outbox(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=timezone(timedelta(hours=9)))
    orders = [dict(proposal_id="external-sell", code="7203", side="SELL",
                   qty=100, limit_price=2000, lot_size=100)]
    with closing(Ledger(tmp_path / "ledger.sqlite")) as ledger:
        ledger.init_snapshot(1_000_000,
                             [dict(code="7203", qty=200, avg_price=1000)],
                             orders, at)
        path = tmp_path / "orchestration.sqlite"
        with closing(sqlite3.connect(path)) as journal:
            journal.executescript("""
                CREATE TABLE candidates(pid TEXT PRIMARY KEY, hash TEXT,
                    day TEXT, side TEXT, state TEXT, result TEXT, owner TEXT);
                CREATE TABLE outbox(key TEXT PRIMARY KEY, body TEXT);
            """)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro" , uri=True)) as journal:
            candidate_ids = {r[0] for r in journal.execute("SELECT pid FROM candidates")}
            candidate_ids.update(o["proposal_id"] for o in orders)
            assert journal.execute("SELECT count(*) FROM candidates WHERE state='INTENT'").fetchone()[0] == 0
            assert journal.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
            assert set(ledger.unconfirmed(at)) <= candidate_ids
            assert ledger.proposal("external-sell")["side"] == "SELL"
            assert ledger.notice("external-sell")["notice_state"] == "EXTERNAL"
            # v4 checks ALL candidate_ids for proposal present / outbox absent.
            # This healthy initial order therefore triggers RUN_INCOMPLETE,
            # before case 41 can yield OPEN_SELL_EXISTS.
            assert "external-sell" in candidate_ids
