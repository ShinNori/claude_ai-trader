"""Runner entry points reject unsafe raw parents and state leaves before writes."""
import hashlib
import os
import sqlite3
from datetime import timedelta

import pytest

import aitrader.runner as runner
import aitrader.mock_demo as mock_demo
from aitrader.review_runner import run_reviewed_mock
from aitrader.runner import RunError, Valuation, initialize_mock
from aitrader_ops.ledger import Ledger
from test_runner import DAY, NOW, setup


def _state(home):
    return {
        str(path.relative_to(home)): (
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
        ) for path in home.rglob("*") if path.is_file()
    }


def test_initialize_rejects_parent_reparse_before_mkdir_or_ledger(
    tmp_path, monkeypatch
):
    parent = tmp_path / "unsafe-parent"
    parent.mkdir()
    home = parent / "new-home"
    original_junction = os.path.isjunction
    monkeypatch.setattr(os.path, "isjunction",
                        lambda path: path == parent or original_junction(path))
    monkeypatch.setattr(Ledger, "__init__", lambda *a, **k: pytest.fail("Ledger reached"))
    with pytest.raises(RunError):
        initialize_mock(home, 1000000, [], NOW - timedelta(days=1))
    assert not home.exists()


def test_safe_initialize_still_creates_dedicated_mock_home(tmp_path):
    home = tmp_path / "new-home"
    initialize_mock(home, 1000000, [], NOW - timedelta(days=1))
    assert (home / "mock-runner.json").is_file()
    assert (home / "ledger.sqlite").is_file()


def test_run_rejects_parent_reparse_before_sqlite_or_state_change(
    setup, monkeypatch
):
    home, execute = setup
    before = _state(home)
    parent = home.parent
    original_junction = os.path.isjunction
    monkeypatch.setattr(os.path, "isjunction",
                        lambda path: path == parent or original_junction(path))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("SQLite reached"))
    monkeypatch.setattr(Ledger, "__init__", lambda *a, **k: pytest.fail("Ledger reached"))
    with pytest.raises(RunError):
        execute()
    assert _state(home) == before


@pytest.mark.parametrize("leaf", [
    "mock-runner.json", "ledger.sqlite", "orchestration.sqlite",
    "runner-lock.sqlite", "runs",
])
def test_run_rejects_reparse_state_leaf_before_sqlite(
    setup, monkeypatch, leaf
):
    home, execute = setup
    target = home / leaf
    if not target.exists():
        target.mkdir() if leaf == "runs" else target.write_bytes(b"")
    before = _state(home)
    original_junction = os.path.isjunction
    monkeypatch.setattr(os.path, "isjunction",
                        lambda path: path == target or original_junction(path))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("SQLite reached"))
    monkeypatch.setattr(Ledger, "__init__", lambda *a, **k: pytest.fail("Ledger reached"))
    with pytest.raises(RunError):
        execute()
    assert _state(home) == before


@pytest.mark.parametrize("leaf,wrong_kind", [
    ("mock-runner.json", "directory"), ("runs", "file"),
])
def test_run_rejects_wrong_leaf_type_before_sqlite(
    setup, monkeypatch, leaf, wrong_kind
):
    home, execute = setup
    target = home / leaf
    if target.exists():
        target.unlink() if target.is_file() else target.rmdir()
    target.mkdir() if wrong_kind == "directory" else target.write_bytes(b"wrong")
    before = _state(home)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("SQLite reached"))
    monkeypatch.setattr(Ledger, "__init__", lambda *a, **k: pytest.fail("Ledger reached"))
    with pytest.raises(RunError):
        execute()
    assert _state(home) == before


@pytest.mark.parametrize("leaf", ["review-lock.sqlite", "review-inputs.sqlite"])
def test_review_entry_rejects_unsafe_review_leaf_before_logging_or_sqlite(
    setup, monkeypatch, leaf
):
    home, _ = setup
    target = home / leaf
    target.write_bytes(b"")
    before = _state(home)
    original_junction = os.path.isjunction
    monkeypatch.setattr(os.path, "isjunction",
                        lambda path: path == target or original_junction(path))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("SQLite reached"))
    with pytest.raises(RunError):
        run_reviewed_mock(
            home, "review1", DAY, [], {}, NOW, Valuation(1000000, 1000000),
            started_at=NOW, market_context={},
        )
    assert _state(home) == before


@pytest.mark.parametrize("entry", ["runner", "review"])
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_sqlite_sidecar_reparse_is_rejected_at_runner_and_review_entries(
    setup, monkeypatch, entry, suffix
):
    home, execute = setup
    base = "ledger.sqlite" if entry == "runner" else "review-lock.sqlite"
    sidecar = home / (base + suffix)
    sidecar.write_bytes(b"closed-sidecar")
    before = _state(home)
    original_junction = os.path.isjunction
    monkeypatch.setattr(os.path, "isjunction",
                        lambda path: path == sidecar or original_junction(path))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("SQLite reached"))
    if entry == "runner":
        with pytest.raises(RunError):
            execute()
    else:
        with pytest.raises(RunError):
            run_reviewed_mock(
                home, "review-sidecar", DAY, [], {}, NOW,
                Valuation(1000000, 1000000), started_at=NOW,
                market_context={},
            )
    assert _state(home) == before


def test_regular_sqlite_sidecars_are_allowed_by_guard_only(setup):
    home, _ = setup
    bases = ("ledger.sqlite", "orchestration.sqlite", "runner-lock.sqlite",
             "review-lock.sqlite", "review-inputs.sqlite")
    for base in bases:
        for suffix in ("-wal", "-shm", "-journal"):
            (home / (base + suffix)).write_bytes(b"closed-regular-sidecar")
    assert runner._runner_home_guard(
        home, extra_files=("review-lock.sqlite", "review-inputs.sqlite")
    ) == home.absolute()


def test_mock_demo_rejects_parent_reparse_before_initialization(tmp_path, monkeypatch):
    parent = tmp_path / "unsafe-demo-parent"
    parent.mkdir()
    home = parent / "demo"
    original_junction = os.path.isjunction
    monkeypatch.setattr(os.path, "isjunction",
                        lambda path: path == parent or original_junction(path))
    monkeypatch.setattr(mock_demo, "initialize_mock",
                        lambda *a, **k: pytest.fail("initialization reached"))
    with pytest.raises(RunError):
        mock_demo.run_demo(home)
    assert not home.exists()
