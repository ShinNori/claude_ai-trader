"""Focused contract tests for UTC-normalized evidence history v2."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from aitrader.evidence_history_fixture import inspect_evidence_history as inspect_v1
from aitrader.evidence_history_fixture_v2 import (
    _ValidatedHistory,
    _validate_history,
    inspect_evidence_history,
)


EXAMPLE = Path(__file__).parents[1] / "examples" / "evidence_history_valid.json"


def _bundle() -> dict:
    value = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    value["mode"] = "evidence_history_fixture_v2"
    return value


def test_public_shape_model_interface_digest_and_input_immutability():
    value = _bundle()
    before = deepcopy(value)
    model = _validate_history(value)
    assert isinstance(model, _ValidatedHistory)
    assert model.decision_at.tzinfo is not None
    assert model.evaluate(model.decision_at).selection_sha256 is not None

    result = inspect_evidence_history(value)
    assert value == before
    assert set(result) == {
        "mode", "source_origin", "status", "receipt_count", "revision_count",
        "noop_receipt_count", "selected_revision_count", "future_revision_count",
        "selection_sha256", "reason_codes", "ready_for_live", "current_signal",
        "read_only",
    }
    v1_value = deepcopy(value)
    v1_value["mode"] = "evidence_history_fixture_v1"
    assert result["selection_sha256"] == inspect_v1(v1_value)["selection_sha256"]


def test_equivalent_jst_and_utc_receipt_replay_is_a_noop():
    value = _bundle()
    replay = deepcopy(value["entries"][0])
    replay["observed_at"] = "2026-09-10T07:00:00.000000Z"
    replay["recorded_at"] = "2026-09-10T07:00:00+00:00"
    value["entries"].append(replay)
    result = inspect_evidence_history(value)
    assert result["status"] == "VERIFIED_OFFLINE_HISTORY"
    assert result["noop_receipt_count"] == 1


def test_same_receipt_with_different_utc_instant_conflicts():
    value = _bundle()
    replay = deepcopy(value["entries"][0])
    replay["recorded_at"] = "2026-09-10T07:00:01Z"
    value["entries"].append(replay)
    assert inspect_evidence_history(value)["reason_codes"] == ["RECEIPT_CONFLICT"]


@pytest.mark.parametrize("bad", [
    "20260912T065000+0900",
    "2026-09-12t06:50:00+09:00",
    "2026-09-12T06:50:00z",
    "2026-09-12T06:50:00.1234567+09:00",
    "2026-09-12T06:50:00+24:00",
    "2026-09-12T06:50:00+09:60",
    "2026-09-12T06:50:00",
    "2026-09-12",
    "2026-09-12T06:50:60Z",
])
def test_narrow_ascii_extended_timestamp_grammar_rejects_malformed_values(bad):
    value = _bundle()
    value["entries"][0]["observed_at"] = bad
    assert inspect_evidence_history(value)["reason_codes"] == ["INVALID_TIMESTAMPS"]


@pytest.mark.parametrize("good", [
    "2026-09-10T00:00:00Z",
    "2026-09-10T00:00:00.1+00:00",
    "2026-09-11T00:00:00.123456+23:59",
])
def test_timestamp_grammar_accepts_fraction_boundaries_and_maximum_offset(good):
    value = _bundle()
    value["entries"][0]["observed_at"] = good
    assert inspect_evidence_history(value)["reason_codes"] == []


@pytest.mark.parametrize("overflow", [
    "0001-01-01T00:00:00+09:00",
    "9999-12-31T23:00:00-05:00",
])
def test_utc_conversion_overflow_is_invalid_timestamps(overflow):
    value = _bundle()
    value["entries"][0]["observed_at"] = overflow
    assert inspect_evidence_history(value)["reason_codes"] == ["INVALID_TIMESTAMPS"]


def test_timestamp_failure_precedes_receipt_conflict_and_subject_failure():
    value = _bundle()
    replay = deepcopy(value["entries"][0])
    replay["observed_at"] = "bad"
    replay["subject"] = {"broken": True}
    value["entries"].append(replay)
    assert inspect_evidence_history(value)["reason_codes"] == ["INVALID_TIMESTAMPS"]


def test_decision_at_keeps_existing_aware_rules_and_payload_hash_is_unchanged():
    value = _bundle()
    original_hash = value["entries"][0]["sha256"]
    value["decision_at"] = "2026-09-12T06:50:00+09:00"
    value["entries"][0]["observed_at"] = "2026-09-10T07:00:00Z"
    result = inspect_evidence_history(value)
    assert result["reason_codes"] == []
    assert value["entries"][0]["sha256"] == original_hash


def test_v1_behavior_and_mode_remain_unchanged():
    value = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    before = inspect_v1(deepcopy(value))
    replay = deepcopy(value["entries"][0])
    replay["observed_at"] = "2026-09-10T07:00:00Z"
    replay["recorded_at"] = "2026-09-10T07:00:00Z"
    value["entries"].append(replay)
    assert inspect_v1(value)["reason_codes"] == ["RECEIPT_CONFLICT"]
    assert before["mode"] == "evidence_history_fixture_v1"
