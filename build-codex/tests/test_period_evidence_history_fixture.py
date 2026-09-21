"""Independent contract tests for artificial period histories; no external I/O."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

import aitrader.period_evidence_history_fixture as module

MODE = "period_evidence_history_fixture_v1"
COUNTS = ("receipt_count", "revision_count", "noop_receipt_count", "series_count",
          "selected_series_count", "future_revision_count")
KEYS = {"mode", "source_origin", "status", "reason_codes", *COUNTS,
        "selection_sha256", "ready_for_live", "current_signal", "read_only"}
EMPTY = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def entry(receipt="receipt-1", revision="revision-1", series="series-1", *, code="0001"):
    payload = {"group": "lots", "code": code, "record_sha256": "1" * 64,
               "valid_from": "2026-09-10T00:00:00+09:00",
               "valid_until": "2026-09-14T00:00:00+09:00"}
    return {"receipt_id": receipt, "revision_id": revision, "series_id": series,
            "subject": {"group": "lots", "code": code}, "payload": payload,
            "sha256": digest(payload), "supersedes": None,
            "observed_at": "2026-09-11T00:00:00+09:00",
            "recorded_at": "2026-09-11T01:00:00+09:00"}


def bundle(*entries):
    return {"mode": MODE, "source_origin": "offline_fixture",
            "decision_at": "2026-09-12T06:50:00+09:00", "entries": list(entries or [entry()])}


def rehash(item):
    item["sha256"] = digest(item["payload"])
    return item


def correction(first):
    second = deepcopy(first)
    second.update(receipt_id="receipt-2", revision_id="revision-2",
                  recorded_at="2026-09-12T01:00:00+09:00",
                  supersedes={"revision_id": first["revision_id"], "sha256": first["sha256"]})
    second["payload"]["valid_until"] = "2026-09-15T00:00:00+09:00"
    return rehash(second)


def inspect(value):
    before = deepcopy(value)
    result = module.inspect_period_evidence_history(value)
    assert value == before
    assert set(result) == KEYS
    assert result["mode"] == MODE and result["source_origin"] == "offline_fixture"
    assert result["ready_for_live"] is False and result["current_signal"] is False
    assert result["read_only"] is True
    return result


def rejected(value, reason):
    result = inspect(value)
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == [reason]
    assert all(result[key] == 0 for key in COUNTS)
    assert result["selection_sha256"] is None


def success(value):
    result = inspect(value)
    assert result["status"] == "VERIFIED_OFFLINE_PERIOD_HISTORY"
    assert result["reason_codes"] == []
    return result


def test_review_example_literal_fourteen_key_output():
    value = json.loads((Path(__file__).parents[1] / "examples/period_evidence_history_valid.json").read_text(encoding="utf-8"))
    assert inspect(value) == {
        "mode": MODE, "source_origin": "offline_fixture", "status": "VERIFIED_OFFLINE_PERIOD_HISTORY",
        "reason_codes": [], "receipt_count": 3, "revision_count": 3, "noop_receipt_count": 0,
        "series_count": 2, "selected_series_count": 2, "future_revision_count": 1,
        "selection_sha256": "ba6486dd33d76cf9594f6e43795f45c70349bee87e40fa6f13e0abcb01d186ca",
        "ready_for_live": False, "current_signal": False, "read_only": True}


@pytest.mark.parametrize("field,value,reason", [
    ("extra", True, "INVALID_BUNDLE"), ("mode", "other", "INVALID_MODE"),
    ("source_origin", "live", "INVALID_ORIGIN"), ("decision_at", "yesterday", "INVALID_DECISION_AT"),
    ("decision_at", "2026-09-12T06:50:00", "INVALID_DECISION_AT"),
    ("entries", [], "INVALID_ENTRIES"), ("entries", {}, "INVALID_ENTRIES")])
def test_envelope_reasons(field, value, reason):
    data = bundle(); data[field] = value
    rejected(data, reason)


def test_non_mapping_bundle():
    rejected([], "INVALID_BUNDLE")


@pytest.mark.parametrize("field,value,reason", [
    ("extra", True, "INVALID_ENTRY"), ("receipt_id", "", "INVALID_IDENTIFIER"),
    ("revision_id", " trailing ", "INVALID_IDENTIFIER"), ("series_id", None, "INVALID_IDENTIFIER"),
    ("subject", {"group": "lots", "code": " "}, "INVALID_SUBJECT"),
    ("subject", {"group": "prices", "code": "0001"}, "INVALID_SUBJECT"),
    ("observed_at", "2026-09-11T00:00:00", "INVALID_TIMESTAMPS"),
    ("observed_at", "2026-09-13T00:00:00+09:00", "INVALID_TIMESTAMPS"),
    ("sha256", "A" * 64, "INVALID_HASH"), ("sha256", "0" * 64, "PAYLOAD_HASH_MISMATCH"),
    ("supersedes", {"revision_id": "unknown", "sha256": "Z" * 64}, "INVALID_SUPERSEDES")])
def test_entry_reason_mapping(field, value, reason):
    item = entry(); item[field] = value
    rejected(bundle(item), reason)


@pytest.mark.parametrize("field,value", [("extra", 1), ("record_sha256", "A" * 64),
                                         ("code", "0002"), ("group", "events")])
def test_invalid_period_payload_shapes(field, value):
    item = entry(); item["payload"][field] = value
    rejected(bundle(rehash(item)), "INVALID_PAYLOAD")


@pytest.mark.parametrize("field,value", [("valid_from", "nonsense"),
    ("valid_from", "2026-09-10T00:00:00"), ("valid_from", "2026-09-14T00:00:00+09:00"),
    ("valid_until", "2026-09-09T00:00:00+09:00")])
def test_invalid_period_intervals(field, value):
    item = entry(); item["payload"][field] = value
    rejected(bundle(rehash(item)), "PERIOD_INTERVAL_INVALID")


def test_noop_precedes_reverse_time_and_keeps_unique_counts():
    first = entry(); second = correction(first)
    result = success(bundle(first, second, deepcopy(first)))
    assert [result[k] for k in COUNTS] == [2, 2, 1, 1, 1, 0]


def test_receipt_conflict_precedes_subject_and_time_validation():
    first = entry(); again = deepcopy(first)
    again["subject"] = {}; again["recorded_at"] = "invalid"
    rejected(bundle(first, again), "RECEIPT_CONFLICT")


def test_receipt_time_spelling_remains_significant():
    first = entry(); again = deepcopy(first)
    again["recorded_at"] = "2026-09-10T16:00:00Z"
    rejected(bundle(first, again), "RECEIPT_CONFLICT")


def test_reverse_new_receipt_is_rejected():
    first = entry(); second = correction(first)
    second["recorded_at"] = first["observed_at"]
    rejected(bundle(first, second), "RECORDED_AT_REVERSED")


def test_revision_conflict_precedes_series_subject_check():
    first = entry(); second = entry("receipt-2", "revision-1", "series-1", code="0002")
    rejected(bundle(first, second), "REVISION_CONFLICT")


def test_series_subject_is_fixed():
    first = entry(); second = correction(first)
    second["subject"]["code"] = second["payload"]["code"] = "0002"
    rejected(bundle(first, rehash(second)), "SERIES_REFERENCE_INVALID")


def test_cross_series_reference_is_distinct_reason():
    first = entry(); second = correction(first); second["series_id"] = "other"
    rejected(bundle(first, second), "SERIES_REFERENCE_INVALID")


@pytest.mark.parametrize("case", ["unknown", "self", "second_initial", "wrong_hash", "old_head"])
def test_invalid_linear_references(case):
    first = entry(); second = correction(first); entries = [first, second]
    if case == "unknown": second["supersedes"]["revision_id"] = "unknown"
    elif case == "self": second["supersedes"]["revision_id"] = second["revision_id"]
    elif case == "second_initial": second["supersedes"] = None
    elif case == "wrong_hash": second["supersedes"]["sha256"] = "0" * 64
    else:
        third = deepcopy(second); third.update(receipt_id="receipt-3", revision_id="revision-3")
        entries.append(third)
    rejected(bundle(*entries), "INVALID_SUPERSEDES")


def test_old_revision_reacquisition_does_not_rewind_selection():
    first = entry(); second = correction(first); again = deepcopy(first)
    again.update(receipt_id="receipt-3", recorded_at="2026-09-13T00:00:00+09:00")
    original = success(bundle(first, second)); result = success(bundle(first, second, again))
    assert result["selection_sha256"] == original["selection_sha256"]
    assert [result[k] for k in COUNTS] == [3, 2, 0, 1, 1, 0]


def test_two_active_series_same_subject_fail_even_same_payload():
    first = entry(); second = entry("receipt-2", "revision-2", "series-2")
    rejected(bundle(first, second), "PERIOD_OVERLAP_AT_DECISION")


def test_outside_decision_overlap_is_allowed_and_counts_all_selected():
    first = entry(); second = entry("receipt-2", "revision-2", "series-2")
    second["payload"]["valid_until"] = "2026-09-12T06:50:00+09:00"
    result = success(bundle(first, rehash(second)))
    assert result["selected_series_count"] == 2
    assert result["selection_sha256"] == success(bundle(first))["selection_sha256"]


def test_expired_correction_does_not_fallback_to_valid_old_revision():
    first = entry(); second = correction(first)
    second["payload"]["valid_until"] = "2026-09-12T06:50:00+09:00"
    result = success(bundle(first, rehash(second)))
    assert result["selected_series_count"] == 1 and result["selection_sha256"] == EMPTY


def test_future_only_revision_is_not_selected_despite_old_observation():
    item = entry(); item["recorded_at"] = "2026-09-13T00:00:00+09:00"
    result = success(bundle(item))
    assert result["selected_series_count"] == 0 and result["future_revision_count"] == 1
    assert result["selection_sha256"] == EMPTY


def test_decision_equality_includes_record_and_interval_start():
    item = entry(); item["recorded_at"] = item["payload"]["valid_from"] = bundle()["decision_at"]
    result = success(bundle(rehash(item)))
    assert result["selected_series_count"] == 1 and result["selection_sha256"] != EMPTY


def test_future_invalid_entry_precedes_overlap_evaluation():
    first = entry(); second = entry("receipt-2", "revision-2", "series-2")
    future = entry("receipt-3", "revision-3", "series-3")
    future["recorded_at"] = "2026-09-13T00:00:00+09:00"; future["sha256"] = "0" * 64
    rejected(bundle(first, second, future), "PAYLOAD_HASH_MISMATCH")


@pytest.mark.parametrize("identifier", ["リビジョン-001", "revision 001", "_leading", "r" * 129, "r\u200b1"])
def test_standalone_revision_ids_do_not_adopt_binding_ascii_constraint(identifier):
    item = entry(revision=identifier)
    success(bundle(item))


def test_digest_uses_payload_self_hash_and_stable_subject_order():
    first = entry(code="9999"); second = entry("receipt-2", "revision-2", "series-2", code="0001")
    expected = digest([{**item["subject"], "series_id": item["series_id"],
                        "revision_id": item["revision_id"], "sha256": item["sha256"]}
                       for item in [second, first]])
    assert success(bundle(first, second))["selection_sha256"] == expected
    assert success(bundle(second, first))["selection_sha256"] == expected


def test_output_never_exposes_identifiers_or_payload_and_is_fresh():
    item = entry(receipt="private-receipt-marker", revision="private-revision-marker", series="private-series-marker")
    value = bundle(item); output = success(value)
    assert "private-" not in json.dumps(output)
    output["reason_codes"].append("injected")
    assert success(value)["reason_codes"] == []


def test_selection_digest_failure_is_fixed_refusal(monkeypatch):
    original = module._digest
    monkeypatch.setattr(module, "_digest", lambda value: None if isinstance(value, list) else original(value))
    rejected(bundle(), "SELECTION_HASH_INVALID")
