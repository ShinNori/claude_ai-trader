"""Adversarial tests for offline lot/event validity evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from aitrader.strict_validity import inspect_strict_validity


SECRET = "secret-validity-evidence-identifier"


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _rehash_input(value):
    for document in value["source_documents"]:
        document["sha256"] = _digest(document["payload"])
    return value


def _input_bundle():
    code = "0001"
    payload = {
        "prices": {code: {
            "code": code, "price_at": "2026-09-11T15:30:00+09:00",
            "selected_basis": "RAW",
            "raw": {"open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000},
            "adjusted": None, "adjustment_contract": None,
        }},
        "publications": {code: {
            "code": code, "publication_at": "2026-09-11T16:30:00+09:00",
            "estimated": False,
        }},
        "lots": {code: {"code": code, "lot_size": 100}},
        "events": {code: {
            "code": code, "next_earnings_at": None, "margin_regulated": False,
        }},
    }
    value = {
        "mode": "strict_input_v1",
        "source_origin": "offline_fixture",
        "as_of": "2026-09-12T06:50:00+09:00",
        "expected_codes": [code],
        "source_documents": [{"id": "input-doc", "sha256": "", "payload": payload}],
        "sources": {
            group: {code: {"document_id": "input-doc", "pointer": f"/{group}/{code}"}}
            for group in ("prices", "publications", "lots", "events")
        },
    }
    return _rehash_input(value)


def _evidence(group, record, doc_id=None):
    payload = {
        "group": group,
        "code": record["code"],
        "record_sha256": _digest(record),
        "valid_from": "2026-09-12T00:00:00+09:00",
        "valid_until": "2026-09-12T07:00:00+09:00",
    }
    return {
        "id": doc_id or f"validity-{group}",
        "sha256": _digest(payload),
        "payload": payload,
    }


def _bundle():
    input_value = _input_bundle()
    payload = input_value["source_documents"][0]["payload"]
    return {
        "mode": "strict_validity_fixture_v1",
        "source_origin": "offline_fixture",
        "input": input_value,
        "validity_documents": [
            _evidence("lots", payload["lots"]["0001"]),
            _evidence("events", payload["events"]["0001"]),
        ],
    }


def _rehash_evidence(value):
    for document in value["validity_documents"]:
        document["sha256"] = _digest(document["payload"])
    return value


def _assert_incomplete(value, reason=None):
    before = deepcopy(value)
    result = inspect_strict_validity(value)
    assert value == before
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == sorted(result["reason_codes"])
    if reason:
        assert reason in result["reason_codes"]
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False
    assert result["read_only"] is True
    return result


def test_valid_evidence_returns_exact_safe_metadata():
    assert inspect_strict_validity(_bundle()) == {
        "mode": "strict_validity_fixture_v1",
        "source_origin": "offline_fixture",
        "scope": "selected_lots_events",
        "status": "VERIFIED_OFFLINE_VALIDITY",
        "expected_evidence_count": 2,
        "verified_evidence_count": 2,
        "reason_codes": [],
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }


@pytest.mark.parametrize(("as_of", "verified", "reason"), [
    ("2026-09-11T23:59:59.999999+09:00", False, "VALIDITY_NOT_YET_EFFECTIVE"),
    ("2026-09-12T00:00:00+09:00", True, None),
    ("2026-09-12T06:59:59.999999+09:00", True, None),
    ("2026-09-12T07:00:00+09:00", False, "VALIDITY_EXPIRED"),
    ("2026-09-12T07:00:00.000001+09:00", False, "VALIDITY_EXPIRED"),
])
def test_validity_interval_is_inclusive_from_and_exclusive_until(as_of, verified, reason):
    value = _bundle()
    value["input"]["as_of"] = as_of
    result = inspect_strict_validity(value)
    expected = "VERIFIED_OFFLINE_VALIDITY" if verified else "DATA_INCOMPLETE"
    assert result["status"] == expected
    assert result["verified_evidence_count"] == (2 if verified else 0)
    if reason:
        assert reason in result["reason_codes"]


def test_equivalent_timezone_instants_are_compared_as_instants():
    value = _bundle()
    for document in value["validity_documents"]:
        document["payload"].update(
            valid_from="2026-09-11T15:00:00+00:00",
            valid_until="2026-09-11T22:00:00+00:00",
        )
    _rehash_evidence(value)
    assert inspect_strict_validity(value)["status"] == "VERIFIED_OFFLINE_VALIDITY"


@pytest.mark.parametrize(("start", "end"), [
    ("2026-09-12T07:00:00+09:00", "2026-09-12T06:00:00+09:00"),
    ("2026-09-12T06:00:00+09:00", "2026-09-12T06:00:00+09:00"),
    ("2026-09-12T00:00:00", "2026-09-12T07:00:00+09:00"),
])
def test_reversed_equal_or_naive_validity_period_is_rejected(start, end):
    value = _bundle()
    value["validity_documents"][0]["payload"].update(valid_from=start, valid_until=end)
    _rehash_evidence(value)
    _assert_incomplete(value, "VALIDITY_PERIOD_INVALID")


@pytest.mark.parametrize("field", ["mode", "source_origin", "input", "validity_documents"])
def test_envelope_requires_every_exact_field(field):
    value = _bundle()
    value.pop(field)
    _assert_incomplete(value, "INVALID_BUNDLE")


def test_fixture_mode_and_origin_are_fixed():
    for field, bad, reason in (
        ("mode", "live", "INVALID_MODE"),
        ("source_origin", "network", "INVALID_ORIGIN"),
    ):
        value = _bundle()
        value[field] = bad
        _assert_incomplete(value, reason)


def test_missing_one_or_all_subjects_is_rejected():
    for remaining in (1, 0):
        value = _bundle()
        value["validity_documents"] = value["validity_documents"][:remaining]
        reason = "VALIDITY_SET_MISMATCH" if remaining else "INVALID_VALIDITY_DOCUMENTS"
        _assert_incomplete(value, reason)


def test_duplicate_evidence_subject_is_rejected():
    value = _bundle()
    duplicate = deepcopy(value["validity_documents"][0])
    duplicate["id"] = "duplicate-subject"
    value["validity_documents"].append(duplicate)
    _assert_incomplete(value, "VALIDITY_SUBJECT_DUPLICATE")


def test_duplicate_document_id_is_rejected():
    value = _bundle()
    value["validity_documents"][1]["id"] = value["validity_documents"][0]["id"]
    _assert_incomplete(value, "INVALID_VALIDITY_DOCUMENTS")


@pytest.mark.parametrize(("field", "bad"), [
    ("group", "prices"), ("code", "9999"), ("code", ""),
])
def test_unknown_group_code_or_blank_code_is_rejected(field, bad):
    value = _bundle()
    value["validity_documents"][0]["payload"][field] = bad
    _rehash_evidence(value)
    _assert_incomplete(value, "VALIDITY_SUBJECT_INVALID")


def test_original_selected_value_change_with_stale_record_hash_cannot_pass():
    value = _bundle()
    input_document = value["input"]["source_documents"][0]
    input_document["payload"]["lots"]["0001"]["lot_size"] = 200
    _rehash_input(value["input"])
    _assert_incomplete(value, "VALIDITY_RECORD_MISMATCH")


def test_bad_original_document_hash_has_input_precedence_and_zero_counts():
    value = _bundle()
    value["input"]["source_documents"][0]["sha256"] = "0" * 64
    result = _assert_incomplete(value, "INPUT_DOCUMENT_HASH_MISMATCH")
    assert result["expected_evidence_count"] == 0
    assert result["verified_evidence_count"] == 0


def test_pointer_hashes_actual_selected_record_not_neighbor():
    value = _bundle()
    payload = value["input"]["source_documents"][0]["payload"]
    payload["lots"]["neighbor"] = {"code": "0001", "lot_size": 999}
    _rehash_input(value["input"])
    assert inspect_strict_validity(value)["status"] == "VERIFIED_OFFLINE_VALIDITY"


def test_evidence_document_hash_mismatch_is_rejected():
    value = _bundle()
    value["validity_documents"][0]["payload"]["valid_until"] = "2026-09-13T00:00:00+09:00"
    _assert_incomplete(value, "VALIDITY_HASH_MISMATCH")


def test_record_hash_must_be_lowercase_hex64_and_match_selected_record():
    for digest in ("A" * 64, "0" * 64, "short", None):
        value = _bundle()
        value["validity_documents"][0]["payload"]["record_sha256"] = digest
        _rehash_evidence(value)
        expected = "VALIDITY_RECORD_MISMATCH" if digest == "0" * 64 else "VALIDITY_SUBJECT_INVALID"
        _assert_incomplete(value, expected)


def test_document_and_payload_shapes_are_exact():
    for target in ("document", "payload"):
        value = _bundle()
        document = value["validity_documents"][0]
        if target == "document":
            document["extra"] = True
        else:
            document["payload"]["extra"] = True
        if target == "payload":
            _rehash_evidence(value)
        _assert_incomplete(value, "INVALID_VALIDITY_DOCUMENTS" if target == "document" else "VALIDITY_SUBJECT_INVALID")


@pytest.mark.parametrize("documents", [None, {}, "documents", [1]])
def test_malformed_validity_document_containers_fail_closed(documents):
    value = _bundle()
    value["validity_documents"] = documents
    _assert_incomplete(value, "INVALID_VALIDITY_DOCUMENTS")


def test_both_source_objects_are_immutable_on_success_and_failure():
    value = _bundle()
    before = deepcopy(value)
    inspect_strict_validity(value)
    assert value == before
    value["validity_documents"][0]["sha256"] = "0" * 64
    _assert_incomplete(value)


@pytest.mark.parametrize("value", [None, 1, True, [], "fixture"])
def test_wrong_top_level_types_fail_closed_without_exception(value):
    result = inspect_strict_validity(value)
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == sorted(result["reason_codes"])
    assert result["ready_for_live"] is False
    assert result["read_only"] is True


def test_failure_metadata_never_echoes_ids_values_codes_or_times():
    value = _bundle()
    value["validity_documents"][0]["id"] = SECRET
    value["validity_documents"][0]["payload"]["valid_until"] = SECRET
    result = _assert_incomplete(value)
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    for raw in (SECRET, "0001", "input-doc", "100", "2026-09-12T06:50:00+09:00"):
        assert raw not in rendered
