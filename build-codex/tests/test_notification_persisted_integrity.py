"""Main notification flows reject a corrupted persisted queue before effects."""
import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from aitrader.mock_delivery import deliver_prepared_mock, reconcile_prepared_mock
from aitrader.notification_plan import prepare_notifications
from aitrader.notification_queue import enqueue_prepared_notifications
from aitrader.runner import RunError
from aitrader_ops.ledger import Ledger
from test_managed_delivery import _managed_run
from test_notification_plan import CONTEXTS
from test_notification_queue import CONFIG
from test_runner import DAY, NOW, setup


def _files(home):
    return {str(p.relative_to(home)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                       p.stat().st_mtime_ns)
            for p in home.rglob("*") if p.is_file()}


def _ledger(home):
    with closing(Ledger(home / "ledger.sqlite")) as ledger:
        return ledger.seq(), ledger.reserved(), ledger.notice("buy1")


def _enqueue(home, now):
    return enqueue_prepared_notifications(home, "run1", DAY, now=now,
        contexts=copy.deepcopy(CONTEXTS), settings=copy.deepcopy(CONFIG))


def _prepare_legacy(setup):
    home, execute = setup
    execute()
    prepare_notifications(home, "run1", DAY, contexts=copy.deepcopy(CONTEXTS),
                          settings=copy.deepcopy(CONFIG))
    _enqueue(home, NOW)
    return home


def _prepare_managed(tmp_path):
    home = _managed_run(tmp_path)
    _enqueue(home, NOW)
    return home


def _corrupt_hash(home):
    with closing(sqlite3.connect(home / "notification.sqlite")) as con:
        with con:
            con.execute("UPDATE outbox SET content_hash=?", ["0" * 64])


@pytest.mark.parametrize("mode", ["legacy", "managed"])
@pytest.mark.parametrize("operation", ["enqueue", "deliver", "reconcile"])
def test_corrupt_stored_hash_blocks_main_flow_before_any_state_change(
    tmp_path, setup, mode, operation
):
    home = _prepare_legacy(setup) if mode == "legacy" else _prepare_managed(tmp_path)
    _corrupt_hash(home)
    before_files = _files(home)
    before_ledger = _ledger(home)
    later = NOW + timedelta(minutes=1)

    with pytest.raises((RunError, ValueError)):
        if operation == "enqueue":
            _enqueue(home, later)
        elif operation == "deliver":
            deliver_prepared_mock(
                home, "run1", DAY, now=later,
                contexts=copy.deepcopy(CONTEXTS), settings=copy.deepcopy(CONFIG),
                stub_results=["success"],
            )
        else:
            reconcile_prepared_mock(
                home, "run1", DAY, now=later,
                contexts=copy.deepcopy(CONTEXTS), settings=copy.deepcopy(CONFIG),
            )

    assert _ledger(home) == before_ledger
    assert _files(home) == before_files


def test_matching_persisted_plan_is_not_rewritten(setup):
    home = _prepare_legacy(setup)
    plan = home / "runs" / DAY.isoformat() / "run1" / "notification_plan.json"
    before = (plan.read_bytes(), plan.stat().st_mtime_ns)

    prepare_notifications(home, "run1", DAY, contexts=copy.deepcopy(CONTEXTS),
                          settings=copy.deepcopy(CONFIG))

    assert (plan.read_bytes(), plan.stat().st_mtime_ns) == before


def test_missing_persisted_plan_is_recreated_from_bound_state(setup):
    home = _prepare_legacy(setup)
    plan = home / "runs" / DAY.isoformat() / "run1" / "notification_plan.json"
    expected = json.loads(plan.read_text(encoding="utf-8"))
    plan.unlink()

    actual = prepare_notifications(
        home, "run1", DAY, contexts=copy.deepcopy(CONTEXTS),
        settings=copy.deepcopy(CONFIG),
    )

    assert actual == expected
    assert json.loads(plan.read_text(encoding="utf-8")) == expected
