"""Claude 第14回 独立反証: 人工束 put/verify の原子性境界・hash 照合・API 呼出回数のうち既存 2 試験ファイルが触れていない点。

対象: aitrader/evidence_bundle_store.py, aitrader/evidence_bundle_store_cli.py。製品・既存試験・例は変更しない。
同一 ID の多重並行 put で WRITE_FAILED / RECORD_CORRUPT が出る件（R14-01）は契約未確定のため、
ここでは安全性の不変条件だけを固定する（状態語彙は固定しない）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading

import pytest

from aitrader import evidence_bundle_store as store
from aitrader import evidence_bundle_store_cli as cli

ROOT = Path(__file__).parents[1]
E1 = ROOT / "examples/history_validity_binding_v2_valid.json"
IDENT = "381454c638620b9f9eb66550a4e667b5bb4b4ad02399ad12b220e9e67353f7cf"
EXPECTED = json.loads((Path(__file__).with_name("binding_v2_expected.json")).read_text(encoding="utf-8"))


def _home(tmp_path):
    return tmp_path / "runtime"


def _record(home):
    return home / "evidence_bundles" / f"{IDENT}.bundle.json"


def _stored(tmp_path):
    home = _home(tmp_path)
    assert store.put(E1, home)["status"] == "STORED"
    return home


def _rewrite(home, change):
    path = _record(home)
    record = json.loads(path.read_text(encoding="utf-8"))
    change(record)
    path.write_bytes((json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    return path.read_bytes()


def _count_api(monkeypatch):
    calls = []
    original = store.inspect_history_validity_binding
    monkeypatch.setattr(store, "inspect_history_validity_binding",
                        lambda value: calls.append(1) or original(value))
    return calls


def test_fixture_identifier_is_the_raw_byte_hash():
    assert hashlib.sha256(E1.read_bytes()).hexdigest() == IDENT


def test_put_over_healthy_record_with_drifted_output_is_refused_and_not_overwritten(tmp_path, monkeypatch):
    home = _stored(tmp_path)
    before = _rewrite(home, lambda r: r["output"].__setitem__("matched_subject_count", 1))
    calls = _count_api(monkeypatch)
    result = store.put(E1, home)
    assert result["reason_codes"] == ["RECORD_CORRUPT"]
    assert result["stored_at"] is None and result["record_path"] is None
    assert _record(home).read_bytes() == before
    assert calls == [1]  # 既存記録の再読検査で 1 回だけ。再評価を重ねない


@pytest.mark.parametrize("bad_output", [[], None, "VERIFIED_OFFLINE_BINDING", 0])
def test_non_mapping_saved_output_is_mismatch_after_exactly_one_api_call(tmp_path, monkeypatch, bad_output):
    home = _stored(tmp_path)
    _rewrite(home, lambda r: r.__setitem__("output", bad_output))
    calls = _count_api(monkeypatch)
    result = store.verify(IDENT, home)
    assert result["reason_codes"] == ["OUTPUT_MISMATCH"]
    assert result["bundle_status"] == "VERIFIED_OFFLINE_BINDING"
    assert result["record_path"] == f"evidence_bundles/{IDENT}.bundle.json"
    assert calls == [1]


@pytest.mark.parametrize("change", [
    lambda r: r.__setitem__("stored_at", "2026-09-17T22:00:00+09:00"),   # UTC 以外の offset
    lambda r: r.__setitem__("stored_at", "2026-09-17T13:00:00"),         # timezone なし
    lambda r: r.__setitem__("stored_at", 1758114000),
    lambda r: r.__setitem__("bundle_base64", r["bundle_base64"] + "\n"),
    lambda r: r.__setitem__("bundle_base64", r["bundle_base64"].rstrip("=") + "=" * 4),
    lambda r: r.__setitem__("bundle_bytes", float(r["bundle_bytes"])),
    lambda r: r.__setitem__("bundle_bytes", True),
    lambda r: r.__setitem__("bundle_bytes", r["bundle_bytes"] + 1),
    lambda r: r.__setitem__("bundle_sha256", r["bundle_sha256"].upper()),
], ids=["offset", "naive", "epoch", "b64-newline", "b64-padding", "size-float", "size-bool", "size-plus1", "hash-upper"])
def test_typed_record_fields_are_corrupt_without_api_call(tmp_path, monkeypatch, change):
    home = _stored(tmp_path)
    _rewrite(home, change)
    calls = _count_api(monkeypatch)
    assert store.verify(IDENT, home)["reason_codes"] == ["RECORD_CORRUPT"]
    assert store.put(E1, home)["reason_codes"] == ["RECORD_CORRUPT"]
    assert calls == []


def test_duplicate_key_record_is_unreadable_and_put_refuses_without_api(tmp_path, monkeypatch):
    home = _stored(tmp_path)
    path = _record(home)
    body = path.read_bytes().rstrip(b"\n")
    path.write_bytes(body[:-1] + b',"store_format":"evidence_bundle_store_v1"}\n')
    before = path.read_bytes()
    calls = _count_api(monkeypatch)
    assert store.verify(IDENT, home)["reason_codes"] == ["RECORD_UNREADABLE"]
    assert store.put(E1, home)["reason_codes"] == ["RECORD_CORRUPT"]
    assert path.read_bytes() == before and calls == []


def test_store_directory_or_home_being_a_file_is_store_path_invalid_without_api(tmp_path, monkeypatch):
    calls = _count_api(monkeypatch)
    home = _home(tmp_path)
    home.mkdir()
    (home / "evidence_bundles").write_text("x", encoding="utf-8")
    assert store.put(E1, home)["reason_codes"] == ["STORE_PATH_INVALID"]
    assert store.verify(IDENT, home)["reason_codes"] == ["STORE_PATH_INVALID"]
    file_home = tmp_path / "home-file"
    file_home.write_text("x", encoding="utf-8")
    assert store.put(E1, file_home)["reason_codes"] == ["STORE_PATH_INVALID"]
    assert calls == []


@pytest.mark.parametrize("identifier", [None, 123, IDENT.upper(), IDENT + "\n", " " + IDENT, IDENT.encode()])
def test_non_string_and_non_canonical_ids_are_rejected_without_touching_storage(tmp_path, monkeypatch, identifier):
    calls = _count_api(monkeypatch)
    home = _home(tmp_path)
    result = store.verify(identifier, home)
    assert result["reason_codes"] == ["RECORD_UNREADABLE"]
    assert result["bundle_sha256"] is None
    assert not home.exists() and calls == []


def test_path_object_id_is_rejected_even_when_its_text_is_valid(tmp_path):
    home = _stored(tmp_path)
    assert store.verify(Path(IDENT), home)["reason_codes"] == ["RECORD_UNREADABLE"]


def test_same_bytes_from_another_path_is_noop_and_leaves_no_temporary_files(tmp_path, monkeypatch):
    home = _stored(tmp_path)
    first = json.loads(_record(home).read_text(encoding="utf-8"))["stored_at"]
    before = _record(home).stat().st_mtime_ns
    other = tmp_path / "copy-elsewhere.json"
    shutil.copyfile(E1, other)
    calls = _count_api(monkeypatch)
    result = store.put(other, home)
    assert result["status"] == "NO_OP" and result["stored_at"] == first
    assert _record(home).stat().st_mtime_ns == before
    assert calls == [1]
    names = sorted(p.name for p in (home / "evidence_bundles").iterdir())
    assert names == [f"{IDENT}.bundle.json"]


def test_input_located_inside_the_store_directory_does_not_disturb_records(tmp_path):
    home = _stored(tmp_path)
    before = _record(home).read_bytes()
    inner = home / "evidence_bundles" / "copy.json"
    shutil.copyfile(E1, inner)
    assert store.put(inner, home)["status"] == "NO_OP"
    assert _record(home).read_bytes() == before
    assert inner.read_bytes() == E1.read_bytes()


def test_verify_never_writes_even_on_mismatch(tmp_path):
    home = _stored(tmp_path)
    _rewrite(home, lambda r: r["output"].__setitem__("read_only", 1))
    directory = home / "evidence_bundles"
    snapshot = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in directory.iterdir()}
    assert store.verify(IDENT, home)["reason_codes"] == ["OUTPUT_MISMATCH"]
    assert {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in directory.iterdir()} == snapshot


def test_replace_failure_after_evaluation_keeps_inner_diagnosis_but_no_location(tmp_path, monkeypatch):
    home = _home(tmp_path)
    def refuse(*_):
        raise PermissionError("target is busy")
    monkeypatch.setattr(store.os, "replace", refuse)
    calls = _count_api(monkeypatch)
    result = store.put(E1, home)
    assert result["reason_codes"] == ["WRITE_FAILED"]
    assert result["bundle_status"] == "VERIFIED_OFFLINE_BINDING"
    assert result["bundle_sha256"] == IDENT and result["bundle_bytes"] == E1.stat().st_size
    assert result["stored_at"] is None and result["record_path"] is None
    assert not _record(home).exists() and calls == [1]


def test_stored_record_carries_exact_bytes_and_expected_output(tmp_path):
    home = _stored(tmp_path)
    record = json.loads(_record(home).read_text(encoding="utf-8"))
    assert base64.b64decode(record["bundle_base64"], validate=True) == E1.read_bytes()
    assert record["output"] == EXPECTED["E1"]
    assert record["api_mode"] == "history_validity_binding_fixture_v2"


def test_many_concurrent_same_id_puts_keep_the_safety_invariants(tmp_path):
    """多重並行でも: 最終記録は健全・原バイト一致、成功は 1 件以上、失敗結果は場所を主張しない。"""
    threads, results = 8, []
    for round_index in range(5):
        home = tmp_path / f"runtime-{round_index}"
        barrier = threading.Barrier(threads)
        def worker():
            barrier.wait()
            results.append(store.put(E1, home))
        pool = [threading.Thread(target=worker) for _ in range(threads)]
        for item in pool:
            item.start()
        for item in pool:
            item.join()
        assert store.verify(IDENT, home)["status"] == "REPRODUCED"
        record = json.loads(_record(home).read_text(encoding="utf-8"))
        assert base64.b64decode(record["bundle_base64"], validate=True) == E1.read_bytes()
    assert any(item["status"] == "STORED" for item in results)
    for item in results:
        assert item["bundle_sha256"] == IDENT
        if item["status"] == "DATA_INCOMPLETE":
            assert len(item["reason_codes"]) == 1
            assert item["stored_at"] is None and item["record_path"] is None
        else:
            assert item["status"] in {"STORED", "NO_OP"}
            assert item["record_path"] == f"evidence_bundles/{IDENT}.bundle.json"


def test_cli_second_put_is_noop_exit_zero_with_thirteen_sorted_keys(tmp_path, capsys):
    home = _home(tmp_path)
    assert cli.main(["put", "--input", str(E1), "--home", str(home)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(["put", "--input", str(E1), "--home", str(home)]) == 0
    captured = capsys.readouterr()
    second = json.loads(captured.out)
    assert captured.err == "" and captured.out.count("\n") == 1
    assert second["status"] == "NO_OP" and second["stored_at"] == first["stored_at"]
    assert len(second) == 13 and list(second) == sorted(second)


@pytest.mark.parametrize("argv", [[], ["put"], ["verify"], ["list"], ["put", "--input"], ["verify", "--id", IDENT, "--extra"]])
def test_cli_argument_errors_use_fixed_stderr_and_never_touch_storage(tmp_path, capsys, monkeypatch, argv):
    monkeypatch.setattr(cli, "put", lambda *a, **k: pytest.fail("put called"))
    monkeypatch.setattr(cli, "verify", lambda *a, **k: pytest.fail("verify called"))
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == cli._ERROR + "\n"
