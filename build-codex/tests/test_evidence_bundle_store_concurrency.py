"""R14 concurrency regressions for the offline evidence bundle store."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import threading

import pytest

from aitrader import evidence_bundle_store as store


ROOT = Path(__file__).parents[1]
E1 = ROOT / "examples" / "history_validity_binding_v2_valid.json"
IDENTIFIER = "381454c638620b9f9eb66550a4e667b5bb4b4ad02399ad12b220e9e67353f7cf"


def _record(home):
    return home / store.STORE_DIRECTORY / f"{IDENTIFIER}{store.RECORD_SUFFIX}"


def _count_api(monkeypatch):
    calls = []
    original = store.inspect_history_validity_binding

    def counted(value):
        calls.append(1)
        return original(value)

    monkeypatch.setattr(store, "inspect_history_validity_binding", counted)
    return calls


def test_twelve_same_bundle_puts_only_store_or_noop(tmp_path, monkeypatch):
    threads = 12
    home = tmp_path / "runtime"
    barrier = threading.Barrier(threads)
    calls = _count_api(monkeypatch)

    def put_once(_):
        barrier.wait()
        return store.put(E1, home)

    results = [None] * threads

    def worker(index):
        results[index] = put_once(index)

    running = [threading.Thread(target=worker, args=(index,))
               for index in range(threads)]
    for item in running:
        item.start()
    for item in running:
        item.join()

    assert {item["status"] for item in results} <= {"STORED", "NO_OP"}
    assert len(calls) == threads
    assert store.verify(IDENTIFIER, home)["status"] == "REPRODUCED"
    assert len(calls) == threads + 1
    assert not list((home / store.STORE_DIRECTORY).glob("*.tmp"))


def test_existing_put_retries_only_transient_unreadable(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    assert store.put(E1, home)["status"] == "STORED"
    original = store._read_regular_bytes
    reads = []

    def transient(path, limit):
        if Path(path) == _record(home) and len(reads) < 2:
            reads.append(1)
            raise ValueError("record changed while being observed")
        return original(path, limit)

    monkeypatch.setattr(store, "_read_regular_bytes", transient)
    calls = _count_api(monkeypatch)
    result = store.put(E1, home)
    assert result["status"] == "NO_OP"
    assert len(reads) == 2
    assert calls == [1]


def test_existing_put_stops_after_finite_unreadable_retries(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    assert store.put(E1, home)["status"] == "STORED"
    original = store._read_regular_bytes
    reads = []

    def unreadable(path, limit):
        if Path(path) == _record(home):
            reads.append(1)
            raise ValueError("record remains busy")
        return original(path, limit)

    monkeypatch.setattr(store, "_read_regular_bytes", unreadable)
    monkeypatch.setattr(store.time, "sleep", lambda _: None)
    calls = _count_api(monkeypatch)
    result = store.put(E1, home)
    assert result["reason_codes"] == ["RECORD_CORRUPT"]
    assert len(reads) == store.PUT_READ_ATTEMPTS == 3
    assert calls == []


def test_replace_failure_accepts_matching_published_record_without_second_api(
    tmp_path, monkeypatch
):
    home = tmp_path / "runtime"

    def publish_then_fail(source, target):
        shutil.copyfile(source, target)
        raise PermissionError("target was concurrently opened")

    monkeypatch.setattr(store.os, "replace", publish_then_fail)
    calls = _count_api(monkeypatch)
    result = store.put(E1, home)
    assert result["status"] == "NO_OP"
    assert result["record_path"] == (
        f"{store.STORE_DIRECTORY}/{IDENTIFIER}{store.RECORD_SUFFIX}")
    assert calls == [1]
    assert store.verify(IDENTIFIER, home)["status"] == "REPRODUCED"
    assert calls == [1, 1]
    assert not list((home / store.STORE_DIRECTORY).glob("*.tmp"))


def test_replace_failure_rejects_type_changed_output_and_cleans_own_tmp(
    tmp_path, monkeypatch
):
    home = tmp_path / "runtime"

    def publish_changed_then_fail(source, target):
        shutil.copyfile(source, target)
        record = json.loads(Path(target).read_text(encoding="utf-8"))
        assert record["output"]["current_signal"] is False
        record["output"]["current_signal"] = 0
        Path(target).write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        raise PermissionError("target was concurrently opened")

    monkeypatch.setattr(store.os, "replace", publish_changed_then_fail)
    calls = _count_api(monkeypatch)
    result = store.put(E1, home)
    assert result["reason_codes"] == ["WRITE_FAILED"]
    assert calls == [1]
    assert not list((home / store.STORE_DIRECTORY).glob("*.tmp"))


@pytest.mark.parametrize("failure", [OSError("disk"), PermissionError("busy")])
def test_failed_replace_without_final_record_cleans_own_tmp(
    tmp_path, monkeypatch, failure
):
    home = tmp_path / "runtime"

    def fail(*_):
        raise failure

    monkeypatch.setattr(store.os, "replace", fail)
    calls = _count_api(monkeypatch)
    result = store.put(E1, home)
    assert result["reason_codes"] == ["WRITE_FAILED"]
    assert calls == [1]
    assert not _record(home).exists()
    assert not list((home / store.STORE_DIRECTORY).glob("*.tmp"))
