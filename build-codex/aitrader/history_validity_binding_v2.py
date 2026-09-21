"""Offline binding of row and period history evaluated at one common instant."""
from __future__ import annotations

from .evidence_history_fixture_v2 import MODE as HISTORY_MODE, _validate_history
from .history_validity_binding import _CODE, _IDENTIFIER, _GROUPS, _hash
from .period_evidence_history_fixture import MODE as PERIOD_MODE, _validate_period_history
from .strict_input import _aware, _resolve, inspect_strict_input

MODE = "history_validity_binding_fixture_v2"
ORIGIN = "offline_fixture"


def _result(reasons=(), *, required=0, matched=0, extra=0, corrected=0,
            series=0, candidates=0, digest=None):
    reasons = sorted(set(reasons))
    return {
        "mode": MODE, "source_origin": ORIGIN,
        "status": "DATA_INCOMPLETE" if reasons else "VERIFIED_OFFLINE_BINDING",
        "selection_sha256": digest, "reason_codes": reasons,
        "required_subject_count": required, "matched_subject_count": matched,
        "extra_subject_count": extra, "corrected_after_as_of_count": corrected,
        "period_series_count": series, "period_candidate_count": candidates,
        "period_evidence_timed": not reasons and matched == required and required > 0,
        "ready_for_live": False, "current_signal": False, "read_only": True,
    }


def _format_reasons(data, records):
    value = data["input"]
    codes = list(value["expected_codes"])
    identifiers = [doc["id"] for doc in value["source_documents"]]
    for selections in value["sources"].values():
        codes.extend(selections)
        identifiers.extend(ref["document_id"] for ref in selections.values())
    codes.extend(record["code"] for record in records.values())
    for field in ("history", "period_history"):
        for entry in data[field]["entries"]:
            codes.extend((entry["subject"]["code"], entry["payload"]["code"]))
            identifiers.extend((entry["receipt_id"], entry["revision_id"]))
            if field == "period_history":
                identifiers.append(entry["series_id"])
            if entry["supersedes"] is not None:
                identifiers.append(entry["supersedes"]["revision_id"])
    reasons = set()
    if any(_CODE.fullmatch(code) is None for code in codes):
        reasons.add("CODE_FORMAT_INVALID")
    if any(_IDENTIFIER.fullmatch(identifier) is None for identifier in identifiers):
        reasons.add("IDENTIFIER_FORMAT_INVALID")
    return reasons


def _inspect(data):
    if (not isinstance(data, dict) or set(data) != {
        "mode", "source_origin", "input", "history", "period_history"
    } or data["mode"] != MODE or data["source_origin"] != ORIGIN):
        return _result(["INVALID_BUNDLE"])

    reasons = set()
    for field, mode, reason in (
        ("history", HISTORY_MODE, "HISTORY_MODE_MISMATCH"),
        ("period_history", PERIOD_MODE, "PERIOD_HISTORY_MODE_MISMATCH"),
    ):
        part = data[field]
        if isinstance(part, dict) and "mode" in part and part["mode"] != mode:
            reasons.add(reason)
    if reasons:
        return _result(reasons)

    value = data["input"]
    if inspect_strict_input(value)["status"] != "VERIFIED_OFFLINE_INPUT":
        return _result(["INVALID_BUNDLE"])
    history = _validate_history(data["history"])
    if isinstance(history, dict):
        return _result(["INVALID_BUNDLE"])
    period = _validate_period_history(data["period_history"])
    if isinstance(period, dict):
        return _result(["INVALID_BUNDLE"])
    as_of = _aware(value["as_of"])
    required = {(group, code) for group in _GROUPS for code in value["expected_codes"]}
    if as_of is None or not required:
        return _result(["INVALID_BUNDLE"])
    originals = {doc["id"]: doc["payload"] for doc in value["source_documents"]}
    records = {}
    for subject in required:
        group, code = subject
        ref = value["sources"][group][code]
        records[subject] = _resolve(originals[ref["document_id"]], ref["pointer"])
    reasons = _format_reasons(data, records)
    if reasons:
        return _result(reasons)

    # No evaluation on mismatching instants; v2 discards every diagnostic count.
    if not (as_of == history.decision_at == period.decision_at):
        return _result(["DECISION_TIME_MISMATCH"])
    row_selection = history.evaluate(as_of)
    if row_selection.selection_sha256 is None:
        return _result(["INVALID_BUNDLE"])
    period_selection = period.evaluate(as_of)
    overlap = period_selection.reason == "PERIOD_OVERLAP_AT_DECISION"
    if ((period_selection.reason is not None and not overlap)
            or (not overlap and period_selection.selection_sha256 is None)):
        return _result(["INVALID_BUNDLE"])
    candidates = {} if overlap else period_selection.candidates
    if overlap:
        reasons.add("VALIDITY_NOT_SATISFIED")
    subjects = row_selection.subjects | {revision.subject for revision in period.revisions}
    extra = len(subjects - required)
    if extra:
        reasons.add("EXTRA_SUBJECT_PRESENT")
    matched = 0
    for subject in required:
        selected = row_selection.selected.get(subject)
        if selected is None:
            reasons.add("HISTORY_SELECTION_MISSING")
            continue
        row_hash = _hash(records[subject])
        if selected.sha256 != row_hash:
            reasons.add("ROW_HASH_MISMATCH")
            continue
        candidate = candidates.get(subject)
        # The period model already enforced uniqueness and the half-open interval.
        if candidate is None or candidate.payload["record_sha256"] != row_hash:
            reasons.add("VALIDITY_NOT_SATISFIED")
            continue
        matched += 1
    return _result(
        reasons, required=len(required), matched=matched, extra=extra,
        corrected=len(row_selection.corrected_subjects & required),
        series=period.series_count, candidates=len(candidates),
        digest=row_selection.selection_sha256,
    )


def inspect_history_validity_binding(bundle: object) -> dict:
    """Inspect JSON-derived values without I/O, mutation, or v1 API evaluation."""
    try:
        return _inspect(bundle)
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, RecursionError):
        return _result(["INVALID_BUNDLE"])
