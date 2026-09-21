"""Notification clock rollback failures never mask the primary failure."""
import copy
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from aitrader import notification_plan
from aitrader.notification_plan import prepare_notifications
from test_notification_plan import CONTEXTS
from test_notification_queue import CONFIG
from test_runner import DAY, NOW, setup


class OrdinaryFailure(RuntimeError):
    pass


class InterruptFailure(BaseException):
    pass


class RollbackFailure(RuntimeError):
    pass


class ConnectionWrapper:
    def __init__(self, inner, primary, failure_point):
        self.inner = inner
        self.primary = primary
        self.failure_point = failure_point
        self.closed = False
        self.rollback_attempted = False

    def execute(self, sql, params=None):
        if self.failure_point == "insert" and sql.startswith(
                "INSERT INTO notification_clock"):
            raise self.primary
        if self.failure_point == "commit" and sql == "COMMIT":
            raise self.primary
        result = self.inner.execute(sql) if params is None else self.inner.execute(sql, params)
        if sql == "ROLLBACK":
            self.rollback_attempted = True
            raise RollbackFailure("secondary rollback failure")
        return result

    def close(self):
        self.closed = True
        self.inner.close()


def _clock_state(path):
    with closing(sqlite3.connect(path)) as database:
        exists = database.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='notification_clock'"
        ).fetchone()
        rows = (database.execute(
            "SELECT run_id,observed_at FROM notification_clock ORDER BY run_id"
        ).fetchall() if exists else [])
    return bool(exists), rows


@pytest.mark.parametrize("primary_type", [OrdinaryFailure, InterruptFailure])
@pytest.mark.parametrize("failure_point", ["insert", "commit"])
@pytest.mark.parametrize("existing_clock", [False, True])
def test_clock_rollback_failure_preserves_primary_and_closes_connection(
        setup, monkeypatch, primary_type, failure_point, existing_clock):
    home, execute = setup
    execute()
    prepare_notifications(
        home, "run1", DAY, contexts=copy.deepcopy(CONTEXTS),
        settings=copy.deepcopy(CONFIG),
    )
    binding = home / "notification-plans.sqlite"
    if existing_clock:
        notification_plan._record_notification_time(home, "run1", DAY, NOW)
    before = _clock_state(binding)
    primary = primary_type("primary clock failure")
    original_connect = notification_plan.sqlite3.connect
    wrappers = []

    def connect(database, *args, **kwargs):
        inner = original_connect(database, *args, **kwargs)
        if isinstance(database, str) and "mode=rw" in database:
            wrapper = ConnectionWrapper(inner, primary, failure_point)
            wrappers.append(wrapper)
            return wrapper
        return inner

    monkeypatch.setattr(notification_plan.sqlite3, "connect", connect)

    with pytest.raises(primary_type) as raised:
        notification_plan._record_notification_time(
            home, "run1", DAY,
            NOW + timedelta(minutes=1) if existing_clock else NOW,
        )

    assert raised.value is primary
    assert len(wrappers) == 1
    assert wrappers[0].rollback_attempted is True
    assert wrappers[0].closed is True
    assert _clock_state(binding) == before
