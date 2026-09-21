"""Bind artificial input, historical selection and validity without any I/O."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import re

from .evidence_history_fixture import MODE as HISTORY_MODE, _validate_history
from .strict_input import _aware, _canonical, _resolve, inspect_strict_input
from .strict_validity import MODE as VALIDITY_MODE, inspect_strict_validity

MODE = "history_validity_binding_fixture_v1"
ORIGIN = "offline_fixture"
_CODE = re.compile(r"[0-9A-Z]{4,5}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HEX = re.compile(r"[0-9a-f]{64}")
_GROUPS = ("lots", "events")


def _result(reasons=(), *, required=0, matched=0, extra=0, corrected=0, digest=None):
    reasons = sorted(set(reasons))
    return {
        "mode": MODE, "source_origin": ORIGIN,
        "status": "DATA_INCOMPLETE" if reasons else "VERIFIED_OFFLINE_BINDING",
        "selection_sha256": digest, "reason_codes": reasons,
        "required_subject_count": required, "matched_subject_count": matched,
        "extra_subject_count": extra, "corrected_after_as_of_count": corrected,
        "period_evidence_timed": False, "ready_for_live": False,
        "current_signal": False, "read_only": True,
    }


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _validity_shape(documents):
    """Stage 3 accepts empty evidence and leaves semantic failures to stage 5."""
    if not isinstance(documents, list):
        return False
    ids = set()
    for document in documents:
        if not isinstance(document, dict) or set(document) != {"id", "sha256", "payload"}:
            return False
        identifier = document["id"]
        if (not isinstance(identifier, str) or not identifier
                or identifier != identifier.strip() or identifier in ids):
            return False
        ids.add(identifier)
        payload = document["payload"]
        if not isinstance(payload, dict) or set(payload) != {
            "group", "code", "record_sha256", "valid_from", "valid_until"
        }:
            return False
        if not all(isinstance(payload[key], str) for key in ("group", "code")):
            return False
        for digest in (document["sha256"], payload["record_sha256"]):
            if not isinstance(digest, str) or _HEX.fullmatch(digest) is None:
                return False
        if any(_aware(payload[key]) is None for key in ("valid_from", "valid_until")):
            return False
        if document["sha256"] != _hash(payload):
            return False
    return True


def _format_reasons(data, records):
    codes = list(data["input"]["expected_codes"])
    identifiers = [document["id"] for document in data["input"]["source_documents"]]
    for selections in data["input"]["sources"].values():
        codes.extend(selections)
        identifiers.extend(reference["document_id"] for reference in selections.values())
    codes.extend(record["code"] for record in records.values())
    for entry in data["history"]["entries"]:
        codes.extend((entry["subject"]["code"], entry["payload"]["code"]))
        identifiers.extend((entry["receipt_id"], entry["revision_id"]))
        if entry["supersedes"] is not None:
            identifiers.append(entry["supersedes"]["revision_id"])
    for document in data["validity_documents"]:
        identifiers.append(document["id"])
        codes.append(document["payload"]["code"])
    reasons = set()
    if any(_CODE.fullmatch(code) is None for code in codes):
        reasons.add("CODE_FORMAT_INVALID")
    if any(_IDENTIFIER.fullmatch(identifier) is None for identifier in identifiers):
        reasons.add("IDENTIFIER_FORMAT_INVALID")
    return reasons


def _inspect(data):
    # Stage 1/2 must not call a component or infer mode from its error codes.
    if not isinstance(data, dict) or set(data) != {
        "mode", "source_origin", "input", "history", "validity_documents"
    } or data["mode"] != MODE or data["source_origin"] != ORIGIN:
        return _result(["INVALID_BUNDLE"])
    history = data["history"]
    if isinstance(history, dict) and "mode" in history and history["mode"] != HISTORY_MODE:
        return _result(["HISTORY_MODE_MISMATCH"])

    strict_input = data["input"]
    if inspect_strict_input(strict_input)["status"] != "VERIFIED_OFFLINE_INPUT":
        return _result(["INVALID_BUNDLE"])
    model = _validate_history(history)
    if isinstance(model, dict) or not _validity_shape(data["validity_documents"]):
        return _result(["INVALID_BUNDLE"])
    required = {(group, code) for group in _GROUPS for code in strict_input["expected_codes"]}
    as_of = _aware(strict_input["as_of"])
    if as_of is None or not required:
        return _result(["INVALID_BUNDLE"])
    originals = {document["id"]: document["payload"] for document in strict_input["source_documents"]}
    records = {}
    for subject in required:
        group, code = subject
        reference = strict_input["sources"][group][code]
        records[subject] = _resolve(originals[reference["document_id"]], reference["pointer"])
    reasons = _format_reasons(data, records)
    if reasons:
        return _result(reasons)

    # One shared evaluation: matching instants make this also decision_at.
    evaluation = model.evaluate(as_of)
    counts = dict(required=len(required), extra=len(evaluation.subjects - required),
                  corrected=len(evaluation.corrected_subjects & required))
    if as_of != model.decision_at:
        return _result(["DECISION_TIME_MISMATCH"], **counts)
    if evaluation.selection_sha256 is None:
        return _result(["INVALID_BUNDLE"])

    validity = inspect_strict_validity({
        "mode": VALIDITY_MODE, "source_origin": ORIGIN,
        "input": strict_input, "validity_documents": data["validity_documents"],
    })
    if any(reason.startswith("INPUT_") for reason in validity["reason_codes"]):
        return _result(["INVALID_BUNDLE"])
    if validity["status"] != "VERIFIED_OFFLINE_VALIDITY":
        reasons.add("VALIDITY_NOT_SATISFIED")
    if counts["extra"]:
        reasons.add("EXTRA_SUBJECT_PRESENT")

    # Public validity exposes aggregate counts, not which subjects passed.
    # For matched, require a unique declared period bound to each selected row.
    periods = defaultdict(list)
    for document in data["validity_documents"]:
        payload = document["payload"]
        periods[(payload["group"], payload["code"])].append(payload)
    matched = 0
    for subject in required:
        selected = evaluation.selected.get(subject)
        if selected is None:
            reasons.add("HISTORY_SELECTION_MISSING")
            continue
        row_hash = _hash(records[subject])
        if selected.sha256 != row_hash:
            reasons.add("ROW_HASH_MISMATCH")
            continue
        evidence = periods[subject]
        if (len(evidence) == 1 and evidence[0]["record_sha256"] == row_hash
                and _aware(evidence[0]["valid_from"]) <= as_of
                and as_of < _aware(evidence[0]["valid_until"])):
            matched += 1
    return _result(reasons, matched=matched, digest=evaluation.selection_sha256, **counts)


def inspect_history_validity_binding(bundle: object) -> dict:
    """Return fixed offline diagnostics without changing input or performing I/O."""
    try:
        return _inspect(bundle)
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, RecursionError):
        # Defensive resolution/canonicalization failure discards all diagnostics.
        return _result(["INVALID_BUNDLE"])
