"""Check the binding proposal's fixture, not an implemented binding API."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from aitrader.evidence_history_fixture import inspect_evidence_history
from aitrader.strict_input import inspect_strict_input
from aitrader.strict_validity import inspect_strict_validity


def _example():
    path = Path(__file__).parents[1] / "examples/history_validity_binding_valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _selected_input_row(bundle, group, code):
    ref = bundle["sources"][group][code]
    document = next(doc for doc in bundle["source_documents"] if doc["id"] == ref["document_id"])
    row = document["payload"]
    for token in ref["pointer"].split("/")[1:]:
        row = row[token.replace("~1", "/").replace("~0", "~")]
    return row


def test_binding_example_components_pass_without_live_claim():
    bundle = _example()
    results = [inspect_strict_input(bundle["input"]),
               inspect_evidence_history(bundle["history"]),
               inspect_strict_validity(dict(mode="strict_validity_fixture_v1",
                                           source_origin="offline_fixture",
                                           input=bundle["input"],
                                           validity_documents=bundle["validity_documents"]))]
    for result in results:
        assert result["status"] != "DATA_INCOMPLETE"
        assert result["reason_codes"] == []
        assert result["ready_for_live"] is False
        assert result["current_signal"] is False
        assert result["read_only"] is True


def test_binding_example_same_instant_and_complete_subjects():
    bundle = _example()
    at = datetime.fromisoformat(bundle["input"]["as_of"])
    assert at == datetime.fromisoformat(bundle["history"]["decision_at"])
    required = {(group, code) for code in bundle["input"]["expected_codes"]
                for group in ("lots", "events")}
    entries = bundle["history"]["entries"]
    subjects = [(entry["subject"]["group"], entry["subject"]["code"]) for entry in entries]
    assert len(subjects) == len(set(subjects)) == len(required) == 2
    assert set(subjects) == required
    for entry in entries:
        assert entry["supersedes"] is None
        assert datetime.fromisoformat(entry["observed_at"]) <= datetime.fromisoformat(entry["recorded_at"]) <= at


@pytest.mark.parametrize("group", ["lots", "events"])
def test_binding_example_row_and_period_hashes_match(group):
    bundle = _example()
    row = _selected_input_row(bundle["input"], group, "0001")
    entry = next(e for e in bundle["history"]["entries"] if e["subject"]["group"] == group)
    assert entry["payload"] == row
    assert entry["sha256"] == _hash(row)
    period = next(d for d in bundle["validity_documents"] if d["payload"]["group"] == group)
    assert period["sha256"] == _hash(period["payload"])
    assert period["payload"]["code"] == row["code"]
    assert period["payload"]["record_sha256"] == entry["sha256"]
    at = datetime.fromisoformat(bundle["input"]["as_of"])
    assert datetime.fromisoformat(period["payload"]["valid_from"]) <= at < datetime.fromisoformat(period["payload"]["valid_until"])
    # Period evidence has no acquisition timestamps: this sample does not prove known-at.
    assert "observed_at" not in period["payload"]
    assert "recorded_at" not in period["payload"]
