"""Fixed contract vectors and stage boundaries for the v2 binding API."""
import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from aitrader import history_validity_binding_v2 as api
from aitrader.evidence_history_fixture import _ValidatedHistory
from aitrader.period_evidence_history_fixture import _ValidatedPeriodHistory

ROOT = Path(__file__).parents[1]
EXPECTED = json.loads(Path(__file__).with_name("binding_v2_expected.json").read_text(encoding="utf-8"))
COUNTS = [key for key in EXPECTED["E1"] if key.endswith("_count")]


def bundle():
    return json.loads((ROOT / "examples/history_validity_binding_v2_valid.json").read_text(encoding="utf-8"))


def rehash(entry):
    raw = json.dumps(entry["payload"], sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode()
    entry["sha256"] = hashlib.sha256(raw).hexdigest()
    return entry


def added_period(value, extra=False):
    entry = copy.deepcopy(value["period_history"]["entries"][0])
    if extra:
        entry.update(receipt_id="artificial-period-extra-receipt",
                     revision_id="artificial-period-extra-revision",
                     series_id="artificial-period-extra-series")
        entry["subject"]["code"] = entry["payload"]["code"] = "0002"
    else:
        entry.update(receipt_id="artificial-period-lots-receipt-x",
                     revision_id="artificial-period-lots-revision-x",
                     series_id="artificial-period-lots-series-x")
        entry["payload"].update(valid_from="2026-09-11T00:00:00+09:00",
                                valid_until="2026-09-15T00:00:00+09:00")
    entry["observed_at"] = entry["recorded_at"] = "2026-09-11T18:00:00+09:00"
    return rehash(entry)


def vector(number):
    value = bundle()
    if number in (3, 5, 9):
        value["period_history"]["entries"].insert(2, added_period(value))
    if number in (4, 5):
        value["period_history"]["entries"].insert(-1, added_period(value, extra=True))
    if number in (2, 9):
        value["period_history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    if number == 6:
        value["history"]["entries"] = value["history"]["entries"][:1]
        value["period_history"]["entries"] = value["period_history"]["entries"][::2]
    if number == 7:
        entry = value["history"]["entries"][0]
        entry["payload"]["lot_size"] = 200
        rehash(entry)
    if number == 8:
        entry = value["period_history"]["entries"][1]
        entry["payload"]["valid_until"] = value["input"]["as_of"]
        rehash(entry)
    return value


def early(value, reasons):
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == reasons
    assert all(result[key] == 0 for key in COUNTS)
    assert result["selection_sha256"] is None
    assert result["period_evidence_timed"] is False
    return result


@pytest.mark.parametrize("number", range(1, 10))
def test_fixed_contract_vectors(number):
    value = vector(number)
    before = copy.deepcopy(value)
    assert api.inspect_history_validity_binding(value) == EXPECTED[f"E{number}"]
    assert value == before


def test_both_modes_precede_broken_envelopes_and_components(monkeypatch):
    value = bundle()
    value["history"] = {"mode": "old"}
    value["period_history"] = {"mode": "old", "garbage": True}
    monkeypatch.setattr(api, "inspect_strict_input", lambda _: pytest.fail("called stage 3"))
    early(value, ["HISTORY_MODE_MISMATCH", "PERIOD_HISTORY_MODE_MISMATCH"])
    value["extra"] = True
    early(value, ["INVALID_BUNDLE"])


@pytest.mark.parametrize("part", ["input", "history", "period_history"])
def test_stage3_origin_and_missing_mode_map_to_invalid_bundle(part):
    value = bundle()
    value[part]["source_origin"] = "other"
    early(value, ["INVALID_BUNDLE"])
    value = bundle()
    del value[part]["mode"]
    early(value, ["INVALID_BUNDLE"])


def test_stage3_short_circuit_and_no_evaluation(monkeypatch):
    monkeypatch.setattr(_ValidatedHistory, "evaluate", lambda *_: pytest.fail("row evaluate"))
    monkeypatch.setattr(_ValidatedPeriodHistory, "evaluate", lambda *_: pytest.fail("period evaluate"))
    value = vector(9)
    early(value, ["DECISION_TIME_MISMATCH"])
    value["history"]["entries"] = []
    monkeypatch.setattr(api, "_validate_period_history", lambda _: pytest.fail("period validation"))
    early(value, ["INVALID_BUNDLE"])


def test_models_evaluated_exactly_once_at_input_instant(monkeypatch):
    calls = []
    for cls, label in ((_ValidatedHistory, "row"), (_ValidatedPeriodHistory, "period")):
        original = cls.evaluate
        def spy(self, at, original=original, label=label):
            calls.append((label, at))
            return original(self, at)
        monkeypatch.setattr(cls, "evaluate", spy)
    value = bundle()
    value["history"]["decision_at"] = "2026-09-11T21:50:00Z"
    assert api.inspect_history_validity_binding(value) == EXPECTED["E1"]
    assert [label for label, _ in calls] == ["row", "period"]
    assert calls[0][1] == calls[1][1] == api._aware(value["input"]["as_of"])


@pytest.mark.parametrize("kind", ["row", "period", "unknown"])
def test_evaluation_defense_resets_all_diagnostics(monkeypatch, kind):
    cls = _ValidatedHistory if kind == "row" else _ValidatedPeriodHistory
    original = cls.evaluate
    def failed(self, at):
        result = original(self, at)
        if kind == "row":
            return replace(result, selection_sha256=None)
        return replace(result, selection_sha256=None,
                       reason="SELECTION_HASH_INVALID" if kind == "period" else "UNEXPECTED")
    monkeypatch.setattr(cls, "evaluate", failed)
    if kind == "row":
        monkeypatch.setattr(_ValidatedPeriodHistory, "evaluate", lambda *_: pytest.fail("period called"))
    early(vector(4), ["INVALID_BUNDLE"])


def test_future_corruption_precedes_time_mismatch():
    value = vector(9)
    value["period_history"]["entries"][-1]["sha256"] = "0" * 64
    early(value, ["INVALID_BUNDLE"])


@pytest.mark.parametrize("identifier", ["receipt_id", "revision_id", "series_id"])
def test_period_ascii_boundary_and_time_priority(identifier):
    value = vector(4)
    value["period_history"]["entries"][2][identifier] = "非ASCII"
    value["period_history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    early(value, ["IDENTIFIER_FORMAT_INVALID"])


def test_code_and_id_same_stage():
    value = vector(4)
    entry = value["period_history"]["entries"][2]
    entry["subject"]["code"] = entry["payload"]["code"] = "ab"
    entry["series_id"] = "非ASCII"
    rehash(entry)
    early(value, ["CODE_FORMAT_INVALID", "IDENTIFIER_FORMAT_INVALID"])


@pytest.mark.parametrize("future", [False, True])
def test_extra_inactive_period_subject_is_not_hidden(future):
    value = vector(4)
    entry = value["period_history"]["entries"].pop(2)
    if future:
        entry["recorded_at"] = "2026-09-20T00:00:00+09:00"
        value["period_history"]["entries"].append(entry)
    else:
        entry["payload"]["valid_until"] = value["input"]["as_of"]
        value["period_history"]["entries"].insert(2, rehash(entry))
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["EXTRA_SUBJECT_PRESENT"]
    assert result["extra_subject_count"] == 1 and result["matched_subject_count"] == 2
    assert result["period_candidate_count"] == 2 and result["period_series_count"] == 3


def test_same_extra_subject_in_both_histories_counted_once():
    value = vector(4)
    entry = copy.deepcopy(value["history"]["entries"][0])
    entry.update(receipt_id="extra-row", revision_id="extra-row")
    entry["subject"]["code"] = entry["payload"]["code"] = "0002"
    value["history"]["entries"].append(rehash(entry))
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["EXTRA_SUBJECT_PRESENT"]
    assert result["extra_subject_count"] == 1


def test_period_receipt_keeps_v1_exact_comparison():
    value = bundle()
    entry = copy.deepcopy(value["period_history"]["entries"][0])
    entry["observed_at"] = "2026-09-10T07:00:00+00:00"
    value["period_history"]["entries"].append(entry)
    early(value, ["INVALID_BUNDLE"])


def test_record_hash_mismatch_with_valid_self_hash():
    value = bundle()
    entry = value["period_history"]["entries"][1]
    entry["payload"]["record_sha256"] = "0" * 64
    rehash(entry)
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert result["matched_subject_count"] == 1


def test_future_row_correction_is_diagnostic_not_disqualifier():
    value = bundle()
    original = value["history"]["entries"][0]
    entry = copy.deepcopy(original)
    entry.update(receipt_id="later", revision_id="later",
                 recorded_at="2026-09-13T00:00:00+09:00",
                 supersedes={"revision_id": original["revision_id"], "sha256": original["sha256"]})
    value["history"]["entries"].append(entry)
    result = api.inspect_history_validity_binding(value)
    expected = dict(EXPECTED["E1"], corrected_after_as_of_count=1)
    assert result == expected
