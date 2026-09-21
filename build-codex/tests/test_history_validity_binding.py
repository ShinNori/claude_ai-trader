"""Product tests for the history/validity binding public API."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import aitrader.history_validity_binding as binding
from aitrader.evidence_history_fixture import inspect_evidence_history
from aitrader.history_validity_binding import inspect_history_validity_binding


EXAMPLE = Path(__file__).parents[1] / "examples" / "history_validity_binding_valid.json"
OUTPUT_KEYS = {
    "mode", "source_origin", "status", "selection_sha256", "reason_codes",
    "required_subject_count", "matched_subject_count", "extra_subject_count",
    "corrected_after_as_of_count", "period_evidence_timed", "ready_for_live",
    "current_signal", "read_only",
}


@pytest.fixture()
def bundle() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _result(bundle: object) -> dict:
    result = inspect_history_validity_binding(bundle)
    assert set(result) == OUTPUT_KEYS
    assert result["mode"] == "history_validity_binding_fixture_v1"
    assert result["source_origin"] == "offline_fixture"
    assert result["period_evidence_timed"] is False
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False
    assert result["read_only"] is True
    assert result["reason_codes"] == sorted(set(result["reason_codes"]))
    return result


def _assert_early(result: dict, reason: str) -> None:
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == [reason]
    assert result["selection_sha256"] is None
    assert [result[name] for name in (
        "required_subject_count", "matched_subject_count", "extra_subject_count",
        "corrected_after_as_of_count",
    )] == [0, 0, 0, 0]


def _rehash_source(bundle: dict) -> None:
    document = bundle["input"]["source_documents"][0]
    document["sha256"] = _digest(document["payload"])


def _rename_code(bundle: dict, code: str) -> None:
    bundle["input"]["expected_codes"] = [code]
    payload = bundle["input"]["source_documents"][0]["payload"]
    for group in ("prices", "publications", "lots", "events"):
        row = payload[group].pop("0001")
        row["code"] = code
        payload[group][code] = row
        ref = bundle["input"]["sources"][group].pop("0001")
        ref["pointer"] = f"/{group}/{code}"
        bundle["input"]["sources"][group][code] = ref
    _rehash_source(bundle)
    hashes = {}
    for entry in bundle["history"]["entries"]:
        entry["subject"]["code"] = code
        entry["payload"]["code"] = code
        entry["sha256"] = hashes[entry["subject"]["group"]] = _digest(entry["payload"])
    for document in bundle["validity_documents"]:
        document["payload"]["code"] = code
        document["payload"]["record_sha256"] = hashes[document["payload"]["group"]]
        document["sha256"] = _digest(document["payload"])


def _future_correction(bundle: dict, group: str = "lots") -> None:
    prior = next(e for e in bundle["history"]["entries"] if e["subject"]["group"] == group)
    payload = copy.deepcopy(prior["payload"])
    if group == "lots":
        payload["lot_size"] = 200
    else:
        payload["margin_regulated"] = True
    bundle["history"]["entries"].append({
        "receipt_id": f"future-{group}-receipt",
        "revision_id": f"future-{group}-revision",
        "subject": {"group": group, "code": "0001"},
        "observed_at": "2026-09-13T09:00:00+09:00",
        "recorded_at": "2026-09-13T09:00:00+09:00",
        "payload": payload,
        "sha256": _digest(payload),
        "supersedes": {"revision_id": prior["revision_id"], "sha256": prior["sha256"]},
    })


def test_valid_example_returns_verified_counts_and_history_digest(bundle):
    result = _result(bundle)
    assert result["status"] == "VERIFIED_OFFLINE_BINDING"
    assert result["reason_codes"] == []
    assert result["required_subject_count"] == result["matched_subject_count"] == 2
    assert result["extra_subject_count"] == result["corrected_after_as_of_count"] == 0
    assert result["selection_sha256"] == inspect_evidence_history(bundle["history"])["selection_sha256"]


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(mode="history_validity_binding_fixture_v2"),
    lambda b: b.update(source_origin="live"),
    lambda b: b.update(extra=True),
    lambda b: b.pop("input"),
])
def test_stage1_envelope_failures_are_private_invalid_bundle(bundle, mutation):
    mutation(bundle)
    _assert_early(_result(bundle), "INVALID_BUNDLE")


@pytest.mark.parametrize("mutation", [
    lambda h: h.update(extra=True),
    lambda h: h.pop("decision_at"),
    lambda h: h.pop("entries"),
])
def test_stage2_mode_value_wins_regardless_of_history_shape(bundle, mutation):
    bundle["history"]["mode"] = "evidence_history_fixture_v2"
    mutation(bundle["history"])
    _assert_early(_result(bundle), "HISTORY_MODE_MISMATCH")


@pytest.mark.parametrize("history", [None, [], "bad", 1])
def test_stage2_non_mapping_history_falls_through_to_stage3(bundle, history):
    bundle["history"] = history
    _assert_early(_result(bundle), "INVALID_BUNDLE")


def test_stage2_missing_mode_falls_through_to_stage3(bundle):
    bundle["history"].pop("mode")
    _assert_early(_result(bundle), "INVALID_BUNDLE")


@pytest.mark.parametrize("mutation", [
    lambda b: b["history"].update(source_origin="live"),
    lambda b: b["input"].update(mode="strict_input_v2"),
    lambda b: b["input"].update(source_origin="live"),
    lambda b: b["history"].update(entries=[]),
    lambda b: b.update(validity_documents={}),
])
def test_stage3_component_shape_and_version_failures_are_invalid_bundle(bundle, mutation):
    mutation(bundle)
    _assert_early(_result(bundle), "INVALID_BUNDLE")


def test_stage3_rejects_bad_validity_self_hash(bundle):
    bundle["validity_documents"][0]["sha256"] = "0" * 64
    _assert_early(_result(bundle), "INVALID_BUNDLE")


@pytest.mark.parametrize("target", ["code", "identifier"])
def test_stage4_format_failure_has_only_public_reason(bundle, target):
    if target == "code":
        secret = "０００１"
        _rename_code(bundle, secret)
        expected = "CODE_FORMAT_INVALID"
    else:
        secret = "PRIVATE VALUE"
        bundle["input"]["source_documents"][0]["id"] = secret
        for references in bundle["input"]["sources"].values():
            references["0001"]["document_id"] = secret
        expected = "IDENTIFIER_FORMAT_INVALID"
    _rehash_source(bundle)
    result = _result(bundle)
    assert expected in result["reason_codes"]
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert result["selection_sha256"] is None
    assert result["required_subject_count"] == 0


def test_stage5_time_mismatch_reports_safe_counts_and_stops(bundle, monkeypatch):
    _future_correction(bundle)
    bundle["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    calls = []
    monkeypatch.setattr(binding, "inspect_strict_validity", lambda value: calls.append(value))
    result = _result(bundle)
    assert result["reason_codes"] == ["DECISION_TIME_MISMATCH"]
    assert result["required_subject_count"] == 2
    assert result["matched_subject_count"] == 0
    assert result["extra_subject_count"] == 0
    assert result["corrected_after_as_of_count"] == 1
    assert result["selection_sha256"] is None
    assert calls == []


def test_empty_validity_is_stage5_but_empty_history_is_stage3(bundle):
    bundle["validity_documents"] = []
    result = _result(bundle)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert result["required_subject_count"] == 2
    assert result["matched_subject_count"] == 0


def test_selected_row_value_mismatch_reports_hash_mismatch(bundle):
    row = bundle["input"]["source_documents"][0]["payload"]["lots"]["0001"]
    row["lot_size"] = 200
    _rehash_source(bundle)
    result = _result(bundle)
    assert "ROW_HASH_MISMATCH" in result["reason_codes"]
    assert result["matched_subject_count"] == 1


def test_validity_record_hash_mismatch_is_not_structural(bundle):
    document = bundle["validity_documents"][0]
    document["payload"]["record_sha256"] = "0" * 64
    document["sha256"] = _digest(document["payload"])
    result = _result(bundle)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert result["matched_subject_count"] == 1


def test_missing_selection_and_corrected_subject_can_coexist(bundle):
    _future_correction(bundle, "lots")
    bundle["history"]["entries"] = [
        e for e in bundle["history"]["entries"] if e["subject"]["group"] != "events"
    ]
    result = _result(bundle)
    assert "HISTORY_SELECTION_MISSING" in result["reason_codes"]
    assert result["matched_subject_count"] == 1
    assert result["corrected_after_as_of_count"] == 1


def test_future_only_initial_and_correction_are_not_corrected(bundle):
    original = next(e for e in bundle["history"]["entries"] if e["subject"]["group"] == "events")
    bundle["history"]["entries"].remove(original)
    first = copy.deepcopy(original)
    first.update(receipt_id="future-first-receipt", revision_id="future-first-revision",
                 observed_at="2026-09-13T09:00:00+09:00",
                 recorded_at="2026-09-13T09:00:00+09:00")
    bundle["history"]["entries"].append(first)
    _future_correction(bundle, "events")
    result = _result(bundle)
    assert "HISTORY_SELECTION_MISSING" in result["reason_codes"]
    assert result["corrected_after_as_of_count"] == 0


def test_late_input_reason_discards_all_partial_diagnostics(bundle, monkeypatch):
    def late_failure(value):
        return {"status": "DATA_INCOMPLETE", "reason_codes": ["INPUT_INVALID_AS_OF"]}
    monkeypatch.setattr(binding, "inspect_strict_validity", late_failure)
    _assert_early(_result(bundle), "INVALID_BUNDLE")


def test_input_is_never_mutated_and_result_has_no_source_values(bundle):
    before = copy.deepcopy(bundle)
    result = _result(bundle)
    assert bundle == before
    rendered = json.dumps(result, ensure_ascii=False)
    for private in ("artificial-example", "artificial-binding-lots-receipt",
                    "artificial-period-events", "0001"):
        assert private not in rendered
