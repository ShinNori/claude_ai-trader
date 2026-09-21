"""Persisted notification history cannot be operated from an earlier time."""
import json
from dataclasses import replace
from datetime import timedelta, timezone

import pytest

from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier
from test_v041_order4_review_claude import NOW, SETTINGS, proposal


@pytest.fixture
def queue(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.init_snapshot(1_000_000, [], [], NOW.replace(hour=6))
    notifier = Notifier(ledger=ledger, state_path=tmp_path / "notify.sqlite",
                        settings=SETTINGS, transport="stub")
    yield ledger, notifier
    notifier.close()
    ledger.close()


def _add(ledger, notifier, key, pid):
    item = replace(proposal(), proposal_id=pid)
    ledger.create_notice(item, at=NOW - timedelta(minutes=2))
    ledger.set_notice_state(pid, "APPROVED", NOW - timedelta(minutes=2))
    notifier.enqueue(key=key, kind="NEW", message={"type": "text", "text": key},
                     proposal_id=pid, now=NOW - timedelta(minutes=2))
    return item


def _snapshot(ledger, notifier):
    return (
        ledger.seq(),
        notifier._con.execute("SELECT * FROM outbox ORDER BY seq").fetchall(),
        notifier._con.execute("SELECT * FROM kv ORDER BY name").fetchall(),
        notifier._con.execute("SELECT * FROM controls ORDER BY seq").fetchall(),
    )


def _make_sent(notifier, key):
    notifier._con.execute(
        "UPDATE outbox SET state='SENT',month='2026-09' WHERE key=?", [key]
    )


def _call(notifier, method, now, keys=None):
    if method == "flush":
        return notifier.flush(now=now, keys=keys)
    return notifier.reconcile_sent(now=now, keys=keys)


@pytest.mark.parametrize("method", ["flush", "reconcile"])
@pytest.mark.parametrize("field", ["created_at", "updated_at", "attempt_at"])
def test_each_future_history_field_rejects_before_effects(queue, method, field):
    ledger, notifier = queue
    _add(ledger, notifier, "candidate", "candidate-1")
    operation_at = NOW - timedelta(minutes=1)
    notifier._con.execute(
        "UPDATE outbox SET created_at=?,updated_at=? WHERE key='candidate'",
        [operation_at.isoformat(), operation_at.isoformat()],
    )
    if field in ("created_at", "updated_at"):
        notifier._con.execute(
            f"UPDATE outbox SET {field}=? WHERE key='candidate'", [NOW.isoformat()]
        )
    else:
        retry_key = notifier._con.execute(
            "SELECT retry_key FROM outbox WHERE key='candidate'"
        ).fetchone()[0]
        attempts = [{"at": NOW.isoformat(), "result": "failure",
                     "retry_key": retry_key}]
        notifier._con.execute("UPDATE outbox SET attempts=? WHERE key='candidate'",
                              [json.dumps(attempts)])
    if method == "reconcile":
        _make_sent(notifier, "candidate")
    before = _snapshot(ledger, notifier)
    notifier.stub_results = ["success"]

    with pytest.raises(ValueError, match="通知履歴より前"):
        _call(notifier, method, operation_at, ["candidate"])

    assert _snapshot(ledger, notifier) == before
    assert notifier.stub_results == ["success"]


@pytest.mark.parametrize("method", ["flush", "reconcile"])
@pytest.mark.parametrize("stored_as_utc", [False, True])
def test_equal_instant_is_allowed_in_jst_or_utc(queue, method, stored_as_utc):
    ledger, notifier = queue
    _add(ledger, notifier, "candidate", "candidate-1")
    text = (NOW.astimezone(timezone.utc) if stored_as_utc else NOW).isoformat()
    retry_key = notifier._con.execute(
        "SELECT retry_key FROM outbox WHERE key='candidate'"
    ).fetchone()[0]
    notifier._con.execute(
        "UPDATE outbox SET created_at=?,updated_at=?,attempts=? WHERE key='candidate'",
        [text, text, json.dumps([{"at": text, "result": "failure",
                                 "retry_key": retry_key}])],
    )
    if method == "reconcile":
        _make_sent(notifier, "candidate")
    else:
        notifier.stub_results = ["success"]

    result = _call(notifier, method, NOW, ["candidate"])

    assert result
    assert ledger.notice("candidate-1")["notice_state"] == "SENT"


@pytest.mark.parametrize("method", ["flush", "reconcile"])
def test_future_later_row_blocks_first_row_before_any_effect(queue, method):
    ledger, notifier = queue
    _add(ledger, notifier, "first", "candidate-1")
    _add(ledger, notifier, "second", "candidate-2")
    notifier._con.execute("UPDATE outbox SET updated_at=? WHERE key='second'",
                          [(NOW + timedelta(minutes=1)).isoformat()])
    if method == "reconcile":
        _make_sent(notifier, "first")
        _make_sent(notifier, "second")
    before = _snapshot(ledger, notifier)
    notifier.stub_results = ["success", "success"]

    with pytest.raises(ValueError, match="通知履歴より前"):
        _call(notifier, method, NOW, None)

    assert _snapshot(ledger, notifier) == before
    assert notifier.stub_results == ["success", "success"]
    assert ledger.notice("candidate-1")["notice_state"] == "APPROVED"


@pytest.mark.parametrize("method", ["flush", "reconcile"])
def test_empty_keys_remain_a_noop_even_with_future_unselected_row(queue, method):
    ledger, notifier = queue
    _add(ledger, notifier, "candidate", "candidate-1")
    notifier._con.execute("UPDATE outbox SET updated_at=?",
                          [(NOW + timedelta(minutes=1)).isoformat()])
    if method == "reconcile":
        _make_sent(notifier, "candidate")
    before = _snapshot(ledger, notifier)

    assert _call(notifier, method, NOW, []) == []
    assert _snapshot(ledger, notifier) == before


@pytest.mark.parametrize("method", ["flush", "reconcile"])
def test_unknown_explicit_key_rejects_without_touching_known_row(queue, method):
    ledger, notifier = queue
    _add(ledger, notifier, "candidate", "candidate-1")
    if method == "reconcile":
        _make_sent(notifier, "candidate")
    before = _snapshot(ledger, notifier)

    with pytest.raises(KeyError):
        _call(notifier, method, NOW, ["outside"])

    assert _snapshot(ledger, notifier) == before


def test_normal_failure_retry_after_time_advances_preserves_ledger_rules(queue):
    ledger, notifier = queue
    _add(ledger, notifier, "candidate", "candidate-1")
    notifier.stub_results = ["failure", "success"]

    assert notifier.flush(now=NOW, keys=["candidate"])[0]["state"] == "PENDING"
    assert ledger.notice("candidate-1")["notice_state"] == "APPROVED"
    assert notifier.flush(now=NOW + timedelta(minutes=1),
                          keys=["candidate"])[0]["state"] == "SENT"
    assert ledger.notice("candidate-1")["notice_state"] == "SENT"
