"""Pure, offline validation for a strict coverage manifest."""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re
from typing import Any


MODE = "strict_coverage_v1"
ORIGIN = "offline_fixture"
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Any) -> bytes:
    """Match strict_input's canonical JSON representation without doing I/O."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _trimmed(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _record(
    value: Any,
    start: date | None,
    end: date | None,
    reasons: set[str],
) -> tuple[str, str] | None:
    if not isinstance(value, dict) or set(value) != {"code", "date"}:
        reasons.add("RECORD_INVALID")
        return None
    code = value.get("code")
    raw_date = value.get("date")
    parsed = _date(raw_date)
    if not _trimmed(code) or parsed is None:
        reasons.add("RECORD_INVALID")
        return None
    if start is not None and end is not None and not start <= parsed <= end:
        reasons.add("RECORD_OUT_OF_RANGE")
        return None
    return code, raw_date


def inspect_strict_coverage(bundle: object) -> dict:
    """Validate an in-memory coverage bundle and return metadata only."""
    reasons: set[str] = set()
    data = bundle if isinstance(bundle, dict) else {}
    if not isinstance(bundle, dict):
        reasons.add("INVALID_BUNDLE")
    elif set(data) != {"mode", "source_origin", "request", "source_documents"}:
        reasons.add("INVALID_BUNDLE")

    if data.get("mode") != MODE:
        reasons.add("INVALID_MODE")
    if data.get("source_origin") != ORIGIN:
        reasons.add("INVALID_ORIGIN")

    start: date | None = None
    end: date | None = None
    expected_pages: list[str] = []
    expected_records: set[tuple[str, str]] = set()
    request = data.get("request")
    if not isinstance(request, dict) or set(request) != {
        "start", "end", "expected_pages", "expected_records"
    }:
        reasons.add("INVALID_REQUEST")
    else:
        start = _date(request.get("start"))
        end = _date(request.get("end"))
        if start is None or end is None or start > end:
            reasons.add("INVALID_REQUEST")

        pages = request.get("expected_pages")
        if (
            not isinstance(pages, list)
            or not pages
            or any(not _trimmed(page) for page in pages)
            or (all(isinstance(page, str) for page in pages) and len(set(pages)) != len(pages))
        ):
            reasons.add("INVALID_REQUEST")
        else:
            expected_pages = pages

        records = request.get("expected_records")
        if not isinstance(records, list):
            reasons.add("INVALID_REQUEST")
        else:
            for value in records:
                item = _record(value, start, end, reasons)
                if item is None:
                    continue
                if item in expected_records:
                    reasons.add("RECORD_DUPLICATE")
                expected_records.add(item)

    documents = data.get("source_documents")
    accepted_document_count = 0
    seen_ids: set[str] = set()
    observed_pages: set[str] = set()
    observed_records: set[tuple[str, str]] = set()
    if not isinstance(documents, list) or not documents:
        reasons.add("INVALID_DOCUMENTS")
    else:
        for document in documents:
            if not isinstance(document, dict) or set(document) != {"id", "sha256", "payload"}:
                reasons.add("INVALID_DOCUMENTS")
                continue
            document_id = document.get("id")
            digest = document.get("sha256")
            if (
                not _trimmed(document_id)
                or document_id in seen_ids
                or not isinstance(digest, str)
                or _HEX.fullmatch(digest) is None
            ):
                reasons.add("INVALID_DOCUMENTS")
                continue
            seen_ids.add(document_id)
            payload = document.get("payload")
            try:
                actual = hashlib.sha256(_canonical(payload)).hexdigest()
            except (TypeError, ValueError, OverflowError, RecursionError):
                reasons.add("INVALID_DOCUMENTS")
                continue
            accepted_document_count += 1
            if actual != digest:
                reasons.add("DOCUMENT_HASH_MISMATCH")

            if not isinstance(payload, dict) or set(payload) != {"page_id", "rows"}:
                reasons.add("INVALID_DOCUMENTS")
                continue
            page_id = payload.get("page_id")
            rows = payload.get("rows")
            if not _trimmed(page_id) or not isinstance(rows, list):
                reasons.add("INVALID_DOCUMENTS")
                continue
            if page_id in observed_pages:
                reasons.add("PAGE_DUPLICATE")
            observed_pages.add(page_id)
            for value in rows:
                item = _record(value, start, end, reasons)
                if item is None:
                    continue
                if item in observed_records:
                    reasons.add("RECORD_DUPLICATE")
                observed_records.add(item)

    if set(expected_pages) != observed_pages:
        reasons.add("PAGE_SET_MISMATCH")
    if expected_records != observed_records:
        reasons.add("RECORD_SET_MISMATCH")

    return {
        "mode": MODE,
        "status": "DATA_INCOMPLETE" if reasons else "VERIFIED_OFFLINE_COVERAGE",
        "source_origin": ORIGIN,
        "expected_page_count": len(expected_pages),
        "source_document_count": accepted_document_count,
        "expected_record_count": len(expected_records),
        "observed_record_count": len(observed_records),
        "reason_codes": sorted(reasons),
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }
