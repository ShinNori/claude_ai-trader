"""Deep JSON is an unreadable observation, never an escaping recursion error."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from aitrader.daily_receipt import inspect_daily_rehearsal
from aitrader.managed_stop import inspect_managed_stop
from aitrader.operations_status import build_operations_status
from aitrader.operations_view import render_operations_view
from test_operations_status import NOW, new_history


DEEP_JSON = "[" * 5000 + "0" + "]" * 5000


@pytest.fixture(scope="module")
def deep_history(new_history):
    return new_history("deep-json-base")


@pytest.fixture
def home(tmp_path, deep_history):
    target = tmp_path / "home"
    shutil.copytree(deep_history, target)
    marker_path = target / "mock-runner.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["stop_files"] = [
        str(target / path.relative_to(deep_history))
        for raw in marker["stop_files"]
        for path in [Path(raw)]
    ]
    marker_path.write_text(
        json.dumps(marker, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    _bind_file(target, "mock-runner.json")
    assert build_operations_status(target, now=NOW)["status"] == "CLEAR"
    assert inspect_daily_rehearsal(target)["status"] == "VERIFIED_HISTORY"
    assert inspect_managed_stop(target, now=NOW)["status"] == "CLEAR"
    return target


def _snapshot(folder):
    return {
        path.relative_to(folder).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
        )
        for path in folder.rglob("*") if path.is_file()
    }


def _bind_file(home, name):
    target = home / name
    receipt_path = home / "daily_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    entry = next(item for item in receipt["files"] if item["path"] == name)
    body = target.read_bytes()
    entry.update(size=len(body), sha256=hashlib.sha256(body).hexdigest())
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )


def _replace_and_bind(home, name):
    (home / name).write_text(DEEP_JSON, encoding="utf-8")
    _bind_file(home, name)


def _assert_unchanged(home, before):
    assert _snapshot(home) == before


@pytest.mark.parametrize("name", [
    "daily_progress.json", "mock-runner.json", "daily_receipt.json",
])
def test_operations_status_contains_deep_json_as_unknown(home, name):
    (home / name).write_text(DEEP_JSON, encoding="utf-8")
    before = _snapshot(home)

    result = build_operations_status(home, now=NOW)

    assert result["status"] in {"UNKNOWN", "NEEDS_RECONCILIATION"}
    assert result["read_only"] is True
    assert result["current_signal"] is False
    assert result["auto_resume"] is False
    assert "summary" not in result["history"]
    _assert_unchanged(home, before)


@pytest.mark.parametrize("name", ["mock-runner.json", "managed-stop-clock.json"])
def test_managed_inspection_contains_deep_json_as_unknown(home, name):
    (home / name).write_text(DEEP_JSON, encoding="utf-8")
    before = _snapshot(home)

    result = inspect_managed_stop(home, now=NOW)

    assert result["status"] == "UNKNOWN"
    assert result["known"] is False
    assert result["effective_stop"] is True
    assert result["read_only"] is True
    _assert_unchanged(home, before)


@pytest.mark.parametrize("name", ["daily_receipt.json", "daily_progress.json"])
def test_daily_receipt_top_level_deep_json_needs_reconciliation(home, name):
    (home / name).write_text(DEEP_JSON, encoding="utf-8")
    before = _snapshot(home)

    result = inspect_daily_rehearsal(home)

    assert result["status"] == "NEEDS_RECONCILIATION"
    assert result["auto_resume"] is False
    assert result["current_signal"] is False
    _assert_unchanged(home, before)


@pytest.mark.parametrize("name", [
    "daily_rehearsal.json", "mock-runner.json", "managed-stop-clock.json",
])
def test_daily_receipt_reachable_deep_artifact_needs_reconciliation(home, name):
    _replace_and_bind(home, name)
    before = _snapshot(home)

    result = inspect_daily_rehearsal(home)

    assert result["status"] == "NEEDS_RECONCILIATION"
    assert result["auto_resume"] is False
    assert result["current_signal"] is False
    assert "saved_summary" not in result
    _assert_unchanged(home, before)


def test_html_renders_deep_progress_as_non_authorizing_unknown(home):
    (home / "daily_progress.json").write_text(DEEP_JSON, encoding="utf-8")
    before = _snapshot(home)

    html = render_operations_view(home, now=NOW)

    assert "状態を確認できません" in html
    assert "現在の売買承認ではありません" in html
    _assert_unchanged(home, before)
