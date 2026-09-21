"""Bind verified strict-input price selections to offline coverage."""
from __future__ import annotations

import hashlib

from .strict_coverage import inspect_strict_coverage
from .strict_input import _aware, _canonical, _resolve, inspect_strict_input


MODE = "strict_price_coverage_v1"
ORIGIN = "offline_fixture"
SCOPE = "selected_price_records"


def _result(
    *,
    status: str,
    reasons: list[str],
    expected_page_count: int = 0,
    source_document_count: int = 0,
    expected_record_count: int = 0,
    observed_record_count: int = 0,
) -> dict:
    return {
        "mode": MODE,
        "source_origin": ORIGIN,
        "scope": SCOPE,
        "status": status,
        "expected_page_count": expected_page_count,
        "source_document_count": source_document_count,
        "expected_record_count": expected_record_count,
        "observed_record_count": observed_record_count,
        "reason_codes": sorted(reasons),
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }


def inspect_strict_price_coverage(bundle: object, request: object) -> dict:
    """Validate coverage of the actual selected strict-input price records."""
    input_result = inspect_strict_input(bundle)
    if input_result["status"] != "VERIFIED_OFFLINE_INPUT":
        return _result(
            status="DATA_INCOMPLETE",
            reasons=[f"INPUT_{reason}" for reason in input_result["reason_codes"]],
        )

    # The successful strict-input inspection proves the shapes and references,
    # but timezone conversion and canonical UTF-8 generation can still fail at
    # Python's representable date/Unicode boundaries.
    try:
        data = bundle
        as_of = _aware(data["as_of"])
        documents = {
            document["id"]: document["payload"]
            for document in data["source_documents"]
        }
        rows_by_document: dict[str, list[dict[str, str]]] = {}
        for code in data["expected_codes"]:
            reference = data["sources"]["prices"][code]
            document_id = reference["document_id"]
            record = _resolve(documents[document_id], reference["pointer"])
            price_at = _aware(record["price_at"])
            local_date = price_at.astimezone(as_of.tzinfo).date().isoformat()
            rows_by_document.setdefault(document_id, []).append(
                {"code": code, "date": local_date}
            )

        coverage_documents = []
        for document_id, rows in rows_by_document.items():
            payload = {"page_id": document_id, "rows": rows}
            coverage_documents.append({
                "id": document_id,
                "sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
                "payload": payload,
            })
    except (TypeError, ValueError, OverflowError, RecursionError):
        return _result(
            status="DATA_INCOMPLETE",
            reasons=["INPUT_PRICE_COVERAGE_INVALID"],
        )

    coverage_result = inspect_strict_coverage({
        "mode": "strict_coverage_v1",
        "source_origin": ORIGIN,
        "request": request,
        "source_documents": coverage_documents,
    })
    verified = coverage_result["status"] == "VERIFIED_OFFLINE_COVERAGE"
    return _result(
        status=(
            "VERIFIED_OFFLINE_PRICE_COVERAGE" if verified else "DATA_INCOMPLETE"
        ),
        reasons=[
            f"COVERAGE_{reason}" for reason in coverage_result["reason_codes"]
        ],
        expected_page_count=coverage_result["expected_page_count"],
        source_document_count=coverage_result["source_document_count"],
        expected_record_count=coverage_result["expected_record_count"],
        observed_record_count=coverage_result["observed_record_count"],
    )
