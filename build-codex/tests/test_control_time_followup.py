"""Follow-up probes for persisted control chronology."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "ops")]

from aitrader_ops.ledger import Ledger  # noqa: E402
from aitrader_ops.notify import Notifier  # noqa: E402
from aitrader_ops.stop_status import inspect_stop_status  # noqa: E402

JST = timezone(timedelta(hours=9))
START = datetime(2026, 9, 12, 8, 30, tzinfo=JST)
SETTINGS = {
    "broker": {"link_template": "https://www.rakuten-sec.co.jp/dummy?code={code}"},
    "line": {"allowed_user_id": "U-followup", "monthly_budget": 200},
}


@pytest.fixture
def box(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.init_snapshot(1_000_000, [], [], START - timedelta(days=1))
    notifier = Notifier(
        ledger=ledger,
        state_path=tmp_path / "notification.sqlite",
        settings=SETTINGS,
        transport="stub",
    )
    try:
        yield notifier
    finally:
        notifier.close()
        ledger.close()


def snapshot(box):
    return (
        box._con.execute("SELECT * FROM controls ORDER BY seq").fetchall(),
        box._con.execute(
            "SELECT name,value FROM kv WHERE name LIKE 'stopped%' ORDER BY name"
        ).fetchall(),
        box.is_stopped(),
    )


@pytest.mark.parametrize("latest_action", ["STOP", "RESUME"])
def test_mixed_offset_latest_control_uses_instant_not_iso_text_order(
        box, latest_action):
    """A late event must stay late when the newest persisted time uses UTC text."""
    assert box.stop(now=START, event_at=START) == "STOPPED"
    resumed_at = START + timedelta(minutes=30)
    if latest_action == "STOP":
        first_resume = START + timedelta(minutes=10)
        assert box.resume(
            now=first_resume,
            reconciled_at=START + timedelta(minutes=5),
            event_at=first_resume,
        ) == "RESUMED"
        assert box.stop(now=resumed_at, event_at=resumed_at) == "STOPPED"
    else:
        assert box.resume(
            now=resumed_at,
            reconciled_at=START + timedelta(minutes=15),
            event_at=resumed_at,
        ) == "RESUMED"

    # This is the same instant, expressed in UTC. Such offset-equivalent persisted
    # history is accepted by the public read-only inspector.
    box._con.execute(
        "UPDATE controls SET event_at=? WHERE seq=(SELECT MAX(seq) FROM controls)",
        [resumed_at.astimezone(timezone.utc).isoformat()],
    )
    box._con.commit()
    state_path = Path(box.path)
    ledger = box.ledger
    box.close()
    expected_status = "STOPPED" if latest_action == "STOP" else "CLEAR"
    assert inspect_stop_status(state_path, now=resumed_at)["status"] == expected_status

    reopened = Notifier(
        ledger=ledger,
        state_path=state_path,
        settings=SETTINGS,
        transport="stub",
    )

    late_at = START + timedelta(minutes=20)
    try:
        if latest_action == "STOP":
            result = reopened.resume(
                now=resumed_at + timedelta(minutes=1),
                reconciled_at=resumed_at,
                event_at=late_at,
            )
            expected_late_action = "RESUME_LATE"
            expected_stopped = True
        else:
            result = reopened.stop(
                now=resumed_at + timedelta(minutes=1), event_at=late_at
            )
            expected_late_action = "STOP_LATE"
            expected_stopped = False
        assert result == "IGNORED"
        assert reopened.is_stopped() is expected_stopped
        assert reopened._con.execute(
            "SELECT action FROM controls ORDER BY seq DESC LIMIT 1"
        ).fetchone()[0] == expected_late_action
    finally:
        reopened.close()


@pytest.mark.parametrize("action", ["STOP", "RESUME"])
def test_naive_direct_control_time_is_rejected_without_mutation(box, action):
    box.stop(now=START, event_at=START)
    before = snapshot(box)
    kwargs = {
        "now": START,
        "event_at": START.replace(tzinfo=None),
    }
    if action == "RESUME":
        kwargs["reconciled_at"] = START
    with pytest.raises(ValueError, match="タイムゾーン"):
        getattr(box, action.lower())(**kwargs)
    assert snapshot(box) == before
