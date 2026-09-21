"""Pure in-memory v2 model for an artificial evidence revision history."""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from .evidence_history_fixture import (
    _ENTRY_KEYS,
    _HEX,
    _Revision,
    _ValidatedHistory,
    _digest,
    _trimmed,
)
from .strict_input import _aware, _canonical, _event, _lot


MODE = "evidence_history_fixture_v2"
ORIGIN = "offline_fixture"
_ENTRY_TIME = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,6}))?"
    r"(?P<zone>Z|(?P<sign>[+-])(?P<offset_hour>[0-9]{2}):(?P<offset_minute>[0-9]{2}))$"
)


def _output(reason: str | None = None, **counts: Any) -> dict:
    return {
        "mode": MODE,
        "source_origin": ORIGIN,
        "status": "DATA_INCOMPLETE" if reason else "VERIFIED_OFFLINE_HISTORY",
        "receipt_count": counts.get("receipt_count", 0),
        "revision_count": counts.get("revision_count", 0),
        "noop_receipt_count": counts.get("noop_receipt_count", 0),
        "selected_revision_count": counts.get("selected_revision_count", 0),
        "future_revision_count": counts.get("future_revision_count", 0),
        "selection_sha256": counts.get("selection_sha256"),
        "reason_codes": [] if reason is None else [reason],
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }


def _entry_time_utc(value: object) -> tuple[datetime, str] | None:
    """Parse the contract's narrow timestamp grammar and return its UTC copy."""
    if not isinstance(value, str):
        return None
    match = _ENTRY_TIME.fullmatch(value)
    if match is None:
        return None
    if match["zone"] != "Z":
        if int(match["offset_hour"]) > 23 or int(match["offset_minute"]) > 59:
            return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        utc = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None
    normalized = (
        f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}T"
        f"{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}."
        f"{utc.microsecond:06d}+00:00"
    )
    return parsed, normalized


def _validate_history(bundle: object) -> _ValidatedHistory | dict:
    """Validate v2 history independently of selection."""
    if not isinstance(bundle, dict) or set(bundle) != {
        "mode", "source_origin", "decision_at", "entries"
    }:
        return _output("INVALID_BUNDLE")
    if bundle.get("mode") != MODE:
        return _output("INVALID_MODE")
    if bundle.get("source_origin") != ORIGIN:
        return _output("INVALID_ORIGIN")
    decision_at = _aware(bundle.get("decision_at"))
    if decision_at is None:
        return _output("INVALID_DECISION_AT")
    entries = bundle.get("entries")
    if not isinstance(entries, list) or not entries:
        return _output("INVALID_ENTRIES")

    receipts: dict[str, bytes] = {}
    revisions: dict[str, tuple[tuple[str, str], str, bytes]] = {}
    heads: dict[tuple[str, str], tuple[str, str]] = {}
    validated_revisions: list[_Revision] = []
    last_recorded = None
    noop_count = 0

    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            return _output("INVALID_ENTRY")
        try:
            _canonical(entry)
        except (TypeError, ValueError, OverflowError, RecursionError):
            return _output("INVALID_ENTRY")

        receipt_id = entry.get("receipt_id")
        revision_id = entry.get("revision_id")
        if not _trimmed(receipt_id) or not _trimmed(revision_id):
            return _output("INVALID_IDENTIFIER")

        observed = _entry_time_utc(entry.get("observed_at"))
        recorded = _entry_time_utc(entry.get("recorded_at"))
        if observed is None or recorded is None:
            return _output("INVALID_TIMESTAMPS")
        observed_at, normalized_observed = observed
        recorded_at, normalized_recorded = recorded
        comparison_entry = dict(entry)
        comparison_entry["observed_at"] = normalized_observed
        comparison_entry["recorded_at"] = normalized_recorded
        try:
            canonical_entry = _canonical(comparison_entry)
        except (TypeError, ValueError, OverflowError, RecursionError):
            return _output("INVALID_TIMESTAMPS")

        if receipt_id in receipts:
            if receipts[receipt_id] == canonical_entry:
                noop_count += 1
                continue
            return _output("RECEIPT_CONFLICT")

        subject_value = entry.get("subject")
        if (
            not isinstance(subject_value, dict)
            or set(subject_value) != {"group", "code"}
            or subject_value.get("group") not in ("lots", "events")
            or not _trimmed(subject_value.get("code"))
        ):
            return _output("INVALID_SUBJECT")
        subject = (subject_value["group"], subject_value["code"])

        if observed_at > recorded_at:
            return _output("INVALID_TIMESTAMPS")
        if last_recorded is not None and recorded_at < last_recorded:
            return _output("RECORDED_AT_REVERSED")

        payload = entry.get("payload")
        payload_reasons: set[str] = set()
        if subject[0] == "lots":
            _lot(payload, subject[1], payload_reasons)
        else:
            _event(payload, subject[1], payload_reasons)
        if payload_reasons:
            return _output("INVALID_PAYLOAD")
        payload_hash = _digest(payload)
        supplied_hash = entry.get("sha256")
        if (
            payload_hash is None
            or not isinstance(supplied_hash, str)
            or _HEX.fullmatch(supplied_hash) is None
        ):
            return _output("INVALID_HASH")
        if supplied_hash != payload_hash:
            return _output("PAYLOAD_HASH_MISMATCH")

        supersedes = entry.get("supersedes")
        if supersedes is None:
            supersedes_bytes = b"null"
        elif (
            isinstance(supersedes, dict)
            and set(supersedes) == {"revision_id", "sha256"}
            and _trimmed(supersedes.get("revision_id"))
            and isinstance(supersedes.get("sha256"), str)
            and _HEX.fullmatch(supersedes["sha256"]) is not None
        ):
            try:
                supersedes_bytes = _canonical(supersedes)
            except (TypeError, ValueError, OverflowError, RecursionError):
                return _output("INVALID_SUPERSEDES")
        else:
            return _output("INVALID_SUPERSEDES")

        identity = (subject, payload_hash, supersedes_bytes)
        if revision_id in revisions:
            if revisions[revision_id] != identity:
                return _output("REVISION_CONFLICT")
            receipts[receipt_id] = canonical_entry
            last_recorded = recorded_at
            continue

        prior = heads.get(subject)
        if prior is None:
            if supersedes is not None:
                return _output("INVALID_SUPERSEDES")
        elif supersedes != {"revision_id": prior[0], "sha256": prior[1]}:
            return _output("INVALID_SUPERSEDES")

        receipts[receipt_id] = canonical_entry
        revisions[revision_id] = identity
        heads[subject] = (revision_id, payload_hash)
        validated_revisions.append(_Revision(
            subject=subject,
            revision_id=revision_id,
            sha256=payload_hash,
            payload=MappingProxyType(deepcopy(payload)),
            recorded_at=recorded_at,
            is_correction=supersedes is not None,
        ))
        last_recorded = recorded_at

    return _ValidatedHistory(
        decision_at=decision_at,
        revisions=tuple(validated_revisions),
        receipt_count=len(receipts),
        revision_count=len(revisions),
        noop_receipt_count=noop_count,
    )


def inspect_evidence_history(bundle: object) -> dict:
    """Validate and inspect artificial v2 history without I/O or persistence."""
    validated = _validate_history(bundle)
    if isinstance(validated, dict):
        return validated
    evaluation = validated.evaluate(validated.decision_at)
    if evaluation.selection_sha256 is None:
        return _output("SELECTION_HASH_INVALID")
    return _output(
        receipt_count=validated.receipt_count,
        revision_count=validated.revision_count,
        noop_receipt_count=validated.noop_receipt_count,
        selected_revision_count=len(evaluation.selected),
        future_revision_count=evaluation.future_revision_count,
        selection_sha256=evaluation.selection_sha256,
    )
