"""Post-publication cleanup failures are explicit and keep the target locked."""
import traceback
from pathlib import Path

import pytest

from aitrader.backtest_exports import ExportCleanupError, export_summary_report


NEW = {
    "summary.json": '{"yearly":{},"generation":"new"}',
    "report.html": "<html>new report</html>",
}
OLD = {
    "summary.json": '{"yearly":{},"generation":"old"}',
    "report.html": "<html>old report</html>",
}


def _write_pair(folder, values):
    folder.mkdir()
    for name, value in values.items():
        (folder / name).write_text(value, encoding="utf-8")


@pytest.mark.parametrize("failure", [
    "backup-summary-unlink",
    "backup-report-unlink",
    "backup-rmdir",
    "stage-rmdir",
])
def test_cleanup_failure_keeps_published_pair_lock_and_blocks_next_export(
    tmp_path, monkeypatch, failure
):
    secret = "SECRET_CLEANUP_DETAIL_7319"
    source, target = tmp_path / "source", tmp_path / "target"
    _write_pair(source, NEW)
    _write_pair(target, OLD)
    original_unlink = Path.unlink
    original_rmdir = Path.rmdir
    injected = False

    def fail_unlink(path, *args, **kwargs):
        nonlocal injected
        expected = {
            "backup-summary-unlink": "summary.json",
            "backup-report-unlink": "report.html",
        }.get(failure)
        if (expected and ".target.export-backup." in path.parent.name
                and path.name == expected):
            injected = True
            raise OSError(secret)
        return original_unlink(path, *args, **kwargs)

    def fail_rmdir(path, *args, **kwargs):
        nonlocal injected
        matches = (
            failure == "backup-rmdir" and ".target.export-backup." in path.name
        ) or (
            failure == "stage-rmdir" and ".target.export-stage." in path.name
        )
        if matches:
            injected = True
            raise OSError(secret)
        return original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    monkeypatch.setattr(Path, "rmdir", fail_rmdir)
    try:
        export_summary_report(source, target)
    except ExportCleanupError as exc:
        rendered = "".join(traceback.format_exception(exc))
    else:
        raise AssertionError("cleanup failure was reported as success")

    assert injected is True
    assert secret not in rendered
    assert "OSError" not in rendered
    assert all((target / name).read_text(encoding="utf-8") == value
               for name, value in NEW.items())
    target_lock = tmp_path / ".target.publish.lock"
    source_lock = tmp_path / ".source.publish.lock"
    assert target_lock.is_file()
    assert not source_lock.exists()
    assert any(".target.export-" in path.name for path in tmp_path.iterdir())

    with pytest.raises(ValueError, match="実行中または要確認"):
        export_summary_report(source, target)
    assert target_lock.is_file()
    assert all((target / name).read_text(encoding="utf-8") == value
               for name, value in NEW.items())

