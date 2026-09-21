"""Pure offline validity evidence for selected strict-input records."""
from __future__ import annotations

from collections import Counter
import hashlib
import re
from typing import Any

from .strict_input import _aware, _canonical, _resolve, inspect_strict_input


MODE = "strict_validity_fixture_v1"
ORIGIN = "offline_fixture"
SCOPE = "selected_lots_events"
_GROUPS = ("lots", "events")
_HEX = re.compile(r"^[0-9a-f]{64}$")


def _result(
    reasons: set[str],
    *,
    expected: int = 0,
    verified: int = 0,
) -> dict:
    return {
        "mode": MODE,
        "source_origin": ORIGIN,
        "scope": SCOPE,
        "status": "DATA_INCOMPLETE" if reasons else "VERIFIED_OFFLINE_VALIDITY",
        "expected_evidence_count": expected,
        "verified_evidence_count": verified,
        "reason_codes": sorted(reasons),
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }


def _trimmed(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def inspect_strict_validity(bundle: object) -> dict:
    """Validate fixture validity periods bound to selected lot/event records."""
    reasons: set[str] = set()
    data = bundle if isinstance(bundle, dict) else {}
    if not isinstance(bundle, dict):
        reasons.add("INVALID_BUNDLE")
    elif set(data) != {
        "mode", "source_origin", "input", "validity_documents"
    }:
        reasons.add("INVALID_BUNDLE")
    if data.get("mode") != MODE:
        reasons.add("INVALID_MODE")
    if data.get("source_origin") != ORIGIN:
        reasons.add("INVALID_ORIGIN")

    input_result = inspect_strict_input(data.get("input"))
    if input_result["status"] != "VERIFIED_OFFLINE_INPUT":
        reasons.update(f"INPUT_{reason}" for reason in input_result["reason_codes"])
        return _result(reasons)

    expected_count = 0
    selected_records: dict[tuple[str, str], Any] = {}
    try:
        # This is defensive isolation from strict_input's VERIFIED invariant: a
        # future validation-order regression must still fail closed here.
        strict_input = data["input"]
        expected_subjects = {
            (group, code)
            for group in _GROUPS
            for code in strict_input["expected_codes"]
        }
        expected_count = len(expected_subjects)
        as_of = _aware(strict_input["as_of"])
        if as_of is None:
            reasons.add("INPUT_INVALID_AS_OF")
            return _result(reasons, expected=expected_count)
        source_documents = {
            document["id"]: document["payload"]
            for document in strict_input["source_documents"]
        }
        for subject in expected_subjects:
            group, code = subject
            reference = strict_input["sources"][group][code]
            selected_records[subject] = _resolve(
                source_documents[reference["document_id"]], reference["pointer"]
            )
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, RecursionError):
        reasons.add("INPUT_SOURCE_REFERENCE_INVALID")
        return _result(reasons, expected=expected_count)

    documents = data.get("validity_documents")
    if not isinstance(documents, list) or not documents:
        reasons.add("INVALID_VALIDITY_DOCUMENTS")
        return _result(reasons, expected=expected_count)

    valid_ids = [
        document.get("id")
        for document in documents
        if isinstance(document, dict) and _trimmed(document.get("id"))
    ]
    id_counts = Counter(valid_ids)
    if any(count > 1 for count in id_counts.values()):
        reasons.add("INVALID_VALIDITY_DOCUMENTS")

    declared_subjects: list[tuple[str, str]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        payload = document.get("payload")
        if isinstance(payload, dict):
            group = payload.get("group")
            code = payload.get("code")
            if isinstance(group, str) and isinstance(code, str):
                declared_subjects.append((group, code))
    subject_counts = Counter(declared_subjects)
    if any(count > 1 for count in subject_counts.values()):
        reasons.add("VALIDITY_SUBJECT_DUPLICATE")
    if (
        set(subject_counts) != expected_subjects
        or any(subject_counts[subject] != 1 for subject in expected_subjects)
    ):
        reasons.add("VALIDITY_SET_MISMATCH")

    passing_subjects: set[tuple[str, str]] = set()
    payload_keys = {
        "group", "code", "record_sha256", "valid_from", "valid_until"
    }
    for document in documents:
        valid = True
        if (
            not isinstance(document, dict)
            or set(document) != {"id", "sha256", "payload"}
            or not _trimmed(document.get("id"))
            or id_counts.get(document.get("id"), 0) != 1
            or not isinstance(document.get("sha256"), str)
            or _HEX.fullmatch(document["sha256"]) is None
        ):
            reasons.add("INVALID_VALIDITY_DOCUMENTS")
            continue

        payload = document.get("payload")
        try:
            actual_document_hash = hashlib.sha256(_canonical(payload)).hexdigest()
        except (TypeError, ValueError, OverflowError, RecursionError):
            reasons.add("INVALID_VALIDITY_DOCUMENTS")
            continue
        if actual_document_hash != document["sha256"]:
            reasons.add("VALIDITY_HASH_MISMATCH")
            valid = False

        if not isinstance(payload, dict) or set(payload) != payload_keys:
            reasons.add("VALIDITY_SUBJECT_INVALID")
            continue
        group = payload.get("group")
        code = payload.get("code")
        subject = (group, code) if isinstance(group, str) and isinstance(code, str) else None
        if subject not in expected_subjects:
            reasons.add("VALIDITY_SUBJECT_INVALID")
            continue
        if subject_counts[subject] != 1:
            valid = False

        record_hash = payload.get("record_sha256")
        if not isinstance(record_hash, str) or _HEX.fullmatch(record_hash) is None:
            reasons.add("VALIDITY_SUBJECT_INVALID")
            valid = False
        else:
            try:
                actual_record_hash = hashlib.sha256(
                    _canonical(selected_records[subject])
                ).hexdigest()
            except (TypeError, ValueError, OverflowError, RecursionError):
                reasons.add("VALIDITY_RECORD_MISMATCH")
                valid = False
            else:
                if record_hash != actual_record_hash:
                    reasons.add("VALIDITY_RECORD_MISMATCH")
                    valid = False

        valid_from = _aware(payload.get("valid_from"))
        valid_until = _aware(payload.get("valid_until"))
        if valid_from is None or valid_until is None or valid_from >= valid_until:
            reasons.add("VALIDITY_PERIOD_INVALID")
            valid = False
        else:
            if as_of < valid_from:
                reasons.add("VALIDITY_NOT_YET_EFFECTIVE")
                valid = False
            if as_of >= valid_until:
                reasons.add("VALIDITY_EXPIRED")
                valid = False

        if valid:
            passing_subjects.add(subject)

    return _result(
        reasons,
        expected=expected_count,
        verified=len(passing_subjects),
    )
