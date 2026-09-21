"""Persisted lifecycle fields are checked before the first queue effect."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from aitrader_ops.models import JST
from aitrader_ops.notify import Notifier


NOW = datetime(2026, 9, 11, 7, 0, tzinfo=JST)
SETTINGS = {"line": {"allowed_user_id": "fixture-user", "monthly_budget": 10}}


class _UnusedLedger:
    pass


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("attempts", "not-json"),
        ("attempts", "{}"),
        ("attempts", '{"x":1}'),
        ("attempts", "[1]"),
        ("attempts", "null"),
        ("attempts", json.dumps([{"at": "invalid"}])),
        ("attempts", json.dumps([{"at": "2026-09-11T07:00:00"}])),
        ("created_at", "invalid"),
        ("created_at", "2026-09-11T07:00:00"),
        ("expires_at", "invalid"),
        ("claimed_at", "2026-09-11T07:00:00"),
    ],
)
def test_later_invalid_lifecycle_row_blocks_first_row(tmp_path, column, value):
    notifier = Notifier(
        ledger=_UnusedLedger(), state_path=tmp_path / "notification.sqlite",
        settings=SETTINGS, transport="stub",
    )
    try:
        for key in ("first", "second"):
            notifier.enqueue(key=key, kind="RISK",
                             message={"type": "text", "text": key},
                             proposal_id=None, now=NOW)
        notifier._con.execute(
            f"UPDATE outbox SET {column}=? WHERE key='second'", [value]
        )
        query = ("SELECT key,state,attempts,month,claimed_by,claimed_at "
                 "FROM outbox ORDER BY seq")
        before = notifier._con.execute(query).fetchall()
        notifier.stub_results = ["success", "success"]

        with pytest.raises(ValueError, match="保存済み通知キュー"):
            notifier.flush(now=NOW, keys=["first", "second"])

        assert notifier._con.execute(query).fetchall() == before
        assert notifier._con.execute("SELECT count(*) FROM kv").fetchone()[0] == 0
        assert notifier.stub_results == ["success", "success"]
    finally:
        notifier.close()
