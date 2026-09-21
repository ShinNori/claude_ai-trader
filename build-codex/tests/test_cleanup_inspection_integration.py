"""Cleanup residues are observed conservatively and never auto-repaired."""
import hashlib
from pathlib import Path

import pandas as pd
import pytest

import aitrader.backtest_artifacts as artifacts
import aitrader.backtest_exports as exports
from aitrader.backtest_inspection import inspect_backtest_results


def _publish(folder, version):
    trades = pd.DataFrame([{"version": version, "pnl": version * 10}])
    equity = pd.DataFrame([{"version": version, "equity": 100 + version}])
    artifacts.publish_backtest_artifacts(
        folder, trades, equity, {"yearly": {}, "version": version}
    )


def _tree(root):
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
        ) for path in root.rglob("*") if path.is_file()
    }


def _assert_review(report):
    assert report["status"] == "REVIEW_REQUIRED"
    assert report["reason"] == "PUBLICATION_RESIDUE_PRESENT"
    assert report["artifacts"] == {}
    assert report["read_only"] is True
    assert report["generation_verified"] is False
    assert report["ready_for_live"] is False
    assert report["automatic_resume_allowed"] is False
    assert report["observation_atomic"] is False


def test_four_file_cleanup_failure_is_review_required_and_blocks_writer(
    tmp_path, monkeypatch
):
    folder = tmp_path / "result"
    _publish(folder, 1)
    original = Path.unlink

    def fail_backup_unlink(path, *args, **kwargs):
        if ".result.backup." in path.parent.name and path.name == "summary.json":
            raise OSError("cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_backup_unlink)
    with pytest.raises(artifacts.ArtifactCleanupError):
        _publish(folder, 2)
    before = _tree(tmp_path)
    report = inspect_backtest_results(folder)
    _assert_review(report)
    assert report["residues"]["lock"] == 1
    assert report["residues"]["backup"] == 1
    assert _tree(tmp_path) == before
    with pytest.raises(ValueError, match="実行中または要確認"):
        _publish(folder, 3)
    assert _tree(tmp_path) == before


def test_two_file_export_cleanup_failure_is_review_required_and_blocks_export(
    tmp_path, monkeypatch
):
    source, target = tmp_path / "source", tmp_path / "shared"
    _publish(source, 1)
    exports.export_summary_report(source, target)
    _publish(source, 2)
    original = Path.unlink

    def fail_backup_unlink(path, *args, **kwargs):
        if ".shared.export-backup." in path.parent.name and path.name == "summary.json":
            raise OSError("cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_backup_unlink)
    with pytest.raises(exports.ExportCleanupError):
        exports.export_summary_report(source, target)
    before = _tree(tmp_path)
    report = inspect_backtest_results(target)
    _assert_review(report)
    assert report["residues"]["lock"] == 1
    assert report["residues"]["export_backup"] == 1
    assert _tree(tmp_path) == before
    with pytest.raises(ValueError, match="実行中または要確認"):
        exports.export_summary_report(source, target)
    assert _tree(tmp_path) == before


def test_normal_four_files_observed_and_two_files_alone_incomplete(tmp_path):
    full, source, pair = tmp_path / "full", tmp_path / "source", tmp_path / "pair"
    _publish(full, 1)
    observed = inspect_backtest_results(full)
    assert observed["status"] == "OBSERVED"
    _publish(source, 1)
    exports.export_summary_report(source, pair)
    incomplete = inspect_backtest_results(pair)
    assert incomplete["status"] == "INCOMPLETE"
    assert incomplete["reason"] == "REQUIRED_ARTIFACT_MISSING"

