"""Binding tests for strict input's selected prices and strict coverage."""
from copy import deepcopy
import hashlib
import json

import pytest

from aitrader.strict_price_coverage import inspect_strict_price_coverage


SECRET = "private-price-document-identifier"


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _rehash(bundle):
    for document in bundle["source_documents"]:
        document["sha256"] = hashlib.sha256(
            _canonical(document["payload"])
        ).hexdigest()
    return bundle


def _price(code="0001", price_at="2026-09-11T15:30:00+09:00"):
    return {
        "code": code,
        "price_at": price_at,
        "selected_basis": "RAW",
        "raw": {"open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000},
        "adjusted": None,
        "adjustment_contract": None,
    }


def _bundle():
    code = "0001"
    payload = {
        "prices": {code: _price()},
        "publications": {code: {
            "code": code, "publication_at": "2026-09-11T16:30:00+09:00",
            "estimated": False,
        }},
        "lots": {code: {"code": code, "lot_size": 100}},
        "events": {code: {
            "code": code, "next_earnings_at": None, "margin_regulated": False,
        }},
    }
    bundle = {
        "mode": "strict_input_v1",
        "source_origin": "offline_fixture",
        "as_of": "2026-09-12T06:50:00+09:00",
        "expected_codes": [code],
        "source_documents": [{"id": "market-doc", "sha256": "", "payload": payload}],
        "sources": {
            group: {code: {"document_id": "market-doc", "pointer": f"/{group}/{code}"}}
            for group in ("prices", "publications", "lots", "events")
        },
    }
    return _rehash(bundle)


def _request(pages=None, records=None):
    return {
        "start": "2026-09-11",
        "end": "2026-09-11",
        "expected_pages": ["market-doc"] if pages is None else pages,
        "expected_records": (
            [{"code": "0001", "date": "2026-09-11"}]
            if records is None else records
        ),
    }


def _assert_incomplete(bundle, request, reason=None):
    before_bundle = deepcopy(bundle)
    before_request = deepcopy(request)
    result = inspect_strict_price_coverage(bundle, request)
    assert bundle == before_bundle
    assert request == before_request
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == sorted(result["reason_codes"])
    if reason:
        assert reason in result["reason_codes"]
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False
    assert result["read_only"] is True
    return result


def _add_code(bundle, code="0002", price_at="2026-09-11T14:30:00+09:00"):
    bundle["expected_codes"].append(code)
    document = bundle["source_documents"][0]
    payload = document["payload"]
    payload["prices"][code] = _price(code, price_at)
    payload["publications"][code] = {
        "code": code, "publication_at": "2026-09-11T16:00:00+09:00",
        "estimated": False,
    }
    payload["lots"][code] = {"code": code, "lot_size": 100}
    payload["events"][code] = {
        "code": code, "next_earnings_at": None, "margin_regulated": False,
    }
    for group in bundle["sources"]:
        bundle["sources"][group][code] = {
            "document_id": document["id"], "pointer": f"/{group}/{code}",
        }
    return _rehash(bundle)


def test_valid_binding_returns_exact_safe_metadata():
    result = inspect_strict_price_coverage(_bundle(), _request())
    assert result == {
        "mode": "strict_price_coverage_v1",
        "source_origin": "offline_fixture",
        "scope": "selected_price_records",
        "status": "VERIFIED_OFFLINE_PRICE_COVERAGE",
        "expected_page_count": 1,
        "source_document_count": 1,
        "expected_record_count": 1,
        "observed_record_count": 1,
        "reason_codes": [],
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }


def test_both_inputs_remain_unchanged_on_success_and_failure():
    bundle, request = _bundle(), _request()
    before_bundle, before_request = deepcopy(bundle), deepcopy(request)
    inspect_strict_price_coverage(bundle, request)
    assert (bundle, request) == (before_bundle, before_request)
    request["expected_pages"] = ["wrong-page"]
    _assert_incomplete(bundle, request)


def test_rehashed_price_date_change_rejects_stale_request():
    bundle = _bundle()
    bundle["source_documents"][0]["payload"]["prices"]["0001"][
        "price_at"
    ] = "2026-09-12T00:01:00+09:00"
    bundle["as_of"] = "2026-09-12T06:50:00+09:00"
    result = _assert_incomplete(_rehash(bundle), _request())
    assert any(reason.startswith("COVERAGE_") for reason in result["reason_codes"])


def test_rehashed_selected_price_code_change_cannot_satisfy_request():
    bundle = _bundle()
    bundle["source_documents"][0]["payload"]["prices"]["0001"]["code"] = "9999"
    result = _assert_incomplete(_rehash(bundle), _request())
    assert any(reason.startswith("INPUT_") for reason in result["reason_codes"])
    assert result["observed_record_count"] == 0


def test_original_document_hash_mismatch_never_reaches_coverage_success():
    bundle = _bundle()
    bundle["source_documents"][0]["payload"]["prices"]["0001"]["raw"]["close"] = 106
    result = _assert_incomplete(bundle, _request(), "INPUT_DOCUMENT_HASH_MISMATCH")
    assert result["expected_page_count"] == 0
    assert result["observed_record_count"] == 0


@pytest.mark.parametrize("coverage_request", [None, {}, {"start": "2026-09-11"}, {
    "start": "2026-09-11", "end": "2026-09-11",
    "expected_pages": ["market-doc"], "expected_records": [], "extra": True,
}])
def test_invalid_missing_or_extra_request_is_coverage_prefixed(coverage_request):
    result = _assert_incomplete(_bundle(), coverage_request)
    assert result["reason_codes"]
    assert all(reason.startswith("COVERAGE_") for reason in result["reason_codes"])


def test_requested_fake_document_page_is_rejected():
    result = _assert_incomplete(_bundle(), _request(pages=["fake-doc"]))
    assert "COVERAGE_PAGE_SET_MISMATCH" in result["reason_codes"]


def test_selected_price_outside_requested_date_range_is_rejected():
    request = _request(records=[{"code": "0001", "date": "2026-09-12"}])
    request.update(start="2026-09-12", end="2026-09-12")
    result = _assert_incomplete(_bundle(), request)
    assert any(reason.startswith("COVERAGE_") for reason in result["reason_codes"])


def test_two_codes_in_one_original_document_form_one_page_and_two_rows():
    bundle = _add_code(_bundle())
    request = _request(records=[
        {"code": "0002", "date": "2026-09-11"},
        {"code": "0001", "date": "2026-09-11"},
    ])
    result = inspect_strict_price_coverage(bundle, request)
    assert result["status"] == "VERIFIED_OFFLINE_PRICE_COVERAGE"
    assert result["expected_page_count"] == 1
    assert result["source_document_count"] == 1
    assert result["observed_record_count"] == 2


def test_two_codes_split_across_price_documents_form_two_pages():
    bundle = _add_code(_bundle())
    main = bundle["source_documents"][0]
    second_price = main["payload"]["prices"].pop("0002")
    bundle["source_documents"].append({
        "id": "second-price-doc", "sha256": "", "payload": {"selected": second_price},
    })
    bundle["sources"]["prices"]["0002"] = {
        "document_id": "second-price-doc", "pointer": "/selected",
    }
    _rehash(bundle)
    request = _request(
        pages=["second-price-doc", "market-doc"],
        records=[
            {"code": "0001", "date": "2026-09-11"},
            {"code": "0002", "date": "2026-09-11"},
        ],
    )
    result = inspect_strict_price_coverage(bundle, request)
    assert result["status"] == "VERIFIED_OFFLINE_PRICE_COVERAGE"
    assert result["source_document_count"] == 2


def test_unused_valid_original_document_is_not_a_selected_price_page():
    bundle = _bundle()
    bundle["source_documents"].append({
        "id": "unused-evidence", "sha256": "", "payload": {"note": "valid but unselected"},
    })
    _rehash(bundle)
    result = inspect_strict_price_coverage(bundle, _request())
    assert result["status"] == "VERIFIED_OFFLINE_PRICE_COVERAGE"
    assert result["source_document_count"] == 1
    assert result["expected_page_count"] == 1


def test_unused_original_document_with_bad_hash_invalidates_input():
    bundle = _bundle()
    bundle["source_documents"].append({
        "id": "unused-bad-evidence",
        "sha256": "0" * 64,
        "payload": {"note": "unselected but still must validate"},
    })
    result = _assert_incomplete(bundle, _request(), "INPUT_DOCUMENT_HASH_MISMATCH")
    assert result["expected_page_count"] == 0
    assert result["source_document_count"] == 0
    assert result["observed_record_count"] == 0


def test_price_pointer_selects_target_instead_of_neighboring_row():
    bundle = _bundle()
    prices = bundle["source_documents"][0]["payload"]["prices"]
    prices["neighbor"] = _price("0001", "2026-09-10T15:30:00+09:00")
    _rehash(bundle)
    assert inspect_strict_price_coverage(bundle, _request())["status"] == "VERIFIED_OFFLINE_PRICE_COVERAGE"
    stale_neighbor_request = _request(
        records=[{"code": "0001", "date": "2026-09-10"}]
    )
    stale_neighbor_request.update(start="2026-09-10", end="2026-09-10")
    _assert_incomplete(bundle, stale_neighbor_request)


@pytest.mark.parametrize("price_at", [
    "2026-09-11T22:30:00+00:00",
    "2026-09-11T15:30:00-07:00",
])
def test_price_date_is_normalized_to_as_of_timezone_for_equivalent_instants(price_at):
    bundle = _bundle()
    bundle["as_of"] = "2026-09-12T08:00:00+09:00"
    bundle["source_documents"][0]["payload"]["prices"]["0001"]["price_at"] = price_at
    _rehash(bundle)
    request = _request(records=[{"code": "0001", "date": "2026-09-12"}])
    request.update(start="2026-09-12", end="2026-09-12")
    assert inspect_strict_price_coverage(bundle, request)["status"] == "VERIFIED_OFFLINE_PRICE_COVERAGE"


def test_invalid_input_precedes_coverage_and_zeroes_all_counts():
    bundle = _bundle()
    bundle["mode"] = "wrong-input-mode"
    result = _assert_incomplete(bundle, _request(pages=["also-wrong"]))
    assert any(reason.startswith("INPUT_") for reason in result["reason_codes"])
    assert not any(reason.startswith("COVERAGE_") for reason in result["reason_codes"])
    assert result["expected_page_count"] == 0
    assert result["source_document_count"] == 0
    assert result["expected_record_count"] == 0
    assert result["observed_record_count"] == 0


@pytest.mark.parametrize("bundle", [None, 1, [], "input"])
def test_wrong_input_container_fails_closed_without_exception(bundle):
    result = inspect_strict_price_coverage(bundle, _request())
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == sorted(result["reason_codes"])
    assert any(reason.startswith("INPUT_") for reason in result["reason_codes"])
    assert result["observed_record_count"] == 0


def test_failure_metadata_never_echoes_codes_documents_or_price_values():
    bundle = _bundle()
    bundle["source_documents"][0]["id"] = SECRET
    bundle["sources"]["prices"]["0001"]["document_id"] = SECRET
    request = _request(pages=[SECRET], records=[{"code": SECRET, "date": "2026-09-11"}])
    result = _assert_incomplete(bundle, request)
    rendered = _canonical(result).decode("utf-8")
    for raw in (SECRET, "0001", "market-doc", "105"):
        assert raw not in rendered


def test_json_roundtrip_timezone_conversion_underflow_fails_closed():
    bundle = _bundle()
    bundle["as_of"] = "0001-01-01T00:30:00-23:00"
    payload = bundle["source_documents"][0]["payload"]
    payload["prices"]["0001"]["price_at"] = "0001-01-01T00:00:00+23:00"
    payload["publications"]["0001"]["publication_at"] = "0001-01-01T00:00:00+00:00"
    bundle = json.loads(json.dumps(_rehash(bundle), ensure_ascii=True))
    result = _assert_incomplete(bundle, _request(), "INPUT_PRICE_COVERAGE_INVALID")
    assert result["expected_page_count"] == 0
    assert result["source_document_count"] == 0
    assert result["expected_record_count"] == 0
    assert result["observed_record_count"] == 0


def test_json_roundtrip_unpaired_surrogate_document_id_fails_closed():
    bundle = _bundle()
    surrogate_id = "doc-\ud800"
    bundle["source_documents"][0]["id"] = surrogate_id
    for group in bundle["sources"].values():
        group["0001"]["document_id"] = surrogate_id
    bundle = json.loads(json.dumps(bundle, ensure_ascii=True))
    result = _assert_incomplete(bundle, _request(), "INPUT_PRICE_COVERAGE_INVALID")
    assert result["expected_page_count"] == 0
    assert result["source_document_count"] == 0
    assert result["expected_record_count"] == 0
    assert result["observed_record_count"] == 0
