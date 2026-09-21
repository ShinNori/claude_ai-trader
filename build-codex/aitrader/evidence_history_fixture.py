"""Pure in-memory model for an artificial evidence revision history."""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from collections.abc import Mapping
from typing import Any

from .strict_input import _aware, _canonical, _event, _lot


MODE = "evidence_history_fixture_v1"
ORIGIN = "offline_fixture"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_ENTRY_KEYS = {
    "receipt_id", "revision_id", "subject", "observed_at", "recorded_at",
    "payload", "sha256", "supersedes",
}


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


def _trimmed(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _digest(value: Any) -> str | None:
    try:
        return hashlib.sha256(_canonical(value)).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


@dataclass(frozen=True)
class _SelectedRevision:
    revision_id: str
    sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class _HistoryEvaluation:
    selected: Mapping[tuple[str, str], _SelectedRevision]
    selection_sha256: str | None
    subjects: frozenset[tuple[str, str]]
    corrected_subjects: frozenset[tuple[str, str]]
    future_revision_count: int


@dataclass(frozen=True)
class _Revision:
    subject: tuple[str, str]
    revision_id: str
    sha256: str
    payload: Mapping[str, Any]
    recorded_at: datetime
    is_correction: bool


@dataclass(frozen=True)
class _ValidatedHistory:
    decision_at: datetime
    revisions: tuple[_Revision, ...]
    receipt_count: int
    revision_count: int
    noop_receipt_count: int

    def evaluate(self, at: datetime) -> _HistoryEvaluation:
        """Select every subject once at ``at`` from already validated history."""
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("at must be a timezone-aware datetime")
        selected: dict[tuple[str, str], _SelectedRevision] = {}
        subjects: set[tuple[str, str]] = set()
        future_count = 0
        for revision in self.revisions:
            subjects.add(revision.subject)
            if revision.recorded_at <= at:
                selected[revision.subject] = _SelectedRevision(
                    revision.revision_id, revision.sha256, revision.payload,
                )
            else:
                future_count += 1
        corrected = {
            revision.subject for revision in self.revisions
            if revision.recorded_at > at
            and revision.is_correction
            and revision.subject in selected
        }
        projection = [
            {
                "group": subject[0], "code": subject[1],
                "revision_id": selected[subject].revision_id,
                "sha256": selected[subject].sha256,
            }
            for subject in sorted(selected)
        ]
        selection_hash = _digest(projection)
        return _HistoryEvaluation(
            selected=MappingProxyType(selected),
            selection_sha256=selection_hash,
            subjects=frozenset(subjects),
            corrected_subjects=frozenset(corrected),
            future_revision_count=future_count,
        )


def _validate_history(bundle: object) -> _ValidatedHistory | dict:
    """Validate history independently of selection; return public failure shape."""


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
        canonical_entry = None
        try:
            canonical_entry = _canonical(entry)
        except (TypeError, ValueError, OverflowError, RecursionError):
            return _output("INVALID_ENTRY")

        receipt_id = entry.get("receipt_id")
        revision_id = entry.get("revision_id")
        if not _trimmed(receipt_id) or not _trimmed(revision_id):
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
        payload_reasons: set[str] = set()
        if subject[0] == "lots":
            _lot(payload, subject[1], payload_reasons)
        else:
            _event(payload, subject[1], payload_reasons)
        if payload_reasons:
            return _output("INVALID_PAYLOAD")
        payload_hash = _digest(payload)
        supplied_hash = entry.get("sha256")
        # Unreachable after canonical_entry succeeds and _lot/_event accepts the
        # payload; retained as a fail-closed guard if either prerequisite changes.
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
            # Canonicalization cannot fail after the containing entry was
            # canonicalized; keep this guard local to future schema changes.
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
        else:
            expected_supersedes = {"revision_id": prior[0], "sha256": prior[1]}
            if supersedes != expected_supersedes:
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
    """Validate an artificial append-only history without I/O or persistence."""
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
