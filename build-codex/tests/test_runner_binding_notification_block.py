"""Downstream notification must retain the completed run's candidate binding."""
import copy
import hashlib
import sqlite3
from contextlib import closing

import pytest

from aitrader.notification_plan import prepare_notifications
from aitrader.notification_queue import enqueue_prepared_notifications
from aitrader.runner import RunError
from aitrader_ops.ledger import Ledger
from test_notification_plan import CONTEXTS, SETTINGS
from test_notification_queue import CONFIG
from test_runner import DAY, NOW, setup


def _files(home):
    return {str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in home.rglob("*") if path.is_file()}


def _ledger(home):
    with closing(Ledger(home / "ledger.sqlite")) as ledger:
        return ledger.seq(), ledger.reserved(), ledger.notice("buy1")


def _prepare(home):
    return prepare_notifications(
        home, "run1", DAY, contexts=copy.deepcopy(CONTEXTS),
        settings=copy.deepcopy(CONFIG),
    )


def _enqueue(home):
    return enqueue_prepared_notifications(
        home, "run1", DAY, now=NOW, contexts=copy.deepcopy(CONTEXTS),
        settings=copy.deepcopy(CONFIG),
    )


def _corrupt(home, field):
    values = {"owner": "other-run", "day": "2026-09-09",
              "side": "SELL", "hash": "0" * 64}
    with sqlite3.connect(home / "orchestration.sqlite") as con:
        con.execute(f"UPDATE candidates SET {field}=? WHERE pid='buy1'", [values[field]])
        con.commit()


def _damage_candidate_record(home, damage):
    statements = {
        "missing": "DELETE FROM candidates WHERE pid='buy1'",
        "state": "UPDATE candidates SET state='INTENT' WHERE pid='buy1'",
        "result": "UPDATE candidates SET result='{}' WHERE pid='buy1'",
        "outbox": "DELETE FROM outbox WHERE key LIKE '%:buy1:%:CANDIDATE'",
    }
    with sqlite3.connect(home / "orchestration.sqlite") as con:
        con.execute(statements[damage])
        con.commit()


def test_normal_completed_history_prepares_and_enqueues_without_new_reservation(setup):
    home, execute = setup
    execute()
    before = _ledger(home)
    plan = _prepare(home)
    assert plan["plans"][0]["proposal_id"] == "buy1"
    queued = _enqueue(home)
    assert queued["sent_by_this_call"] is False
    assert _ledger(home) == before


@pytest.mark.parametrize("operation", ["prepare", "enqueue"])
@pytest.mark.parametrize("field", ["owner", "day", "side", "hash"])
def test_candidate_binding_conflict_blocks_notification_before_queue_or_reserve(
    setup, operation, field
):
    home, execute = setup
    execute()
    if operation == "enqueue":
        _prepare(home)
    _corrupt(home, field)
    before_ledger = _ledger(home)
    before_files = _files(home)

    with pytest.raises((RunError, ValueError)):
        _prepare(home) if operation == "prepare" else _enqueue(home)

    assert _ledger(home) == before_ledger
    assert not (home / "notification.sqlite").exists()
    assert _files(home) == before_files


@pytest.mark.parametrize("operation", ["prepare", "enqueue"])
@pytest.mark.parametrize("damage", ["missing", "state", "result", "outbox"])
def test_incomplete_candidate_record_cannot_become_notification_authority(
    setup, operation, damage
):
    home, execute = setup
    execute()
    if operation == "enqueue":
        _prepare(home)
    _damage_candidate_record(home, damage)
    before_ledger = _ledger(home)
    before_files = _files(home)
    with pytest.raises((RunError, ValueError)):
        _prepare(home) if operation == "prepare" else _enqueue(home)
    assert _ledger(home) == before_ledger
    assert not (home / "notification.sqlite").exists()
    assert _files(home) == before_files
