"""Defensive isolation tests for strict-validity source resolution."""
from copy import deepcopy

import pytest

from aitrader import strict_validity
from test_strict_validity import _bundle


def _verified_input(_value):
    return {"status": "VERIFIED_OFFLINE_INPUT", "reason_codes": []}


def _assert_closed(result):
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == ["INPUT_SOURCE_REFERENCE_INVALID"]
    assert result["expected_evidence_count"] == 2
    assert result["verified_evidence_count"] == 0
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False
    assert result["read_only"] is True


@pytest.mark.parametrize("damage", ["missing_document", "missing_reference"])
def test_verified_invariant_regression_in_source_access_fails_closed(monkeypatch, damage):
    value = _bundle()
    if damage == "missing_document":
        value["input"]["source_documents"] = []
    else:
        del value["input"]["sources"]["lots"]["0001"]
    before = deepcopy(value)
    monkeypatch.setattr(strict_validity, "inspect_strict_input", _verified_input)

    _assert_closed(strict_validity.inspect_strict_validity(value))
    assert value == before


@pytest.mark.parametrize("failure", [KeyError("pointer"), IndexError("index"), ValueError("pointer")])
def test_resolution_exception_fails_closed_with_fixed_reason(monkeypatch, failure):
    value = _bundle()
    before = deepcopy(value)

    def fail_resolution(_document, _pointer):
        raise failure

    monkeypatch.setattr(strict_validity, "_resolve", fail_resolution)
    _assert_closed(strict_validity.inspect_strict_validity(value))
    assert value == before
