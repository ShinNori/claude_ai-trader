"""Contract tests for the bounded offline evidence bundle store."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path

import pytest

import aitrader.evidence_bundle_store as store
from aitrader.packet_cli import _mapping


ROOT = Path(__file__).parents[1]
E1 = ROOT / "examples" / "history_validity_binding_v2_valid.json"
V1 = ROOT / "examples" / "history_validity_binding_valid.json"
IDENTIFIER = "381454c638620b9f9eb66550a4e667b5bb4b4ad02399ad12b220e9e67353f7cf"


def _source(tmp_path, body=None, name="bundle.json"):
    path = tmp_path / name
    path.write_bytes(E1.read_bytes() if body is None else body)
    return path


def _record(home, identifier=IDENTIFIER):
    return home / store.STORE_DIRECTORY / f"{identifier}{store.RECORD_SUFFIX}"


def _stored(tmp_path):
    home = tmp_path / "runtime"
    source = _source(tmp_path)
    result = store.put(source, home)
    assert result["status"] == "STORED"
    return home, source, result, _record(home, result["bundle_sha256"])


def _rewrite_record(path, change):
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _count_api(monkeypatch):
    calls = []
    original = store.inspect_history_validity_binding

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(store, "inspect_history_validity_binding", counted)
    return calls


def test_put_fixed_fixture_and_record_contract(tmp_path):
    home, _, result, path = _stored(tmp_path)
    assert result == {
        "mode": store.MODE,
        "source_origin": "offline_fixture",
        "status": "STORED",
        "reason_codes": [],
        "bundle_sha256": IDENTIFIER,
        "bundle_bytes": 6148,
        "bundle_status": "VERIFIED_OFFLINE_BINDING",
        "bundle_reason_codes": [],
        "stored_at": result["stored_at"],
        "record_path": f"evidence_bundles/{IDENTIFIER}.bundle.json",
        "ready_for_live": False,
        "current_signal": False,
        "read_only": False,
    }
    assert datetime.fromisoformat(result["stored_at"]).utcoffset().total_seconds() == 0
    record = json.loads(path.read_text(encoding="utf-8"))
    assert set(record) == store._RECORD_KEYS
    expected = json.loads(
        (ROOT / "tests" / "binding_v2_expected.json").read_text(encoding="utf-8")
    )["E1"]
    assert record["output"] == expected
    assert path.parent == home / "evidence_bundles"


def test_no_op_preserves_timestamp_mtime_and_calls_api_once(tmp_path, monkeypatch):
    home, source, first, path = _stored(tmp_path)
    before = path.stat().st_mtime_ns
    calls = _count_api(monkeypatch)
    again = store.put(source, home)
    assert again["status"] == "NO_OP"
    assert again["stored_at"] == first["stored_at"]
    assert path.stat().st_mtime_ns == before
    assert len(calls) == 1


def test_different_raw_spelling_gets_distinct_id_but_same_output(tmp_path):
    home = tmp_path / "runtime"
    first = store.put(_source(tmp_path), home)
    second = store.put(
        _source(tmp_path, E1.read_bytes() + b"\n", "newline.json"), home)
    assert first["status"] == second["status"] == "STORED"
    assert first["bundle_sha256"] != second["bundle_sha256"]
    records = [json.loads(_record(home, item["bundle_sha256"]).read_text("utf-8"))
               for item in (first, second)]
    assert records[0]["output"] == records[1]["output"]


def test_bom_is_preserved_in_stored_raw_bytes(tmp_path):
    home = tmp_path / "runtime"
    result = store.put(_source(tmp_path, b"\xef\xbb\xbf" + E1.read_bytes()), home)
    record = json.loads(_record(home, result["bundle_sha256"]).read_text("utf-8"))
    assert result["status"] == "STORED"
    assert base64.b64decode(record["bundle_base64"]).startswith(b"\xef\xbb\xbf")


def test_inner_data_incomplete_bundle_is_successfully_stored(tmp_path):
    result = store.put(_source(tmp_path, V1.read_bytes()), tmp_path / "runtime")
    assert result["status"] == "STORED"
    assert result["bundle_status"] == "DATA_INCOMPLETE"
    assert result["bundle_reason_codes"] == ["INVALID_BUNDLE"]


@pytest.mark.parametrize("body", [b"[]", b'{"x":NaN}', b'{"x":1,"x":2}', b"{"])
def test_invalid_input_is_unreadable_without_api(tmp_path, monkeypatch, body):
    calls = _count_api(monkeypatch)
    result = store.put(_source(tmp_path, body), tmp_path / "runtime")
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == ["INPUT_UNREADABLE"]
    assert calls == []


def test_oversize_input_is_unreadable_without_api_or_directory(tmp_path, monkeypatch):
    calls = _count_api(monkeypatch)
    home = tmp_path / "runtime"
    result = store.put(
        _source(tmp_path, b" " * (store.MAX_BUNDLE_BYTES + 1)), home)
    assert result["reason_codes"] == ["INPUT_UNREADABLE"]
    assert calls == []
    assert not (home / "evidence_bundles").exists()


def test_dropbox_home_is_rejected_before_input_or_api(tmp_path, monkeypatch):
    calls = _count_api(monkeypatch)
    result = store.put(tmp_path / "missing.json", tmp_path / "Dropbox" / "runtime")
    assert result["reason_codes"] == ["STORE_PATH_INVALID"]
    assert calls == []


def test_store_reuses_the_exact_shared_mapping_object():
    assert store._mapping is _mapping


def test_snapshot_failure_is_write_failed_without_api(tmp_path, monkeypatch):
    calls = _count_api(monkeypatch)
    monkeypatch.setattr(store, "_snapshot_mapping", lambda *args: (_ for _ in ()).throw(OSError()))
    result = store.put(_source(tmp_path), tmp_path / "runtime")
    assert result["reason_codes"] == ["WRITE_FAILED"]
    assert calls == []


def test_verify_reproduces_with_one_api_call(tmp_path, monkeypatch):
    home, _, first, _ = _stored(tmp_path)
    calls = _count_api(monkeypatch)
    result = store.verify(IDENTIFIER, home)
    assert result["status"] == "REPRODUCED"
    assert result["stored_at"] == first["stored_at"]
    assert result["read_only"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("identifier", ["", "0" * 63, "G" * 64, "A" * 64, "../" + "0" * 64])
def test_invalid_verify_id_is_rejected_before_path_guard(identifier, tmp_path, monkeypatch):
    calls = _count_api(monkeypatch)
    result = store.verify(identifier, tmp_path / "Dropbox" / "unsafe")
    assert result["reason_codes"] == ["RECORD_UNREADABLE"]
    assert result["bundle_sha256"] is None
    assert calls == []


def test_missing_record_does_not_create_home_or_call_api(tmp_path, monkeypatch):
    calls = _count_api(monkeypatch)
    home = tmp_path / "absent-runtime"
    result = store.verify("0" * 64, home)
    assert result["reason_codes"] == ["RECORD_NOT_FOUND"]
    assert result["bundle_sha256"] == "0" * 64
    assert calls == []
    assert not home.exists()


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda r: r.update(store_format="v0"), "UNSUPPORTED_FORMAT"),
        (lambda r: r.update(api_mode="history_validity_binding_fixture_v1"), "UNSUPPORTED_FORMAT"),
        (lambda r: r.update(extra=True), "UNSUPPORTED_FORMAT"),
        (lambda r: r.update(bundle_sha256="0" * 64), "RECORD_CORRUPT"),
        (lambda r: r.update(bundle_bytes=True), "RECORD_CORRUPT"),
        (lambda r: r.update(stored_at="not-an-instant"), "RECORD_CORRUPT"),
        (lambda r: r.update(bundle_base64="!"), "RECORD_CORRUPT"),
    ],
)
def test_record_format_and_integrity_fail_before_api(
    tmp_path, monkeypatch, change, reason
):
    home, _, _, path = _stored(tmp_path)
    _rewrite_record(path, change)
    calls = _count_api(monkeypatch)
    result = store.verify(IDENTIFIER, home)
    assert result["reason_codes"] == [reason]
    assert calls == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda output: output.update(matched_subject_count=1),
        lambda output: output.update(matched_subject_count=True),
        lambda output: output.update(extra_field=None),
        lambda output: output.pop("matched_subject_count"),
    ],
)
def test_output_shape_value_and_type_changes_are_mismatches_after_one_api(
    tmp_path, monkeypatch, mutate
):
    home, _, _, path = _stored(tmp_path)
    _rewrite_record(path, lambda record: mutate(record["output"]))
    calls = _count_api(monkeypatch)
    result = store.verify(IDENTIFIER, home)
    assert result["reason_codes"] == ["OUTPUT_MISMATCH"]
    assert result["stored_at"] is not None
    assert result["record_path"].endswith(".bundle.json")
    assert len(calls) == 1


def test_corrupt_base64_payload_fails_hash_before_api(tmp_path, monkeypatch):
    home, _, _, path = _stored(tmp_path)

    def change(record):
        encoded = record["bundle_base64"]
        record["bundle_base64"] = ("A" if encoded[0] != "A" else "B") + encoded[1:]

    _rewrite_record(path, change)
    calls = _count_api(monkeypatch)
    result = store.verify(IDENTIFIER, home)
    assert result["reason_codes"] == ["RECORD_CORRUPT"]
    assert calls == []


def test_oversize_record_is_unreadable_before_api(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    directory = home / "evidence_bundles"
    directory.mkdir(parents=True)
    _record(home).write_bytes(b" " * (store.MAX_RECORD_BYTES + 1))
    calls = _count_api(monkeypatch)
    result = store.verify(IDENTIFIER, home)
    assert result["reason_codes"] == ["RECORD_UNREADABLE"]
    assert calls == []


def test_tmp_file_is_ignored_by_verify(tmp_path):
    home = tmp_path / "runtime"
    directory = home / "evidence_bundles"
    directory.mkdir(parents=True)
    (directory / f"{IDENTIFIER}.bundle.json.old.tmp").write_text("{}", "utf-8")
    result = store.verify(IDENTIFIER, home)
    assert result["reason_codes"] == ["RECORD_NOT_FOUND"]


def test_record_write_failure_has_no_final_file(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    monkeypatch.setattr(store, "_write_record", lambda *args: (_ for _ in ()).throw(OSError()))
    result = store.put(_source(tmp_path), home)
    assert result["reason_codes"] == ["WRITE_FAILED"]
    assert result["bundle_status"] == "VERIFIED_OFFLINE_BINDING"
    assert result["stored_at"] is None
    assert result["record_path"] is None
    assert not _record(home).exists()


def test_replace_failure_leaves_only_ignored_tmp(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    monkeypatch.setattr(store.os, "replace", lambda *args: (_ for _ in ()).throw(OSError()))
    result = store.put(_source(tmp_path), home)
    assert result["reason_codes"] == ["WRITE_FAILED"]
    assert not _record(home).exists()
    assert list((home / "evidence_bundles").glob("*.tmp"))
    assert store.verify(IDENTIFIER, home)["reason_codes"] == ["RECORD_NOT_FOUND"]


def test_existing_corrupt_record_is_not_overwritten(tmp_path):
    home, source, _, path = _stored(tmp_path)
    path.write_text("{}", encoding="utf-8")
    before = path.read_bytes()
    result = store.put(source, home)
    assert result["reason_codes"] == ["RECORD_CORRUPT"]
    assert path.read_bytes() == before


def test_put_calls_api_once_and_does_not_mutate_input(tmp_path, monkeypatch):
    source = _source(tmp_path)
    before = (source.read_bytes(), source.stat().st_size, source.stat().st_mtime_ns)
    calls = _count_api(monkeypatch)
    result = store.put(source, tmp_path / "runtime")
    after = (source.read_bytes(), source.stat().st_size, source.stat().st_mtime_ns)
    assert result["status"] == "STORED"
    assert len(calls) == 1
    assert after == before


def test_record_json_is_sorted_and_has_exactly_seven_keys(tmp_path):
    _, _, _, path = _stored(tmp_path)
    body = path.read_text(encoding="utf-8")
    record = json.loads(body)
    assert list(record) == sorted(store._RECORD_KEYS)
    assert body == json.dumps(
        record, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":")) + "\n"


def test_same_bundle_concurrent_puts_leave_reproducible_record(tmp_path):
    home = tmp_path / "runtime"
    source = _source(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: store.put(source, home), range(2)))
    assert {item["status"] for item in results} <= {"STORED", "NO_OP"}
    assert store.verify(IDENTIFIER, home)["status"] == "REPRODUCED"


def test_different_bundle_concurrent_puts_do_not_interfere(tmp_path):
    home = tmp_path / "runtime"
    sources = [
        _source(tmp_path, E1.read_bytes(), "one.json"),
        _source(tmp_path, E1.read_bytes() + b"\n", "two.json"),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda path: store.put(path, home), sources))
    assert [item["status"] for item in results] == ["STORED", "STORED"]
    assert len({item["bundle_sha256"] for item in results}) == 2
    assert all(store.verify(item["bundle_sha256"], home)["status"] == "REPRODUCED"
               for item in results)


def test_record_symlink_is_unreadable_without_api(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    directory = home / "evidence_bundles"
    directory.mkdir(parents=True)
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    try:
        _record(home).symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlink unavailable: {error}")
    calls = _count_api(monkeypatch)
    result = store.verify(IDENTIFIER, home)
    assert result["reason_codes"] == ["RECORD_UNREADABLE"]
    assert calls == []
