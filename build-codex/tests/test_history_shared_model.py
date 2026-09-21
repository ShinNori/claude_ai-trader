"""Contract tests for the binding's shared validated-history model."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

import aitrader.evidence_history_fixture as history_module
from aitrader.evidence_history_fixture import (
    _ValidatedHistory,
    _validate_history,
    inspect_evidence_history,
)


EXAMPLE = Path(__file__).parents[1] / "examples" / "evidence_history_valid.json"


def _history() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_validation_is_separate_from_one_requested_evaluation():
    history = _history()
    model = _validate_history(history)
    assert isinstance(model, _ValidatedHistory)

    before = model.evaluate(datetime.fromisoformat("2026-09-11T07:00:00+09:00"))
    selected = before.selected[("lots", "0001")]
    assert (selected.revision_id, selected.sha256, dict(selected.payload)) == (
        "artificial-v1",
        history["entries"][0]["sha256"],
        history["entries"][0]["payload"],
    )
    assert before.subjects == frozenset({("lots", "0001")})
    assert before.corrected_subjects == frozenset({("lots", "0001")})
    assert before.future_revision_count == 1

    after = model.evaluate(datetime.fromisoformat("2026-09-13T00:00:00+09:00"))
    assert after.selected[("lots", "0001")].revision_id == "artificial-v2"
    assert after.corrected_subjects == frozenset()
    assert after.future_revision_count == 0


def test_public_v1_result_remains_exactly_derived_at_decision_at():
    history = _history()
    expected = inspect_evidence_history(deepcopy(history))
    model = _validate_history(history)
    assert isinstance(model, _ValidatedHistory)
    evaluation = model.evaluate(model.decision_at)
    assert expected["selection_sha256"] == evaluation.selection_sha256
    assert expected["selected_revision_count"] == len(evaluation.selected)
    assert expected["future_revision_count"] == evaluation.future_revision_count
    assert expected["receipt_count"] == model.receipt_count
    assert expected["revision_count"] == model.revision_count


def test_future_first_revision_and_its_future_correction_are_not_corrected_at_time():
    history = _history()
    model = _validate_history(history)
    assert isinstance(model, _ValidatedHistory)
    evaluation = model.evaluate(datetime.fromisoformat("2026-09-09T00:00:00+09:00"))
    assert evaluation.selected == {}
    assert evaluation.subjects == frozenset({("lots", "0001")})
    assert evaluation.corrected_subjects == frozenset()


def test_invalid_history_returns_the_unchanged_public_failure_shape():
    history = _history()
    history["entries"][0]["sha256"] = "0" * 64
    assert _validate_history(history) == inspect_evidence_history(history)


def test_evaluate_rejects_naive_datetime():
    model = _validate_history(_history())
    assert isinstance(model, _ValidatedHistory)
    with pytest.raises(ValueError):
        model.evaluate(datetime(2026, 9, 11, 7))


def test_public_v1_keeps_selection_hash_failure_reason(monkeypatch):
    original = history_module._digest
    monkeypatch.setattr(
        history_module, "_digest",
        lambda value: None if isinstance(value, list) else original(value),
    )
    result = inspect_evidence_history(_history())
    assert result["reason_codes"] == ["SELECTION_HASH_INVALID"]
    assert result["selection_sha256"] is None
