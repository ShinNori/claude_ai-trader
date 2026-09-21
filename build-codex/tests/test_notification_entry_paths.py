"""Notification entry points reject unsafe raw paths before state changes."""
import copy
import hashlib
import os

import pytest

from aitrader.mock_delivery import deliver_prepared_mock, reconcile_prepared_mock
from aitrader.notification_plan import prepare_notifications
from aitrader.notification_queue import enqueue_prepared_notifications
from aitrader.runner import RunError
from test_notification_plan import CONTEXTS
from test_notification_queue import CONFIG
from test_runner import DAY, NOW, setup


def _tree(home):
    return {str(p.relative_to(home)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                       p.stat().st_mtime_ns)
            for p in home.rglob("*") if p.is_file()}


def _prepare(home):
    return prepare_notifications(home, "run1", DAY, contexts=copy.deepcopy(CONTEXTS),
                                 settings=copy.deepcopy(CONFIG))


def _enqueue(home):
    return enqueue_prepared_notifications(home, "run1", DAY, now=NOW,
        contexts=copy.deepcopy(CONTEXTS), settings=copy.deepcopy(CONFIG))


def _deliver(home):
    return deliver_prepared_mock(home, "run1", DAY, now=NOW,
        contexts=copy.deepcopy(CONTEXTS), settings=copy.deepcopy(CONFIG),
        stub_results=["success"])


def _reconcile(home):
    return reconcile_prepared_mock(home, "run1", DAY, now=NOW,
        contexts=copy.deepcopy(CONTEXTS), settings=copy.deepcopy(CONFIG))


def _ready(setup, operation):
    home, execute = setup
    execute()
    if operation != "prepare":
        _prepare(home)
    if operation in ("deliver", "reconcile"):
        _enqueue(home)
    return home


CALL = {"prepare": _prepare, "enqueue": _enqueue,
        "deliver": _deliver, "reconcile": _reconcile}


@pytest.mark.parametrize("operation", CALL)
def test_parent_reparse_blocks_every_notification_entry(setup, monkeypatch, operation):
    home = _ready(setup, operation)
    before = _tree(home)
    parent = home.parent
    original = os.path.isjunction
    monkeypatch.setattr(os.path, "isjunction",
                        lambda path: path == parent or original(path))
    with pytest.raises(RunError):
        CALL[operation](home)
    assert _tree(home) == before


@pytest.mark.parametrize("operation", CALL)
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_notification_sqlite_sidecar_reparse_blocks_every_entry(
    setup, monkeypatch, operation, suffix
):
    home = _ready(setup, operation)
    database = "notification.sqlite" if operation in ("deliver", "reconcile") \
        else "notification-plans.sqlite"
    sidecar = home / (database + suffix)
    sidecar.write_bytes(b"closed-sidecar")
    before = _tree(home)
    original = os.path.isjunction
    monkeypatch.setattr(os.path, "isjunction",
                        lambda path: path == sidecar or original(path))
    with pytest.raises(RunError):
        CALL[operation](home)
    assert _tree(home) == before


@pytest.mark.parametrize("operation,relative", [
    ("prepare", "manifest.json"),
    ("enqueue", "notification_plan.json"),
    ("deliver", "notification_queue.json"),
    ("reconcile", "notification_queue.json"),
])
def test_unsafe_saved_run_child_blocks_every_entry(
    setup, monkeypatch, operation, relative
):
    home = _ready(setup, operation)
    target = home / "runs" / str(DAY) / "run1" / relative
    before = _tree(home)
    original = os.path.isjunction
    monkeypatch.setattr(os.path, "isjunction",
                        lambda path: path == target or original(path))
    with pytest.raises(RunError):
        CALL[operation](home)
    assert _tree(home) == before


def test_normal_prepare_enqueue_deliver_reconcile_flow_remains_offline(setup):
    home = _ready(setup, "deliver")
    delivered = _deliver(home)
    assert delivered["real_sent_by_this_call"] is False
    reconciled = _reconcile(home)
    assert reconciled["real_sent_by_this_call"] is False
