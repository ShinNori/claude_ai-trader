"""The three STOP/RESUME paths share one future-time boundary."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "ops")]

from aitrader_ops import notify  # noqa: E402
from aitrader_ops.ledger import Ledger  # noqa: E402
from aitrader_ops.stop_status import inspect_stop_status  # noqa: E402

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 12, 7, 0, tzinfo=JST)
USER = "U-control-contract"
SETTINGS = {
    "broker": {"link_template": "https://www.rakuten-sec.co.jp/dummy?code={code}"},
    "line": {"allowed_user_id": USER, "monthly_budget": 200},
}


@pytest.fixture
def notifier(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.init_snapshot(1_000_000, [], [], NOW - timedelta(days=1))
    box = notify.Notifier(
        ledger=ledger,
        state_path=tmp_path / "notification.sqlite",
        settings=SETTINGS,
        transport="stub",
    )
    try:
        yield box
    finally:
        box.close()
        ledger.close()


def control_snapshot(box):
    """Capture the durable state a rejected control operation must not alter."""
    controls = box._con.execute(
        "SELECT action,at,event_id,reconciled_at,event_at FROM controls ORDER BY seq"
    ).fetchall()
    kv = box._con.execute(
        "SELECT name,value FROM kv WHERE name LIKE 'stopped%' ORDER BY name"
    ).fetchall()
    return controls, kv, box.is_stopped()


def signed_control(action, event_id, at):
    event = {
        "type": "message",
        "webhookEventId": event_id,
        "timestamp": int(at.timestamp() * 1000),
        "source": {"type": "user", "userId": USER},
        "message": {"type": "text", "text": action},
    }
    body = json.dumps({"events": [event]}).encode()
    signature = base64.b64encode(
        hmac.new(b"contract-secret", body, hashlib.sha256).digest()
    ).decode()
    return body, signature


@pytest.mark.parametrize("action", ["STOP", "RESUME"])
def test_direct_api_rejects_future_event_without_control_mutation(notifier, action):
    notifier.stop(now=NOW, event_at=NOW)
    before = control_snapshot(notifier)
    kwargs = {"now": NOW + timedelta(minutes=1), "event_at": NOW + timedelta(minutes=2)}
    if action == "RESUME":
        kwargs["reconciled_at"] = NOW + timedelta(seconds=30)
    assert getattr(notifier, action.lower())(**kwargs) == "INVALID"
    assert control_snapshot(notifier) == before


@pytest.mark.parametrize("action", ["STOP", "RESUME"])
def test_webhook_rejects_future_event_without_control_mutation(
        notifier, monkeypatch, action):
    notifier.stop(now=NOW, event_at=NOW)
    before = control_snapshot(notifier)
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "contract-secret")
    body, signature = signed_control(action, "future-" + action, NOW + timedelta(milliseconds=1))
    result = notify.handle_webhook(
        notifier=notifier,
        body=body,
        signature=signature,
        now=NOW,
        reconciled_at=NOW,
    )
    assert result["results"][0]["status"] == "INVALID"
    assert control_snapshot(notifier) == before


def test_stop_status_future_history_is_unknown_and_read_only(notifier):
    notifier.stop(now=NOW, event_at=NOW)
    path = Path(notifier.path)
    notifier.close()
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "UPDATE controls SET event_at=? WHERE seq=1",
            [(NOW + timedelta(microseconds=1)).isoformat()],
        )
        db.commit()
    before = path.read_bytes()
    result = inspect_stop_status(path, now=NOW)
    assert result["status"] == "UNKNOWN"
    assert result["effective_stop"] is True
    assert path.read_bytes() == before


def test_direct_api_accepts_equal_instant_across_timezones_and_keeps_late_rule(notifier):
    assert notifier.stop(now=NOW, event_at=NOW.astimezone(timezone.utc)) == "STOPPED"
    later = NOW + timedelta(minutes=2)
    assert notifier.resume(
        now=later,
        reconciled_at=NOW + timedelta(minutes=1),
        event_at=later.astimezone(timezone.utc),
    ) == "RESUMED"
    assert notifier.stop(
        now=later + timedelta(minutes=1), event_at=NOW + timedelta(minutes=1)
    ) == "IGNORED"
    assert notifier.is_stopped() is False
    assert notifier._con.execute(
        "SELECT action FROM controls ORDER BY seq DESC LIMIT 1"
    ).fetchone()[0] == "STOP_LATE"


def test_webhook_accepts_equal_boundary_and_late_event(monkeypatch, notifier):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "contract-secret")
    body, signature = signed_control("STOP", "equal", NOW.astimezone(timezone.utc))
    equal = notify.handle_webhook(
        notifier=notifier, body=body, signature=signature, now=NOW
    )
    assert equal["results"][0]["status"] == "STOPPED"

    resumed_at = NOW + timedelta(minutes=2)
    body, signature = signed_control("RESUME", "resume", resumed_at)
    resumed = notify.handle_webhook(
        notifier=notifier,
        body=body,
        signature=signature,
        now=resumed_at,
        reconciled_at=NOW + timedelta(minutes=1),
    )
    assert resumed["results"][0]["status"] == "RESUMED"

    body, signature = signed_control("STOP", "late", NOW + timedelta(minutes=1))
    late = notify.handle_webhook(
        notifier=notifier,
        body=body,
        signature=signature,
        now=NOW + timedelta(minutes=3),
    )
    assert late["results"][0]["status"] == "IGNORED"


def test_stop_status_accepts_equal_and_offset_equivalent_history(notifier):
    assert notifier.stop(now=NOW, event_at=NOW.astimezone(timezone.utc)) == "STOPPED"
    path = Path(notifier.path)
    notifier.close()
    result = inspect_stop_status(path, now=NOW)
    assert result["status"] == "STOPPED"
    assert result["effective_stop"] is True
