"""Pure in-memory inspection of artificial period-evidence revision history.

Inputs are JSON-derived plain values. No persistence, provenance verification,
row validation, or calls to the four existing inspection APIs occur here.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from .strict_input import _aware, _canonical


MODE = "period_evidence_history_fixture_v1"
ORIGIN = "offline_fixture"
_HEX = re.compile(r"[0-9a-f]{64}")
_ENTRY_KEYS = {
    "receipt_id", "revision_id", "series_id", "subject", "observed_at",
    "recorded_at", "payload", "sha256", "supersedes",
}
_PAYLOAD_KEYS = {"group", "code", "record_sha256", "valid_from", "valid_until"}


def _output(reason: str | None = None, **counts: Any) -> dict:
    return {
        "mode": MODE,
        "source_origin": ORIGIN,
        "status": "DATA_INCOMPLETE" if reason else "VERIFIED_OFFLINE_PERIOD_HISTORY",
        "reason_codes": [] if reason is None else [reason],
        "receipt_count": counts.get("receipt_count", 0),
        "revision_count": counts.get("revision_count", 0),
        "noop_receipt_count": counts.get("noop_receipt_count", 0),
        "series_count": counts.get("series_count", 0),
        "selected_series_count": counts.get("selected_series_count", 0),
        "future_revision_count": counts.get("future_revision_count", 0),
        "selection_sha256": counts.get("selection_sha256"),
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }


def _trimmed(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _hash_format(value: Any) -> bool:
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def _digest(value: Any) -> str | None:
    try:
        return hashlib.sha256(_canonical(value)).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


@dataclass(frozen=True)
class _PeriodRevision:
    series_id: str
    subject: tuple[str, str]
    revision_id: str
    sha256: str
    payload: Mapping[str, str]
    recorded_at: datetime
    valid_from: datetime
    valid_until: datetime


@dataclass(frozen=True)
class _PeriodEvaluation:
    selected: Mapping[str, _PeriodRevision]
    candidates: Mapping[tuple[str, str], _PeriodRevision]
    future_revision_count: int
    selection_sha256: str | None
    reason: str | None


@dataclass(frozen=True)
class _ValidatedPeriodHistory:
    decision_at: datetime
    revisions: tuple[_PeriodRevision, ...]
    receipt_count: int
    revision_count: int
    noop_receipt_count: int
    series_count: int

    def evaluate(self, at: datetime) -> _PeriodEvaluation:
        """Select by first recorded time, then filter the selected intervals."""
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("at must be a timezone-aware datetime")
        selected: dict[str, _PeriodRevision] = {}
        future_count = 0
        for revision in self.revisions:
            if revision.recorded_at <= at:
                selected[revision.series_id] = revision
            else:
                future_count += 1
        candidates: dict[tuple[str, str], _PeriodRevision] = {}
        for revision in selected.values():
            if revision.valid_from <= at < revision.valid_until:
                if revision.subject in candidates:
                    return _PeriodEvaluation(
                        MappingProxyType(selected), MappingProxyType({}),
                        future_count, None, "PERIOD_OVERLAP_AT_DECISION",
                    )
                candidates[revision.subject] = revision
        projection = [
            {
                "group": subject[0], "code": subject[1],
                "series_id": candidates[subject].series_id,
                "revision_id": candidates[subject].revision_id,
                "sha256": candidates[subject].sha256,
            }
            for subject in sorted(candidates)
        ]
        selection_hash = _digest(projection)
        return _PeriodEvaluation(
            MappingProxyType(selected), MappingProxyType(candidates),
            future_count, selection_hash,
            "SELECTION_HASH_INVALID" if selection_hash is None else None,
        )


def _validate_period_history(bundle: object) -> _ValidatedPeriodHistory | dict:
    """Validate all entries, including future entries, without selecting them."""
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
    revisions: dict[str, tuple[str, tuple[str, str], str, bytes]] = {}
    heads: dict[str, tuple[str, str]] = {}
    series_subjects: dict[str, tuple[str, str]] = {}
    validated_revisions: list[_PeriodRevision] = []
    last_recorded = None
    noop_count = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            return _output("INVALID_ENTRY")
        try:
            canonical_entry = _canonical(entry)
        except (TypeError, ValueError, OverflowError, RecursionError):
            return _output("INVALID_ENTRY")
        receipt_id = entry.get("receipt_id")
        revision_id = entry.get("revision_id")
        series_id = entry.get("series_id")
        if not all(_trimmed(value) for value in (receipt_id, revision_id, series_id)):
            return _output("INVALID_IDENTIFIER")
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
        observed_at = _aware(entry.get("observed_at"))
        recorded_at = _aware(entry.get("recorded_at"))
        if observed_at is None or recorded_at is None or observed_at > recorded_at:
            return _output("INVALID_TIMESTAMPS")
        if last_recorded is not None and recorded_at < last_recorded:
            return _output("RECORDED_AT_REVERSED")
        payload = entry.get("payload")
        if (
            not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS
            or payload.get("group") != subject[0] or payload.get("code") != subject[1]
            or not _hash_format(payload.get("record_sha256"))
        ):
            return _output("INVALID_PAYLOAD")
        valid_from = _aware(payload.get("valid_from"))
        valid_until = _aware(payload.get("valid_until"))
        if valid_from is None or valid_until is None or valid_from >= valid_until:
            return _output("PERIOD_INTERVAL_INVALID")
        payload_hash = _digest(payload)
        supplied_hash = entry.get("sha256")
        if payload_hash is None or not _hash_format(supplied_hash):
            return _output("INVALID_HASH")
        if supplied_hash != payload_hash:
            return _output("PAYLOAD_HASH_MISMATCH")
        supersedes = entry.get("supersedes")
        if supersedes is None:
            supersedes_bytes = b"null"
        elif (
            isinstance(supersedes, dict) and set(supersedes) == {"revision_id", "sha256"}
            and _trimmed(supersedes.get("revision_id"))
            and _hash_format(supersedes.get("sha256"))
        ):
            supersedes_bytes = _canonical(supersedes)
        else:
            return _output("INVALID_SUPERSEDES")
        identity = (series_id, subject, payload_hash, supersedes_bytes)
        if revision_id in revisions:
            if revisions[revision_id] != identity:
                return _output("REVISION_CONFLICT")
            receipts[receipt_id] = canonical_entry
            last_recorded = recorded_at
            continue
        if series_id in series_subjects and series_subjects[series_id] != subject:
            return _output("SERIES_REFERENCE_INVALID")
        if supersedes is not None:
            reference = revisions.get(supersedes["revision_id"])
            if reference is not None and reference[0] != series_id:
                return _output("SERIES_REFERENCE_INVALID")
        prior = heads.get(series_id)
        if prior is None:
            if supersedes is not None:
                return _output("INVALID_SUPERSEDES")
        elif supersedes != {"revision_id": prior[0], "sha256": prior[1]}:
            # Includes a second initial revision (null) in an existing series.
            return _output("INVALID_SUPERSEDES")
        receipts[receipt_id] = canonical_entry
        revisions[revision_id] = identity
        heads[series_id] = (revision_id, payload_hash)
        series_subjects[series_id] = subject
        validated_revisions.append(_PeriodRevision(
            series_id, subject, revision_id, payload_hash,
            MappingProxyType(dict(payload)), recorded_at, valid_from, valid_until,
        ))
        last_recorded = recorded_at
    return _ValidatedPeriodHistory(
        decision_at, tuple(validated_revisions), len(receipts), len(revisions),
        noop_count, len(heads),
    )


def inspect_period_evidence_history(bundle: object) -> dict:
    """Inspect an offline fixture and return only fixed counts and a digest."""
    validated = _validate_period_history(bundle)
    if isinstance(validated, dict):
        return validated
    evaluation = validated.evaluate(validated.decision_at)
    if evaluation.reason is not None:
        return _output(evaluation.reason)
    return _output(
        receipt_count=validated.receipt_count,
        revision_count=validated.revision_count,
        noop_receipt_count=validated.noop_receipt_count,
        series_count=validated.series_count,
        selected_series_count=len(evaluation.selected),
        future_revision_count=evaluation.future_revision_count,
        selection_sha256=evaluation.selection_sha256,
    )
