"""Acceptance boundaries for the offline strict-input proof mode."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from aitrader.strict_input import inspect_strict_input


SECRET = "dummy-secret-must-never-be-returned"


@pytest.mark.parametrize("index", ["０", "٠", "00", "+0", "-1", "-", "", "1"])
def test_array_reference_rejects_noncanonical_or_missing_index(index):
    value = bundle()
    value["sources"]["prices"]["6857"]["pointer"] = "/prices/" + index
    assert_incomplete(value, "SOURCE_REFERENCE_INVALID")


def test_each_document_can_reference_its_root_record():
    value = bundle()
    payload = value["source_documents"][0]["payload"]
    value["source_documents"] = [
        {"id": group, "sha256": "", "payload": payload[group][0]}
        for group in value["sources"]
    ]
    for group in value["sources"]:
        value["sources"][group]["6857"] = {"document_id": group, "pointer": ""}
    result = inspect_strict_input(rehash(value))
    assert result["status"] == "VERIFIED_OFFLINE_INPUT"
    assert result["source_document_count"] == 4
    assert result["ready_for_live"] is False


@pytest.mark.parametrize("key,pointer", [("０", "/０"), ("", "/"), ("a/b~c", "/a~1b~0c")])
def test_object_reference_preserves_unicode_empty_and_escaped_keys(key, pointer):
    value = bundle()
    payload = value["source_documents"][0]["payload"]
    payload["prices"] = {key: payload["prices"][0]}
    value["sources"]["prices"]["6857"]["pointer"] = "/prices" + pointer
    assert inspect_strict_input(rehash(value))["status"] == "VERIFIED_OFFLINE_INPUT"


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def rehash(bundle):
    for document in bundle["source_documents"]:
        document["sha256"] = hashlib.sha256(
            canonical(document["payload"]).encode("utf-8")
        ).hexdigest()
    return bundle


def bundle():
    payload = {
        "prices": [{
            "code": "6857", "price_at": "2026-09-12T06:45:00+09:00",
            "selected_basis": "RAW",
            "raw": {"open": 1000, "high": 1050, "low": 990,
                    "close": 1020, "volume": 120000},
            "adjusted": None, "adjustment_contract": None,
        }],
        "publications": [{
            "code": "6857", "publication_at": "2026-09-12T06:30:00+09:00",
            "estimated": False,
        }],
        "lots": [{"code": "6857", "lot_size": 100}],
        "events": [{
            "code": "6857", "next_earnings_at": "2026-10-30T15:00:00+09:00",
            "margin_regulated": False,
        }],
    }
    value = {
        "mode": "strict_input_v1",
        "source_origin": "offline_fixture",
        "as_of": "2026-09-12T07:00:00+09:00",
        "expected_codes": ["6857"],
        "source_documents": [{"id": "market-doc", "sha256": "", "payload": payload}],
        "sources": {
            group: {"6857": {"document_id": "market-doc", "pointer": f"/{group}/0"}}
            for group in ("prices", "publications", "lots", "events")
        },
    }
    return rehash(value)


def assert_incomplete(value, reason):
    result = inspect_strict_input(value)
    assert result["status"] == "DATA_INCOMPLETE"
    assert reason in result["reason_codes"]
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False
    assert result["read_only"] is True
    return result


def test_valid_bundle_returns_metadata_only_and_does_not_mutate_input():
    value = bundle()
    before = deepcopy(value)
    result = inspect_strict_input(value)
    assert value == before
    assert result == {
        "mode": "strict_input_v1",
        "status": "VERIFIED_OFFLINE_INPUT",
        "source_origin": "offline_fixture",
        "as_of": "2026-09-12T07:00:00+09:00",
        "expected_code_count": 1,
        "source_document_count": 1,
        "reason_codes": [],
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }


@pytest.mark.parametrize(("mutation", "reason"), [
    (lambda value: value.pop("mode"), "INVALID_BUNDLE"),
    (lambda value: value.__setitem__("mode", "synthetic"), "INVALID_MODE"),
    (lambda value: value.__setitem__("source_origin", "network"), "INVALID_ORIGIN"),
    (lambda value: value.__setitem__("as_of", "2026-09-12T07:00:00"), "INVALID_AS_OF"),
])
def test_envelope_is_exact_offline_and_time_aware(mutation, reason):
    value = bundle()
    mutation(value)
    assert_incomplete(value, reason)


@pytest.mark.parametrize(("mutation", "reason"), [
    (lambda value: value["source_documents"][0].pop("sha256"), "INVALID_DOCUMENTS"),
    (lambda value: value["source_documents"][0].__setitem__("sha256", "0" * 64),
     "DOCUMENT_HASH_MISMATCH"),
    (lambda value: value["sources"]["prices"]["6857"].__setitem__(
        "document_id", "missing-doc"), "SOURCE_REFERENCE_INVALID"),
    (lambda value: value["sources"]["prices"]["6857"].__setitem__(
        "pointer", "/prices/99"), "SOURCE_REFERENCE_INVALID"),
])
def test_document_hash_and_source_binding_are_mandatory(mutation, reason):
    value = bundle()
    mutation(value)
    assert_incomplete(value, reason)


def test_reference_must_resolve_to_the_same_code():
    value = bundle()
    value["source_documents"][0]["payload"]["prices"][0]["code"] = "7203"
    rehash(value)
    assert_incomplete(value, "SOURCE_RECORD_INVALID")


@pytest.mark.parametrize("document_id", [[], {}])
def test_non_string_reference_document_id_fails_closed(document_id):
    value = bundle()
    value["sources"]["prices"]["6857"]["document_id"] = document_id
    assert_incomplete(value, "SOURCE_REFERENCE_INVALID")


def test_partial_adjusted_ohlcv_is_rejected_even_with_recomputed_hash():
    value = bundle()
    price = value["source_documents"][0]["payload"]["prices"][0]
    price["selected_basis"] = "ADJUSTED"
    price["adjusted"] = {"open": 1000, "high": 1050, "low": 990, "close": 1020}
    price["adjustment_contract"] = "fixture-adjustment-v1"
    rehash(value)
    assert_incomplete(value, "PRICE_ADJUSTMENT_INCOMPLETE")


def test_adjusted_basis_requires_nonempty_contract():
    value = bundle()
    price = value["source_documents"][0]["payload"]["prices"][0]
    price["selected_basis"] = "ADJUSTED"
    price["adjusted"] = deepcopy(price["raw"])
    price["adjustment_contract"] = ""
    rehash(value)
    assert_incomplete(value, "PRICE_BASIS_INVALID")


@pytest.mark.parametrize("field", ["adjusted", "adjustment_contract"])
def test_raw_basis_rejects_mixed_adjusted_claims(field):
    value = bundle()
    price = value["source_documents"][0]["payload"]["prices"][0]
    price[field] = deepcopy(price["raw"]) if field == "adjusted" else "adjusted-v1"
    rehash(value)
    assert_incomplete(value, "PRICE_BASIS_MIXED")


def test_price_observation_after_as_of_is_rejected():
    value = bundle()
    value["source_documents"][0]["payload"]["prices"][0][
        "price_at"] = "2026-09-12T07:00:00.000001+09:00"
    rehash(value)
    assert_incomplete(value, "PRICE_AFTER_AS_OF")


def test_unbounded_json_integer_in_ohlcv_fails_closed():
    value = bundle()
    value["source_documents"][0]["payload"]["prices"][0]["raw"]["open"] = 10 ** 4000
    rehash(value)
    assert_incomplete(value, "SOURCE_RECORD_INVALID")


@pytest.mark.parametrize(("change", "reason"), [
    ({"estimated": True}, "PUBLICATION_ESTIMATED"),
    ({"publication_at": "2026-09-12T07:00:00.000001+09:00"},
     "PUBLICATION_AFTER_AS_OF"),
])
def test_publication_must_be_observed_and_not_from_the_future(change, reason):
    value = bundle()
    value["source_documents"][0]["payload"]["publications"][0].update(change)
    rehash(value)
    assert_incomplete(value, reason)


def test_publication_estimated_flag_must_not_be_missing():
    value = bundle()
    value["source_documents"][0]["payload"]["publications"][0].pop("estimated")
    rehash(value)
    assert_incomplete(value, "SOURCE_RECORD_INVALID")


def test_equal_publication_in_another_timezone_is_allowed():
    value = bundle()
    value["source_documents"][0]["payload"]["publications"][0].update(
        publication_at="2026-09-11T22:00:00+00:00")
    rehash(value)
    assert inspect_strict_input(value)["status"] == "VERIFIED_OFFLINE_INPUT"


@pytest.mark.parametrize("lot", [0, -1, 1.5, True, "100"])
def test_lot_size_is_a_positive_non_boolean_integer(lot):
    value = bundle()
    value["source_documents"][0]["payload"]["lots"][0]["lot_size"] = lot
    rehash(value)
    assert_incomplete(value, "LOT_INVALID")


@pytest.mark.parametrize(("field", "unknown"), [
    ("margin_regulated", "UNKNOWN"),
    ("next_earnings_at", "UNKNOWN"),
])
def test_unknown_event_facts_are_rejected(field, unknown):
    value = bundle()
    value["source_documents"][0]["payload"]["events"][0][field] = unknown
    rehash(value)
    assert_incomplete(value, "EVENT_UNKNOWN")


def test_duplicate_expected_codes_are_rejected():
    value = bundle()
    value["expected_codes"].append("6857")
    assert_incomplete(value, "INVALID_EXPECTED_CODES")


def test_expected_codes_cannot_be_empty():
    value = bundle()
    value["expected_codes"] = []
    assert_incomplete(value, "INVALID_EXPECTED_CODES")


def test_every_source_group_must_exactly_cover_expected_codes():
    value = bundle()
    value["expected_codes"].append("7203")
    assert_incomplete(value, "SOURCE_SET_MISMATCH")


def test_one_document_pointer_cannot_bind_two_selected_facts():
    value = bundle()
    value["sources"]["lots"]["6857"] = deepcopy(
        value["sources"]["prices"]["6857"])
    assert_incomplete(value, "SOURCE_REFERENCE_DUPLICATE")


def test_failure_result_never_echoes_document_values_or_secret_identifiers():
    value = bundle()
    value["source_documents"][0]["id"] = SECRET
    value["source_documents"][0]["sha256"] = "broken"
    result = assert_incomplete(value, "INVALID_DOCUMENTS")
    rendered = canonical(result)
    assert SECRET not in rendered
    assert "6857" not in rendered
    assert "market-doc" not in rendered


def test_invalid_input_is_not_mutated_while_being_rejected():
    value = bundle()
    value["source_documents"][0]["payload"]["events"][0][
        "margin_regulated"] = "UNKNOWN"
    rehash(value)
    before = deepcopy(value)
    assert_incomplete(value, "EVENT_UNKNOWN")
    assert value == before


def test_multiple_failures_have_sorted_deterministic_metadata_only_reasons():
    value = bundle()
    value["mode"] = SECRET
    value["source_origin"] = "network-" + SECRET
    value["as_of"] = "naive-" + SECRET
    first = inspect_strict_input(value)
    second = inspect_strict_input(deepcopy(value))
    assert first == second
    assert first["reason_codes"] == sorted(first["reason_codes"])
    assert first["reason_codes"] == ["INVALID_AS_OF", "INVALID_MODE", "INVALID_ORIGIN"]
    assert SECRET not in canonical(first)
    assert first["ready_for_live"] is False
