"""Deep saved JSON must fail closed without changing diagnostic inputs."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import date

from aitrader.runner_diagnostics import diagnose_mock_run


DAY = date(2026, 9, 8)


def _hashes(home):
    return {
        str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in home.rglob("*")
        if path.is_file()
    }


def _assert_unsafe_without_changes(home):
    before = _hashes(home)
    result = diagnose_mock_run(home, "run1", DAY)
    assert result["classification"] == "NEEDS_RECONCILIATION"
    assert result["reasons"] == ["OBSERVATION_UNSAFE"]
    assert result["candidates"] == []
    assert result["snapshot_consistent"] is False
    assert result["observation_unchanged"] is False
    assert result["automatic_resume_allowed"] is False
    assert result["repaired"] is False
    assert result["current_signal"] is False
    assert _hashes(home) == before


def test_deep_saved_manifest_is_conservative_and_read_only(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "mock-runner.json").write_text(
        '{"mode":"mock","version":1}', encoding="utf-8"
    )
    with sqlite3.connect(home / "orchestration.sqlite") as con:
        con.executescript(
            "CREATE TABLE runs(id, hash, manifest, result);"
            "CREATE TABLE candidates(pid, hash, day, side, state, result, owner);"
            "CREATE TABLE outbox(key, body);"
        )
        deep_manifest = "[" * 5000 + "0" + "]" * 5000
        con.execute(
            "INSERT INTO runs VALUES(?,?,?,?)",
            ("run1", "request-hash", deep_manifest, None),
        )
    with sqlite3.connect(home / "ledger.sqlite") as con:
        con.execute("CREATE TABLE ledger_events(seq, kind, payload)")

    _assert_unsafe_without_changes(home)


def test_deep_mock_marker_is_conservative_and_read_only(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    deep_marker = '{"nested":' + "[" * 5000 + "0" + "]" * 5000 + "}"
    (home / "mock-runner.json").write_text(deep_marker, encoding="utf-8")

    _assert_unsafe_without_changes(home)
