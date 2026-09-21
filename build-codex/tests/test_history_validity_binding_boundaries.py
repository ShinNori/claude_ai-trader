"""Boundary and ordering tests for the public history/validity binding."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import aitrader.history_validity_binding as binding
from aitrader.history_validity_binding import inspect_history_validity_binding
from test_history_validity_binding import _digest, _rename_code, _rehash_source


EXAMPLE = Path(__file__).parents[1] / "examples" / "history_validity_binding_valid.json"


@pytest.fixture()
def bundle():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _rehash_period(document):
    document["sha256"] = _digest(document["payload"])


def _rename_document(bundle, identifier):
    old = bundle["input"]["source_documents"][0]["id"]
    bundle["input"]["source_documents"][0]["id"] = identifier
    for references in bundle["input"]["sources"].values():
        for reference in references.values():
            if reference["document_id"] == old:
                reference["document_id"] = identifier
    _rehash_source(bundle)


def _future_extra(bundle):
    payload = {"code": "9999", "lot_size": 100}
    bundle["history"]["entries"].append({
        "receipt_id": "future-extra-receipt", "revision_id": "future-extra-revision",
        "subject": {"group": "lots", "code": "9999"},
        "observed_at": "2026-09-13T09:00:00+09:00",
        "recorded_at": "2026-09-13T09:00:00+09:00",
        "payload": payload, "sha256": _digest(payload), "supersedes": None,
    })


def test_same_instant_in_utc_and_jst_is_accepted(bundle):
    bundle["history"]["decision_at"] = "2026-09-11T21:50:00Z"
    assert inspect_history_validity_binding(bundle)["status"] == "VERIFIED_OFFLINE_BINDING"


def test_stage4_format_beats_stage5_time_difference(bundle):
    _rename_code(bundle, "０００１")
    bundle["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    result = inspect_history_validity_binding(bundle)
    assert result["reason_codes"] == ["CODE_FORMAT_INVALID"]
    assert result["required_subject_count"] == 0


def test_stage3_shape_beats_stage5_time_difference(bundle):
    bundle["validity_documents"][0]["payload"]["valid_from"] = 1
    bundle["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    result = inspect_history_validity_binding(bundle)
    assert result["reason_codes"] == ["INVALID_BUNDLE"]
    assert result["required_subject_count"] == 0


def test_future_only_extra_subject_is_rejected(bundle):
    _future_extra(bundle)
    result = inspect_history_validity_binding(bundle)
    assert result["reason_codes"] == ["EXTRA_SUBJECT_PRESENT"]
    assert result["extra_subject_count"] == 1
    assert result["matched_subject_count"] == 2


def test_stage5_combines_and_sorts_independent_reasons(bundle):
    _future_extra(bundle)
    row = bundle["input"]["source_documents"][0]["payload"]["lots"]["0001"]
    row["lot_size"] = 200
    _rehash_source(bundle)
    bundle["validity_documents"] = []
    result = inspect_history_validity_binding(bundle)
    assert result["reason_codes"] == [
        "EXTRA_SUBJECT_PRESENT", "ROW_HASH_MISMATCH", "VALIDITY_NOT_SATISFIED",
    ]
    assert result["matched_subject_count"] == 0


def test_duplicate_period_document_id_is_stage3(bundle):
    duplicate = copy.deepcopy(bundle["validity_documents"][0])
    bundle["validity_documents"].append(duplicate)
    result = inspect_history_validity_binding(bundle)
    assert result["reason_codes"] == ["INVALID_BUNDLE"]
    assert result["required_subject_count"] == 0


def test_duplicate_period_subject_with_distinct_ids_is_stage5(bundle):
    duplicate = copy.deepcopy(bundle["validity_documents"][0])
    duplicate["id"] = "second-period-lots"
    _rehash_period(duplicate)
    bundle["validity_documents"].append(duplicate)
    result = inspect_history_validity_binding(bundle)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert result["matched_subject_count"] == 1


@pytest.mark.parametrize("field,value,matched", [
    ("valid_from", "2026-09-12T06:50:00+09:00", 2),
    ("valid_until", "2026-09-12T06:50:00+09:00", 1),
])
def test_validity_interval_is_left_closed_right_open(bundle, field, value, matched):
    document = bundle["validity_documents"][0]
    document["payload"][field] = value
    _rehash_period(document)
    result = inspect_history_validity_binding(bundle)
    assert result["matched_subject_count"] == matched
    assert ("VALIDITY_NOT_SATISFIED" in result["reason_codes"]) is (matched == 1)


@pytest.mark.parametrize("length,accepted", [(128, True), (129, False)])
def test_identifier_ascii_length_boundary(bundle, length, accepted):
    _rename_document(bundle, "A" * length)
    result = inspect_history_validity_binding(bundle)
    assert (result["status"] == "VERIFIED_OFFLINE_BINDING") is accepted
    if not accepted:
        assert result["reason_codes"] == ["IDENTIFIER_FORMAT_INVALID"]


@pytest.mark.parametrize("code,accepted", [
    ("A123", True), ("A1234", True), ("A12345", False),
])
def test_code_length_boundary(bundle, code, accepted):
    _rename_code(bundle, code)
    result = inspect_history_validity_binding(bundle)
    assert (result["status"] == "VERIFIED_OFFLINE_BINDING") is accepted
    if not accepted:
        assert result["reason_codes"] == ["CODE_FORMAT_INVALID"]


@pytest.mark.parametrize("stage", [1, 2])
def test_stage1_and_stage2_do_not_call_components(bundle, monkeypatch, stage):
    def forbidden(*args, **kwargs):
        raise AssertionError("component called before its stage")
    monkeypatch.setattr(binding, "inspect_strict_input", forbidden)
    monkeypatch.setattr(binding, "_validate_history", forbidden)
    monkeypatch.setattr(binding, "inspect_strict_validity", forbidden)
    if stage == 1:
        bundle["mode"] = "history_validity_binding_fixture_v2"
        expected = "INVALID_BUNDLE"
    else:
        bundle["history"]["mode"] = "evidence_history_fixture_v2"
        expected = "HISTORY_MODE_MISMATCH"
    assert inspect_history_validity_binding(bundle)["reason_codes"] == [expected]


@pytest.mark.parametrize("matching", [True, False])
def test_shared_model_is_evaluated_exactly_once(bundle, monkeypatch, matching):
    real_validate = binding._validate_history
    calls = []

    class CountingModel:
        def __init__(self, model):
            self._model = model
            self.decision_at = model.decision_at

        def evaluate(self, at):
            calls.append(at)
            return self._model.evaluate(at)

    monkeypatch.setattr(binding, "_validate_history", lambda value: CountingModel(real_validate(value)))
    if not matching:
        bundle["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    inspect_history_validity_binding(bundle)
    assert len(calls) == 1
    assert calls[0].isoformat() == "2026-09-12T06:50:00+09:00"


def test_time_difference_skips_validity_even_with_semantically_bad_period(bundle, monkeypatch):
    bundle["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    document = bundle["validity_documents"][0]
    document["payload"]["valid_until"] = document["payload"]["valid_from"]
    _rehash_period(document)
    monkeypatch.setattr(binding, "inspect_strict_validity",
                        lambda value: (_ for _ in ()).throw(AssertionError("called")))
    assert inspect_history_validity_binding(bundle)["reason_codes"] == ["DECISION_TIME_MISMATCH"]
