"""Raw parent paths are guarded by the direct daily receipt APIs."""
from pathlib import Path

import pytest

import aitrader.daily_receipt as receipt
from aitrader.runner import RunError
from test_daily_receipt_boundaries import running_home, sealed_history


def test_ordinary_parent_direct_inspect_and_seal_remain_supported(tmp_path):
    sealed, summary, _ = sealed_history(tmp_path)
    result = receipt.inspect_daily_rehearsal(sealed)
    assert result["status"] == "VERIFIED_HISTORY"
    assert result["saved_summary"] == summary

    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    running = running_home(ordinary)
    sealed_receipt = receipt.seal_daily_rehearsal(
        running, expected_progress_seq=4
    )
    assert sealed_receipt["expected_completed_checkpoint_seq"] == 4


@pytest.mark.parametrize("operation", ["inspect", "seal"])
def test_parent_reparse_is_rejected_before_any_child_file_read(
    tmp_path, monkeypatch, operation
):
    base = tmp_path / "parent"
    base.mkdir()
    home = sealed_history(base)[0] if operation == "inspect" else running_home(base)
    parent = home.parent
    original_link = receipt._is_link
    original_open = Path.open
    opened = []

    monkeypatch.setattr(
        receipt, "_is_link",
        lambda path: path == parent or original_link(path),
    )

    def monitor(path, *args, **kwargs):
        if path.is_relative_to(home):
            opened.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", monitor)
    if operation == "inspect":
        result = receipt.inspect_daily_rehearsal(home)
        assert result["status"] == "NEEDS_RECONCILIATION"
        assert "saved_summary" not in result
    else:
        with pytest.raises(RunError, match="親フォルダを安全"):
            receipt.seal_daily_rehearsal(home, expected_progress_seq=4)
        assert not (home / "daily_receipt.json").exists()
    assert opened == []


def test_parent_becoming_unsafe_at_final_snapshot_prevents_verified_history(
    tmp_path, monkeypatch
):
    home, _, _ = sealed_history(tmp_path)
    parent = home.parent
    original_snapshot = receipt._snapshot
    original_link = receipt._is_link
    unsafe = False
    calls = 0

    def dynamic_link(path):
        return (unsafe and path == parent) or original_link(path)

    def change_before_second_snapshot(target):
        nonlocal calls, unsafe
        calls += 1
        if calls == 2:
            unsafe = True
        return original_snapshot(target)

    monkeypatch.setattr(receipt, "_is_link", dynamic_link)
    monkeypatch.setattr(receipt, "_snapshot", change_before_second_snapshot)
    result = receipt.inspect_daily_rehearsal(home)
    assert calls == 2
    assert result["status"] == "NEEDS_RECONCILIATION"
    assert result["reason"] == "UNREADABLE_OR_UNSAFE_HISTORY"
    assert "saved_summary" not in result
