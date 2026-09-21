"""Codex R7-03: multiple-subject product regressions, independent of review tests."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from aitrader.history_validity_binding import inspect_history_validity_binding as inspect


def _hash(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@pytest.fixture
def multi():
    path = Path(__file__).resolve().parents[1] / "examples/history_validity_binding_valid.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    data = bundle["input"]
    original = data["source_documents"][0]
    data["expected_codes"].append("ABCDE")
    for group, records in original["payload"].items():
        records["ABCDE"] = dict(records["0001"], code="ABCDE")
        data["sources"][group]["ABCDE"] = {
            "document_id": original["id"], "pointer": f"/{group}/ABCDE"}
    original["sha256"] = _hash(original["payload"])
    for entry in list(bundle["history"]["entries"]):
        added = copy.deepcopy(entry)
        added["receipt_id"] += "-ABCDE"
        added["revision_id"] += "-ABCDE"
        added["subject"]["code"] = "ABCDE"
        added["payload"]["code"] = "ABCDE"
        added["sha256"] = _hash(added["payload"])
        bundle["history"]["entries"].append(added)
    for document in list(bundle["validity_documents"]):
        added = copy.deepcopy(document)
        added["id"] += "-ABCDE"
        added["payload"]["code"] = "ABCDE"
        added["payload"]["record_sha256"] = _hash(
            original["payload"][added["payload"]["group"]]["ABCDE"])
        added["sha256"] = _hash(added["payload"])
        bundle["validity_documents"].append(added)
    return bundle


def _counts(result, matched, corrected=0):
    assert (result["required_subject_count"], result["matched_subject_count"],
            result["extra_subject_count"], result["corrected_after_as_of_count"]) == (
                4, matched, 0, corrected)
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False
    assert result["selection_sha256"] is not None


@pytest.mark.parametrize("damage_count", [0, 1, 2, 3])
def test_distinct_subject_failures_accumulate_without_double_counting(multi, damage_count):
    expected = []
    if damage_count >= 1:
        # Missing ABCDE/events, differing 0001/lots, expired ABCDE/lots:
        # independent causes must consume precisely one required subject each.
        multi["history"]["entries"] = [e for e in multi["history"]["entries"]
            if e["subject"] != {"group": "events", "code": "ABCDE"}]
        expected.append("HISTORY_SELECTION_MISSING")
    if damage_count >= 2:
        entry = next(e for e in multi["history"]["entries"]
                     if e["subject"] == {"group": "lots", "code": "0001"})
        entry["payload"]["lot_size"] = 200
        entry["sha256"] = _hash(entry["payload"])
        expected.append("ROW_HASH_MISMATCH")
    if damage_count >= 3:
        document = next(d for d in multi["validity_documents"]
                        if d["payload"]["group"] == "lots" and d["payload"]["code"] == "ABCDE")
        document["payload"]["valid_until"] = multi["input"]["as_of"]
        document["sha256"] = _hash(document["payload"])
        expected.append("VALIDITY_NOT_SATISFIED")
    before = copy.deepcopy(multi)
    result = inspect(multi)
    assert result["reason_codes"] == expected
    assert result["status"] == ("DATA_INCOMPLETE" if expected else "VERIFIED_OFFLINE_BINDING")
    _counts(result, 4 - damage_count)
    assert multi == before
    assert inspect(multi) == result


def test_future_corrections_count_subjects_without_replacing_selected_rows(multi):
    baseline = inspect(multi)
    for group, code, repetitions in (("events", "0001", 1), ("lots", "ABCDE", 2)):
        head = next(e for e in multi["history"]["entries"]
                    if e["subject"] == {"group": group, "code": code})
        for index in range(repetitions):
            correction = copy.deepcopy(head)
            correction["revision_id"] += f"-future-{index}"
            correction["receipt_id"] += f"-future-{index}"
            correction["observed_at"] = correction["recorded_at"] = f"2026-09-14T0{index}:00:00+09:00"
            correction["supersedes"] = {"revision_id": head["revision_id"], "sha256": head["sha256"]}
            if group == "lots":
                correction["payload"]["lot_size"] += 100
            else:
                correction["payload"]["margin_regulated"] = True
            correction["sha256"] = _hash(correction["payload"])
            multi["history"]["entries"].append(correction)
            head = correction
    result = inspect(multi)
    _counts(result, 4, corrected=2)
    assert result["reason_codes"] == []
    assert result["status"] == "VERIFIED_OFFLINE_BINDING"
    assert result["selection_sha256"] == baseline["selection_sha256"]


def test_matching_all_four_is_insufficient_with_extra_period_subject(multi):
    extra = copy.deepcopy(multi["validity_documents"][0])
    extra["id"] = "codex-r7-unrequired-period"
    extra["payload"]["code"] = "ZZZZZ"
    extra["sha256"] = _hash(extra["payload"])
    multi["validity_documents"].append(extra)
    result = inspect(multi)
    _counts(result, 4)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert result["status"] == "DATA_INCOMPLETE"
