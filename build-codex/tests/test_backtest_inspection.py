"""Independent read-only tests for saved backtest observation."""
import hashlib
import os
from pathlib import Path

import pytest

import aitrader.backtest_inspection as inspection
from aitrader.backtest_inspection import inspect_backtest_results


FILES = {
    "trades.csv": b"date,code,side\n",
    "equity.csv": b"date,equity\n",
    "summary.json": b'{"status":"BACKTEST"}\n',
    "report.html": b"<html>saved</html>\n",
}


def _complete(folder):
    folder.mkdir()
    for name, content in FILES.items():
        (folder / name).write_bytes(content)


def _state(root):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
    }


def _assert_fixed_flags(result):
    assert result["read_only"] is True
    assert result["generation_verified"] is False
    assert result["ready_for_live"] is False
    assert result["automatic_resume_allowed"] is False
    assert result["observation_atomic"] is False


def test_complete_four_file_set_is_observed_without_mutation(tmp_path):
    folder = tmp_path / "result"
    _complete(folder)
    before = _state(tmp_path)

    result = inspect_backtest_results(folder)

    assert result["status"] == "OBSERVED"
    assert result["reason"] == "FOUR_ARTIFACTS_OBSERVED"
    assert result["observation_unchanged"] is True
    assert result["residues"] == {}
    assert set(result["artifacts"]) == set(FILES)
    for name, content in FILES.items():
        assert result["artifacts"][name] == {
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    _assert_fixed_flags(result)
    assert _state(tmp_path) == before


@pytest.mark.parametrize("missing", FILES)
def test_missing_required_artifact_is_incomplete(tmp_path, missing):
    folder = tmp_path / "result"
    _complete(folder)
    (folder / missing).unlink()
    before = _state(tmp_path)

    result = inspect_backtest_results(folder)

    assert result["status"] == "INCOMPLETE"
    assert result["reason"] == "REQUIRED_ARTIFACT_MISSING"
    assert result["artifacts"] == {}
    assert result["observation_unchanged"] is True
    assert _state(tmp_path) == before


def test_missing_folder_is_unknown_and_not_created(tmp_path):
    folder = tmp_path / "missing"
    result = inspect_backtest_results(folder)
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "RESULT_FOLDER_MISSING"
    assert result["artifacts"] == {}
    assert not folder.exists()


@pytest.mark.parametrize("kind,prefix", [
    ("lock", ".result.publish.lock"),
    ("stage", ".result.stage.left"),
    ("backup", ".result.backup.left"),
    ("export_stage", ".result.export-stage.left"),
    ("export_backup", ".result.export-backup.left"),
])
def test_publication_residue_requires_review_without_reading_artifacts(
    tmp_path, monkeypatch, kind, prefix
):
    folder = tmp_path / "result"
    _complete(folder)
    residue = tmp_path / prefix
    residue.write_bytes(b"lock") if kind == "lock" else residue.mkdir()
    original = Path.open

    def reject_artifact_read(path, *args, **kwargs):
        if path.parent == folder:
            raise AssertionError("locked artifacts must not be read")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_artifact_read)
    result = inspect_backtest_results(folder)
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["reason"] == "PUBLICATION_RESIDUE_PRESENT"
    assert result["artifacts"] == {}
    assert result["residues"][kind] == 1
    assert result["observation_unchanged"] is True


def test_artifact_change_between_snapshots_is_unknown(tmp_path, monkeypatch):
    folder = tmp_path / "result"
    _complete(folder)
    original = inspection._fingerprint
    calls = 0

    def change_after_first_snapshot(path):
        nonlocal calls
        value = original(path)
        calls += 1
        if calls == len(FILES):
            (folder / "trades.csv").write_bytes(b"changed same boundary")
        return value

    monkeypatch.setattr(inspection, "_fingerprint", change_after_first_snapshot)
    result = inspect_backtest_results(folder)
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "OBSERVATION_CHANGED"
    assert result["artifacts"] == {}


@pytest.mark.parametrize("unsafe_name", ["result", "summary.json"])
def test_reparse_injection_is_unknown_before_content_read(
    tmp_path, monkeypatch, unsafe_name
):
    folder = tmp_path / "result"
    _complete(folder)
    original_unsafe = inspection._unsafe
    original_open = Path.open

    def injected(info, path):
        return path.name == unsafe_name or original_unsafe(info, path)

    def monitor(path, *args, **kwargs):
        if path.name == unsafe_name:
            raise AssertionError("unsafe path content was opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(inspection, "_unsafe", injected)
    monkeypatch.setattr(Path, "open", monitor)
    result = inspect_backtest_results(folder)
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "UNSAFE_OR_UNREADABLE_PATH"
    assert result["artifacts"] == {}


def test_oversize_artifact_is_unknown_without_reading_it(tmp_path, monkeypatch):
    folder = tmp_path / "result"
    _complete(folder)
    oversized = folder / "report.html"
    with oversized.open("r+b") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    original = Path.open

    def monitor(path, *args, **kwargs):
        if path == oversized:
            raise AssertionError("oversize content was opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", monitor)
    result = inspect_backtest_results(folder)
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "UNSAFE_OR_UNREADABLE_PATH"
    assert result["artifacts"] == {}


def test_parent_entry_limit_is_conservative(tmp_path, monkeypatch):
    folder = tmp_path / "result"
    _complete(folder)

    class Entry:
        name = "unrelated"

    class Scan:
        def __enter__(self):
            return iter([Entry()] * 5001)

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(os, "scandir", lambda _path: Scan())
    result = inspect_backtest_results(folder)
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "UNSAFE_OR_UNREADABLE_PATH"
    assert result["artifacts"] == {}


@pytest.mark.parametrize("with_residue", [False, True])
def test_result_folder_replacement_is_detected_on_both_return_paths(
    tmp_path, monkeypatch, with_residue
):
    folder = tmp_path / "result"
    _complete(folder)
    if with_residue:
        (tmp_path / ".result.publish.lock").write_bytes(b"held")
    held_original = tmp_path / "original-result"
    original_parent_state = inspection._parent_state
    calls = 0

    def replace_after_initial_parent_observation(target):
        nonlocal calls
        value = original_parent_state(target)
        calls += 1
        if calls == 1:
            folder.rename(held_original)
            folder.mkdir()
            for name, content in FILES.items():
                (folder / name).write_bytes(content)
        return value

    monkeypatch.setattr(inspection, "_parent_state", replace_after_initial_parent_observation)
    result = inspect_backtest_results(folder)
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "OBSERVATION_CHANGED"
    assert result["artifacts"] == {}
    assert held_original.is_dir()
    assert all((held_original / name).read_bytes() == content
               for name, content in FILES.items())
