"""Persisted notification bodies remain bound to freshly rendered inputs."""
import copy
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from aitrader.notification_plan import prepare_notifications
from aitrader.notification_queue import enqueue_prepared_notifications
from aitrader.runner import RunError, encoded
from test_notification_plan import CONTEXTS, snapshot
from test_notification_queue import CONFIG
from test_runner import DAY, NOW, setup


DEEP_JSON = "[" * 5000 + "0" + "]" * 5000


def _prepare(setup):
    home, execute = setup
    execute()
    plan = prepare_notifications(
        home, "run1", DAY, contexts=copy.deepcopy(CONTEXTS),
        settings=copy.deepcopy(CONFIG),
    )
    return home, plan


def _call_prepare(home):
    return prepare_notifications(
        home, "run1", DAY, contexts=copy.deepcopy(CONTEXTS),
        settings=copy.deepcopy(CONFIG),
    )


def _files(home):
    return {
        path.relative_to(home).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
        )
        for path in home.rglob("*") if path.is_file()
    }


def _set_plan_body(home, body):
    with closing(sqlite3.connect(home / "notification-plans.sqlite")) as db:
        with db:
            db.execute("UPDATE plans SET body=? WHERE run_id='run1'", [body])


def _mutated(plan, field):
    changed = copy.deepcopy(plan)
    if field == "meta":
        changed["delivery"] = "TAMPERED"
    elif field == "key":
        changed["plans"][0]["key"] += ":tampered"
    else:
        changed["plans"][0]["message"]["altText"] = "TAMPERED-SAVED-BODY"
    return changed


def _assert_rejected_without_change(home, action):
    before_ledger = snapshot(home)
    before_files = _files(home)
    with pytest.raises((RunError, ValueError)):
        action()
    assert snapshot(home) == before_ledger
    assert _files(home) == before_files


@pytest.mark.parametrize("field", ["meta", "key", "message"])
def test_changed_saved_body_is_rejected_before_effects(setup, field):
    home, plan = _prepare(setup)
    _set_plan_body(home, encoded(_mutated(plan, field)))

    _assert_rejected_without_change(home, lambda: _call_prepare(home))


@pytest.mark.parametrize("bad_body", ["{malformed", DEEP_JSON])
def test_unreadable_saved_body_is_rejected_before_effects(setup, bad_body):
    home, _ = _prepare(setup)
    _set_plan_body(home, bad_body)

    _assert_rejected_without_change(home, lambda: _call_prepare(home))


def test_changed_body_is_not_published_when_plan_json_is_missing(setup):
    home, plan = _prepare(setup)
    _set_plan_body(home, encoded(_mutated(plan, "message")))
    output = home / "runs" / DAY.isoformat() / "run1" / "notification_plan.json"
    output.unlink()

    _assert_rejected_without_change(home, lambda: _call_prepare(home))
    assert not output.exists()


def test_matching_changed_json_and_body_cannot_reach_enqueue(setup):
    home, plan = _prepare(setup)
    changed = _mutated(plan, "message")
    _set_plan_body(home, encoded(changed))
    output = home / "runs" / DAY.isoformat() / "run1" / "notification_plan.json"
    output.write_text(encoded(changed), encoding="utf-8")

    _assert_rejected_without_change(
        home,
        lambda: enqueue_prepared_notifications(
            home, "run1", DAY, now=NOW, contexts=copy.deepcopy(CONTEXTS),
            settings=copy.deepcopy(CONFIG),
        ),
    )
    assert not (home / "notification.sqlite").exists()


def test_matching_saved_body_is_reused_without_rewrite(setup):
    home, plan = _prepare(setup)
    before_ledger = snapshot(home)
    before_files = _files(home)

    assert _call_prepare(home) == plan

    assert snapshot(home) == before_ledger
    assert _files(home) == before_files


def test_missing_plan_json_is_restored_from_verified_render(setup):
    home, plan = _prepare(setup)
    output = home / "runs" / DAY.isoformat() / "run1" / "notification_plan.json"
    output.unlink()
    before_ledger = snapshot(home)
    binding_before = (home / "notification-plans.sqlite").read_bytes()

    assert _call_prepare(home) == plan

    assert json.loads(output.read_text(encoding="utf-8")) == plan
    assert snapshot(home) == before_ledger
    assert (home / "notification-plans.sqlite").read_bytes() == binding_before


@pytest.mark.parametrize("table", ["plans", "cards"])
def test_existing_hash_checks_still_reject_corruption(setup, table):
    home, _ = _prepare(setup)
    with closing(sqlite3.connect(home / "notification-plans.sqlite")) as db:
        with db:
            db.execute(f"UPDATE {table} SET hash='bad'")

    _assert_rejected_without_change(home, lambda: _call_prepare(home))
