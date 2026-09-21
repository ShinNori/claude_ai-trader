"""Adversarial tests for offline J-Quants V2 pagination-chain fixtures."""
from copy import deepcopy
import json

import pytest

from aitrader.jquants_v2_page_chain import inspect_v2_page_chain


SECRET = "secret-pagination-token-must-not-leak"


def _row(day="2026-08-31", code="13000"):
    return {
        "Date": day, "Code": code, "O": 100, "H": 110.5, "L": 90,
        "C": 105, "Vo": 10000, "Va": 1000000, "AdjFactor": 1.0,
        "AdjO": 100, "AdjH": 110.5, "AdjL": 90, "AdjC": 105,
        "AdjVo": 10000,
    }


def _page(request_token, rows, response_token="__OMIT__", query=None):
    response = {"data": rows}
    if response_token != "__OMIT__":
        response["pagination_key"] = response_token
    return {
        "query": {"code": "13000", "from": "2026-08-01"} if query is None else query,
        "request_token": request_token,
        "response": response,
    }


def _bundle():
    return {
        "mode": "v2_page_chain_fixture_v1",
        "dataset": "prices",
        "pages": [
            _page(None, [_row("2026-08-29")], "token-one"),
            _page("token-one", [_row("2026-08-30")], "token-two"),
            _page("token-two", [_row("2026-08-31")]),
        ],
    }


def _assert_incomplete(value, reason=None):
    before = deepcopy(value)
    result = inspect_v2_page_chain(value)
    assert value == before
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == sorted(result["reason_codes"])
    if reason:
        assert reason in result["reason_codes"]
    assert result["fixture_only"] is True
    assert result["consistent_snapshot_verified"] is False
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False
    assert result["read_only"] is True
    return result


def test_complete_three_page_chain_returns_exact_metadata():
    assert inspect_v2_page_chain(_bundle()) == {
        "mode": "v2_page_chain_fixture_v1",
        "dataset": "prices",
        "status": "VERIFIED_OFFLINE_PAGE_CHAIN",
        "page_count": 3,
        "row_count": 3,
        "reason_codes": [],
        "fixture_only": True,
        "consistent_snapshot_verified": False,
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }


def test_single_empty_terminal_page_is_valid():
    value = _bundle()
    value["pages"] = [_page(None, [])]
    result = inspect_v2_page_chain(value)
    assert result["status"] == "VERIFIED_OFFLINE_PAGE_CHAIN"
    assert result["page_count"] == 1
    assert result["row_count"] == 0


def test_input_is_not_mutated_on_success_or_failure():
    value = _bundle()
    before = deepcopy(value)
    inspect_v2_page_chain(value)
    assert value == before
    value["pages"][1]["request_token"] = "wrong"
    _assert_incomplete(value)


@pytest.mark.parametrize("field", ["mode", "dataset", "pages"])
def test_exact_envelope_requires_every_field(field):
    value = _bundle()
    value.pop(field)
    _assert_incomplete(value, "INVALID_BUNDLE")


def test_exact_envelope_rejects_extra_field():
    value = _bundle()
    value["source"] = "network"
    _assert_incomplete(value, "INVALID_BUNDLE")


def test_unsupported_dataset_is_rejected_and_not_echoed():
    value = _bundle()
    value["dataset"] = SECRET
    result = _assert_incomplete(value, "INVALID_DATASET")
    assert result["dataset"] is None
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("pages", [[], None, {}, "pages"])
def test_pages_must_be_a_nonempty_array(pages):
    value = _bundle()
    value["pages"] = pages
    _assert_incomplete(value, "INVALID_PAGES")


def test_page_requires_exact_query_request_token_response_fields():
    for mutation in ("missing", "extra"):
        value = _bundle()
        if mutation == "missing":
            value["pages"][0].pop("query")
        else:
            value["pages"][0]["extra"] = True
        _assert_incomplete(value, "INVALID_PAGE")


def test_missing_middle_page_breaks_request_token_continuity():
    value = _bundle()
    value["pages"].pop(1)
    _assert_incomplete(value, "REQUEST_TOKEN_MISMATCH")


def test_request_token_mismatch_is_rejected():
    value = _bundle()
    value["pages"][1]["request_token"] = "not-token-one"
    _assert_incomplete(value, "REQUEST_TOKEN_MISMATCH")


def test_swapped_page_order_is_rejected():
    value = _bundle()
    value["pages"][0], value["pages"][1] = value["pages"][1], value["pages"][0]
    _assert_incomplete(value, "REQUEST_TOKEN_MISMATCH")


def test_query_value_drift_is_rejected():
    value = _bundle()
    value["pages"][1]["query"]["code"] = "99999"
    _assert_incomplete(value, "QUERY_CHANGED")


def test_canonical_query_key_order_is_accepted():
    value = _bundle()
    value["pages"][1]["query"] = {"from": "2026-08-01", "code": "13000"}
    assert inspect_v2_page_chain(value)["status"] == "VERIFIED_OFFLINE_PAGE_CHAIN"


def test_pagination_key_is_forbidden_inside_query():
    value = _bundle()
    value["pages"][0]["query"]["pagination_key"] = "bypass"
    _assert_incomplete(value, "INVALID_QUERY")


def test_repeated_returned_token_cycle_is_rejected():
    value = _bundle()
    value["pages"][1]["response"]["pagination_key"] = "token-one"
    value["pages"][2]["request_token"] = "token-one"
    _assert_incomplete(value, "CONTINUATION_CYCLE")


def test_final_page_still_claiming_more_is_rejected():
    value = _bundle()
    value["pages"][-1]["response"]["pagination_key"] = "unfetched-token"
    _assert_incomplete(value, "MISSING_TERMINAL")


def test_earlier_terminal_page_followed_by_more_is_rejected():
    value = _bundle()
    value["pages"][0]["response"].pop("pagination_key")
    value["pages"][1]["request_token"] = None
    _assert_incomplete(value, "EARLY_TERMINAL")


@pytest.mark.parametrize("token", [None, ""])
def test_explicit_null_or_empty_response_token_is_invalid(token):
    value = _bundle()
    value["pages"][-1]["response"]["pagination_key"] = token
    _assert_incomplete(value, "INVALID_CONTINUATION_TOKEN")


def test_bad_shape_on_final_page_counts_only_valid_shape_rows():
    value = _bundle()
    value["pages"][-1]["response"]["data"][0].pop("AdjC")
    result = _assert_incomplete(value, "INVALID_RESPONSE")
    assert result["page_count"] == 3
    assert result["row_count"] == 2


@pytest.mark.parametrize("value", [None, 7, True, [], "chain"])
def test_unsupported_top_level_types_fail_closed(value):
    result = inspect_v2_page_chain(value)
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["dataset"] is None
    assert result["reason_codes"] == sorted(result["reason_codes"])
    assert result["ready_for_live"] is False
    assert result["read_only"] is True


def test_failure_result_does_not_echo_query_rows_or_tokens():
    value = _bundle()
    value["pages"][0]["response"]["pagination_key"] = SECRET
    value["pages"][1]["request_token"] = "different-secret"
    result = _assert_incomplete(value)
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    for raw in (SECRET, "different-secret", "13000", "2026-08-29"):
        assert raw not in rendered
