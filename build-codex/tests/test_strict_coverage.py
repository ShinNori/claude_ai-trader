"""Adversarial acceptance tests for the offline strict-coverage inspector."""
from copy import deepcopy
import hashlib
import json

import pytest

from aitrader.strict_coverage import inspect_strict_coverage


SECRET = "raw-secret-identifier-must-not-leak"


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _rehash(value):
    for document in value["source_documents"]:
        document["sha256"] = hashlib.sha256(
            _canonical(document["payload"])
        ).hexdigest()
    return value


def _bundle():
    value = {
        "mode": "strict_coverage_v1",
        "source_origin": "offline_fixture",
        "request": {
            "start": "2024-02-28",
            "end": "2024-03-01",
            "expected_pages": ["page-b", "page-a"],
            "expected_records": [
                {"code": "7203", "date": "2024-03-01"},
                {"code": "6857", "date": "2024-02-29"},
            ],
        },
        "source_documents": [
            {
                "id": "doc-b",
                "sha256": "",
                "payload": {
                    "page_id": "page-b",
                    "rows": [{"code": "7203", "date": "2024-03-01"}],
                },
            },
            {
                "id": "doc-a",
                "sha256": "",
                "payload": {
                    "page_id": "page-a",
                    "rows": [{"code": "6857", "date": "2024-02-29"}],
                },
            },
        ],
    }
    return _rehash(value)


def _assert_incomplete(value, reason=None):
    before = deepcopy(value)
    result = inspect_strict_coverage(value)
    assert value == before
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == sorted(result["reason_codes"])
    if reason is not None:
        assert reason in result["reason_codes"]
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False
    assert result["read_only"] is True
    return result


def test_valid_coverage_is_order_independent_and_returns_exact_metadata():
    value = _bundle()
    value["request"]["expected_pages"].reverse()
    value["request"]["expected_records"].reverse()
    value["source_documents"].reverse()
    result = inspect_strict_coverage(value)
    assert result == {
        "mode": "strict_coverage_v1",
        "status": "VERIFIED_OFFLINE_COVERAGE",
        "source_origin": "offline_fixture",
        "expected_page_count": 2,
        "source_document_count": 2,
        "expected_record_count": 2,
        "observed_record_count": 2,
        "reason_codes": [],
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }


def test_valid_input_is_not_mutated():
    value = _bundle()
    before = deepcopy(value)
    inspect_strict_coverage(value)
    assert value == before


def test_empty_expected_records_and_explicit_empty_page_are_valid():
    value = _bundle()
    value["request"]["expected_pages"] = ["empty-page"]
    value["request"]["expected_records"] = []
    value["source_documents"] = [{
        "id": "empty-doc", "sha256": "",
        "payload": {"page_id": "empty-page", "rows": []},
    }]
    result = inspect_strict_coverage(_rehash(value))
    assert result["status"] == "VERIFIED_OFFLINE_COVERAGE"
    assert result["expected_record_count"] == 0
    assert result["observed_record_count"] == 0


def test_one_expected_page_may_be_empty_among_nonempty_pages():
    value = _bundle()
    value["source_documents"][0]["payload"]["rows"] = []
    value["request"]["expected_records"] = [
        {"code": "6857", "date": "2024-02-29"}
    ]
    assert inspect_strict_coverage(_rehash(value))["status"] == "VERIFIED_OFFLINE_COVERAGE"


@pytest.mark.parametrize("field", ["mode", "source_origin", "request", "source_documents"])
def test_envelope_requires_every_exact_field(field):
    value = _bundle()
    value.pop(field)
    _assert_incomplete(value, "INVALID_BUNDLE")


def test_envelope_rejects_extra_field():
    value = _bundle()
    value["unexpected"] = True
    _assert_incomplete(value, "INVALID_BUNDLE")


@pytest.mark.parametrize("field", ["start", "end", "expected_pages", "expected_records"])
def test_request_requires_every_exact_field(field):
    value = _bundle()
    value["request"].pop(field)
    _assert_incomplete(value, "INVALID_REQUEST")


@pytest.mark.parametrize(
    "start,end",
    [
        ("2024-02-30", "2024-03-01"),
        ("2023-02-29", "2024-03-01"),
        ("2024-2-28", "2024-03-01"),
        ("2024-02-28T00:00:00Z", "2024-03-01"),
        ("2024-03-02", "2024-03-01"),
        (20240228, "2024-03-01"),
    ],
)
def test_request_dates_are_real_strict_strings_in_inclusive_order(start, end):
    value = _bundle()
    value["request"].update(start=start, end=end)
    _assert_incomplete(value, "INVALID_REQUEST")


def test_equal_start_and_end_is_valid():
    value = _bundle()
    value["request"].update(
        start="2024-02-29",
        end="2024-02-29",
        expected_pages=["page-a"],
        expected_records=[{"code": "6857", "date": "2024-02-29"}],
    )
    value["source_documents"] = [value["source_documents"][1]]
    assert inspect_strict_coverage(value)["status"] == "VERIFIED_OFFLINE_COVERAGE"


@pytest.mark.parametrize("pages", [[], [""], [" page-a"], ["page-a "], ["page-a", "page-a"], "page-a"])
def test_expected_pages_are_a_nonempty_unique_trimmed_string_list(pages):
    value = _bundle()
    value["request"]["expected_pages"] = pages
    _assert_incomplete(value, "INVALID_REQUEST")


@pytest.mark.parametrize(("records", "reason"), [
    (None, "INVALID_REQUEST"),
    ("rows", "INVALID_REQUEST"),
    ([1], "RECORD_INVALID"),
    ([{"code": "6857"}], "RECORD_INVALID"),
    ([{"code": "6857", "date": "2024-02-29", "x": 1}], "RECORD_INVALID"),
])
def test_expected_records_are_a_list_of_exact_records(records, reason):
    value = _bundle()
    value["request"]["expected_records"] = records
    _assert_incomplete(value, reason)


@pytest.mark.parametrize("code", ["", " 6857", "6857 ", 6857, True])
def test_expected_record_code_is_a_nonblank_trimmed_string(code):
    value = _bundle()
    value["request"]["expected_records"][0]["code"] = code
    _assert_incomplete(value, "RECORD_INVALID")


def test_duplicate_expected_record_tuple_is_rejected():
    value = _bundle()
    value["request"]["expected_records"].append(
        deepcopy(value["request"]["expected_records"][0])
    )
    _assert_incomplete(value, "RECORD_DUPLICATE")


@pytest.mark.parametrize("date", ["2024-02-27", "2024-03-02", "2024-02-30", "2024-02-29T00:00:00Z", None])
def test_expected_record_date_must_be_real_and_within_request(date):
    value = _bundle()
    value["request"]["expected_records"][0]["date"] = date
    _assert_incomplete(value, "RECORD_OUT_OF_RANGE" if date in {"2024-02-27", "2024-03-02"} else "RECORD_INVALID")


@pytest.mark.parametrize("documents", [[], {}, "docs", None])
def test_source_documents_must_be_a_nonempty_list(documents):
    value = _bundle()
    value["source_documents"] = documents
    _assert_incomplete(value, "INVALID_DOCUMENTS")


def test_document_and_payload_shapes_are_exact():
    for target in ("document", "payload"):
        value = _bundle()
        if target == "document":
            value["source_documents"][0]["extra"] = 1
        else:
            value["source_documents"][0]["payload"]["extra"] = 1
            _rehash(value)
        _assert_incomplete(value, "INVALID_DOCUMENTS")


@pytest.mark.parametrize("doc_id", ["", " doc-b", "doc-b ", 7, False])
def test_document_id_is_a_nonblank_trimmed_string(doc_id):
    value = _bundle()
    value["source_documents"][0]["id"] = doc_id
    _assert_incomplete(value, "INVALID_DOCUMENTS")


def test_document_ids_must_be_unique():
    value = _bundle()
    value["source_documents"][1]["id"] = value["source_documents"][0]["id"]
    _assert_incomplete(value, "INVALID_DOCUMENTS")


@pytest.mark.parametrize("digest", ["0" * 63, "G" * 64, "A" * 64, 0, None])
def test_sha256_must_be_lowercase_hex64(digest):
    value = _bundle()
    value["source_documents"][0]["sha256"] = digest
    _assert_incomplete(value, "INVALID_DOCUMENTS")


def test_canonical_payload_mutation_without_rehash_is_rejected():
    value = _bundle()
    value["source_documents"][0]["payload"]["rows"][0]["code"] = "9984"
    _assert_incomplete(value, "DOCUMENT_HASH_MISMATCH")


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_observed_page_set_must_exactly_match_expected_pages(mutation):
    value = _bundle()
    if mutation == "missing":
        value["source_documents"].pop()
    elif mutation == "extra":
        value["source_documents"].append({
            "id": "doc-extra", "sha256": "",
            "payload": {"page_id": "page-extra", "rows": []},
        })
        _rehash(value)
    else:
        value["source_documents"][1]["payload"]["page_id"] = "page-b"
        _rehash(value)
    _assert_incomplete(value, "PAGE_DUPLICATE" if mutation == "duplicate" else "PAGE_SET_MISMATCH")


@pytest.mark.parametrize("page_id", ["", " page-b", "page-b ", 1, True])
def test_payload_page_id_is_a_nonblank_trimmed_string(page_id):
    value = _bundle()
    value["source_documents"][0]["payload"]["page_id"] = page_id
    _rehash(value)
    _assert_incomplete(value, "INVALID_DOCUMENTS")


@pytest.mark.parametrize(("rows", "reason"), [
    (None, "INVALID_DOCUMENTS"),
    ({}, "INVALID_DOCUMENTS"),
    ("rows", "INVALID_DOCUMENTS"),
    ([1], "RECORD_INVALID"),
])
def test_payload_rows_must_be_a_list_of_exact_records(rows, reason):
    value = _bundle()
    value["source_documents"][0]["payload"]["rows"] = rows
    _rehash(value)
    _assert_incomplete(value, reason)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate-across-pages", "outside-range"])
def test_observed_record_tuple_set_must_exactly_match_without_duplicates(mutation):
    value = _bundle()
    if mutation == "missing":
        value["source_documents"][0]["payload"]["rows"] = []
    elif mutation == "extra":
        value["source_documents"][0]["payload"]["rows"].append(
            {"code": "9984", "date": "2024-02-29"}
        )
    elif mutation == "duplicate-across-pages":
        value["source_documents"][1]["payload"]["rows"] = deepcopy(
            value["source_documents"][0]["payload"]["rows"]
        )
    else:
        row = value["source_documents"][0]["payload"]["rows"][0]
        row["date"] = "2024-03-02"
    _rehash(value)
    expected_reason = {
        "missing": "RECORD_SET_MISMATCH",
        "extra": "RECORD_SET_MISMATCH",
        "duplicate-across-pages": "RECORD_DUPLICATE",
        "outside-range": "RECORD_OUT_OF_RANGE",
    }[mutation]
    _assert_incomplete(value, expected_reason)


@pytest.mark.parametrize("value", [None, 0, True, "bundle", [], object()])
def test_scalar_and_wrong_container_inputs_fail_closed_without_exception(value):
    result = inspect_strict_coverage(value)
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == sorted(result["reason_codes"])
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False
    assert result["read_only"] is True


def test_failure_metadata_never_echoes_raw_identifiers():
    value = _bundle()
    value["request"]["expected_pages"][0] = SECRET
    value["request"]["expected_records"][0]["code"] = SECRET
    value["source_documents"][0]["id"] = SECRET
    result = _assert_incomplete(value)
    rendered = _canonical(result).decode("utf-8")
    assert SECRET not in rendered
    assert "6857" not in rendered
    assert "7203" not in rendered
    assert "page-a" not in rendered
    assert "doc-a" not in rendered


def test_multiple_failures_are_deterministic_and_reasons_are_sorted():
    value = _bundle()
    value["mode"] = SECRET
    value["source_origin"] = SECRET
    value["request"]["start"] = "bad-date"
    first = inspect_strict_coverage(value)
    second = inspect_strict_coverage(deepcopy(value))
    assert first == second
    assert first["reason_codes"] == sorted(first["reason_codes"])
    assert SECRET not in _canonical(first).decode("utf-8")
