"""R2-02: field-access isolation after a regressed input validator."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from aitrader import strict_validity


@pytest.mark.parametrize(
    "damage,expected_count,reasons",
    [
        ("missing_codes", 0, ["INPUT_SOURCE_REFERENCE_INVALID"]),
        ("missing_as_of", 2, ["INPUT_SOURCE_REFERENCE_INVALID"]),
        ("invalid_as_of", 2, ["INPUT_INVALID_AS_OF"]),
        ("array_input", 0, ["INPUT_SOURCE_REFERENCE_INVALID"]),
        ("missing_input", 0, ["INPUT_SOURCE_REFERENCE_INVALID", "INVALID_BUNDLE"]),
    ],
)
def test_verified_invariant_regression_in_required_fields_fails_closed(
    monkeypatch, damage, expected_count, reasons
):
    example = Path(__file__).resolve().parents[1] / "examples" / "strict_validity_valid.json"
    value = json.loads(example.read_text(encoding="utf-8"))
    assert strict_validity.inspect_strict_validity(value)["status"] == "VERIFIED_OFFLINE_VALIDITY"
    if damage == "missing_codes":
        del value["input"]["expected_codes"]
    elif damage == "missing_as_of":
        del value["input"]["as_of"]
    elif damage == "invalid_as_of":
        value["input"]["as_of"] = "not-a-time-secret"
    elif damage == "array_input":
        value["input"] = []
    else:
        del value["input"]
    before = deepcopy(value)
    monkeypatch.setattr(
        strict_validity, "inspect_strict_input",
        lambda _value: {"status": "VERIFIED_OFFLINE_INPUT", "reason_codes": []},
    )

    result = strict_validity.inspect_strict_validity(value)

    assert result == {
        "mode": "strict_validity_fixture_v1",
        "source_origin": "offline_fixture",
        "scope": "selected_lots_events",
        "status": "DATA_INCOMPLETE",
        "expected_evidence_count": expected_count,
        "verified_evidence_count": 0,
        "reason_codes": sorted(reasons),
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }
    assert value == before
