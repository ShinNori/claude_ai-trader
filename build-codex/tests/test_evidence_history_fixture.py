"""Adversarial tests for the pure-offline evidence history proposal."""
from copy import deepcopy
import hashlib
import json

import pytest

from aitrader.evidence_history_fixture import inspect_evidence_history


SECRET = "secret-history-receipt-and-revision"


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _payload(group, code="0001", version=1):
    if group == "lots":
        return {"code": code, "lot_size": 100 * version}
    return {
        "code": code,
        "next_earnings_at": None if version == 1 else "2026-10-30T15:00:00+09:00",
        "margin_regulated": version > 1,
    }


def _entry(
    receipt_id, revision_id, group, *, version=1,
    observed_at="2026-09-12T05:00:00+09:00",
    recorded_at="2026-09-12T06:00:00+09:00", supersedes=None,
):
    payload = _payload(group, version=version)
    return {
        "receipt_id": receipt_id,
        "revision_id": revision_id,
        "subject": {"group": group, "code": "0001"},
        "observed_at": observed_at,
        "recorded_at": recorded_at,
        "payload": payload,
        "sha256": _digest(payload),
        "supersedes": supersedes,
    }


def _bundle():
    lot1 = _entry("receipt-lot-1", "lot-r1", "lots")
    event1 = _entry(
        "receipt-event-1", "event-r1", "events",
        recorded_at="2026-09-12T06:10:00+09:00",
    )
    lot2 = _entry(
        "receipt-lot-2", "lot-r2", "lots", version=2,
        observed_at="2026-09-12T06:15:00+09:00",
        recorded_at="2026-09-12T06:20:00+09:00",
        supersedes={"revision_id": "lot-r1", "sha256": lot1["sha256"]},
    )
    return {
        "mode": "evidence_history_fixture_v1",
        "source_origin": "offline_fixture",
        "decision_at": "2026-09-12T07:00:00+09:00",
        "entries": [lot1, event1, lot2],
    }


def _selection_digest(entries):
    selected = [
        {
            "group": entry["subject"]["group"],
            "code": entry["subject"]["code"],
            "revision_id": entry["revision_id"],
            "sha256": entry["sha256"],
        }
        for entry in entries
    ]
    selected.sort(key=lambda item: (item["group"], item["code"]))
    return _digest(selected)


def _assert_incomplete(value, reason=None):
    before = deepcopy(value)
    result = inspect_evidence_history(value)
    assert value == before
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == sorted(result["reason_codes"])
    if reason:
        assert reason in result["reason_codes"]
    for field in (
        "receipt_count", "revision_count", "noop_receipt_count",
        "selected_revision_count", "future_revision_count",
    ):
        assert result[field] == 0
    assert result["selection_sha256"] is None
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False
    assert result["read_only"] is True
    return result


def test_valid_history_returns_exact_metadata_and_independent_digest():
    value = _bundle()
    expected_digest = _selection_digest([value["entries"][1], value["entries"][2]])
    assert inspect_evidence_history(value) == {
        "mode": "evidence_history_fixture_v1",
        "source_origin": "offline_fixture",
        "status": "VERIFIED_OFFLINE_HISTORY",
        "receipt_count": 3,
        "revision_count": 3,
        "noop_receipt_count": 0,
        "selected_revision_count": 2,
        "future_revision_count": 0,
        "selection_sha256": expected_digest,
        "reason_codes": [],
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }


def test_exact_receipt_replay_is_noop_even_after_mixed_entries():
    value = _bundle()
    replay = deepcopy(value["entries"][0])
    value["entries"].append(replay)
    result = inspect_evidence_history(value)
    assert result["status"] == "VERIFIED_OFFLINE_HISTORY"
    assert result["receipt_count"] == 3
    assert result["revision_count"] == 3
    assert result["noop_receipt_count"] == 1


def test_exact_replay_is_detected_before_reverse_clock_check():
    value = _bundle()
    replay = deepcopy(value["entries"][0])
    value["entries"].append(replay)
    assert inspect_evidence_history(value)["status"] == "VERIFIED_OFFLINE_HISTORY"


def test_same_receipt_with_changed_content_is_rejected():
    value = _bundle()
    changed = deepcopy(value["entries"][0])
    changed["payload"]["lot_size"] = 999
    changed["sha256"] = _digest(changed["payload"])
    value["entries"].append(changed)
    _assert_incomplete(value, "RECEIPT_CONFLICT")


def test_rereceiving_same_revision_under_new_receipt_does_not_add_revision():
    value = _bundle()
    rereceived = deepcopy(value["entries"][0])
    rereceived["receipt_id"] = "receipt-lot-1-copy"
    rereceived["recorded_at"] = "2026-09-12T06:30:00+09:00"
    value["entries"].append(rereceived)
    result = inspect_evidence_history(value)
    assert result["status"] == "VERIFIED_OFFLINE_HISTORY"
    assert result["receipt_count"] == 4
    assert result["revision_count"] == 3
    assert result["selected_revision_count"] == 2


def test_rereceiving_old_revision_does_not_roll_back_current_head():
    value = _bundle()
    old = deepcopy(value["entries"][0])
    old["receipt_id"] = "late-old-receipt"
    old["recorded_at"] = "2026-09-12T06:30:00+09:00"
    value["entries"].append(old)
    result = inspect_evidence_history(value)
    expected = _selection_digest([value["entries"][1], value["entries"][2]])
    assert result["selection_sha256"] == expected


def test_new_revision_may_repeat_same_payload_value_with_valid_parent():
    value = _bundle()
    prior = value["entries"][2]
    repeated_value = _entry(
        "receipt-lot-3", "lot-r3", "lots", version=2,
        observed_at="2026-09-12T06:25:00+09:00",
        recorded_at="2026-09-12T06:30:00+09:00",
        supersedes={"revision_id": prior["revision_id"], "sha256": prior["sha256"]},
    )
    value["entries"].append(repeated_value)
    result = inspect_evidence_history(value)
    assert result["status"] == "VERIFIED_OFFLINE_HISTORY"
    assert result["revision_count"] == 4


@pytest.mark.parametrize("mutation", ["missing", "bad-hash", "cross-subject", "fork", "self", "forward"])
def test_corrections_require_the_exact_current_head(mutation):
    value = _bundle()
    correction = value["entries"][2]
    if mutation == "missing":
        correction["supersedes"] = None
    elif mutation == "bad-hash":
        correction["supersedes"]["sha256"] = "0" * 64
    elif mutation == "cross-subject":
        correction["supersedes"] = {
            "revision_id": "event-r1", "sha256": value["entries"][1]["sha256"],
        }
    elif mutation == "fork":
        correction["supersedes"] = {
            "revision_id": "missing-revision", "sha256": "0" * 64,
        }
    elif mutation == "self":
        correction["supersedes"] = {
            "revision_id": "lot-r2", "sha256": correction["sha256"],
        }
    else:
        correction["supersedes"] = {
            "revision_id": "lot-r3", "sha256": "0" * 64,
        }
    _assert_incomplete(value, "INVALID_SUPERSEDES")


def test_future_correction_validates_but_does_not_change_past_selection():
    value = _bundle()
    value["decision_at"] = "2026-09-12T06:15:00+09:00"
    result = inspect_evidence_history(value)
    expected = _selection_digest([value["entries"][0], value["entries"][1]])
    assert result["status"] == "VERIFIED_OFFLINE_HISTORY"
    assert result["future_revision_count"] == 1
    assert result["selection_sha256"] == expected


def test_future_chain_cannot_leak_new_payload_into_historical_digest():
    value = _bundle()
    value["decision_at"] = "2026-09-12T06:15:00+09:00"
    before = inspect_evidence_history(value)["selection_sha256"]
    value["entries"][2]["payload"]["lot_size"] = 300
    value["entries"][2]["sha256"] = _digest(value["entries"][2]["payload"])
    after = inspect_evidence_history(value)["selection_sha256"]
    assert after == before


def test_observed_at_after_recorded_at_is_rejected():
    value = _bundle()
    value["entries"][0]["observed_at"] = "2026-09-12T06:00:00.000001+09:00"
    _assert_incomplete(value, "INVALID_TIMESTAMPS")


def test_recorded_clock_must_be_nondecreasing_for_new_receipts():
    value = _bundle()
    value["entries"][1]["recorded_at"] = "2026-09-12T05:59:59+09:00"
    _assert_incomplete(value, "RECORDED_AT_REVERSED")


def test_timezone_equivalent_clock_values_are_accepted():
    value = _bundle()
    value["entries"][0]["observed_at"] = "2026-09-11T20:00:00+00:00"
    value["entries"][0]["recorded_at"] = "2026-09-11T21:00:00+00:00"
    assert inspect_evidence_history(value)["status"] == "VERIFIED_OFFLINE_HISTORY"


def test_payload_hash_must_match_canonical_payload():
    value = _bundle()
    value["entries"][0]["payload"]["lot_size"] = 200
    _assert_incomplete(value, "PAYLOAD_HASH_MISMATCH")


@pytest.mark.parametrize("group", ["lots", "events"])
def test_payload_must_match_subject_group_strict_row_shape(group):
    value = _bundle()
    entry = next(item for item in value["entries"] if item["subject"]["group"] == group)
    entry["payload"]["extra"] = SECRET
    entry["sha256"] = _digest(entry["payload"])
    _assert_incomplete(value, "INVALID_PAYLOAD")


def test_exact_envelope_and_entry_shapes_reject_extra_fields():
    for target in ("bundle", "entry", "subject"):
        value = _bundle()
        if target == "bundle":
            value["extra"] = True
        elif target == "entry":
            value["entries"][0]["extra"] = True
        else:
            value["entries"][0]["subject"]["extra"] = True
        reason = "INVALID_BUNDLE" if target == "bundle" else (
            "INVALID_ENTRY" if target == "entry" else "INVALID_SUBJECT"
        )
        _assert_incomplete(value, reason)


@pytest.mark.parametrize("value", [None, 1, True, [], "history"])
def test_bad_top_level_types_fail_closed_without_exception(value):
    result = inspect_evidence_history(value)
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["selection_sha256"] is None
    assert result["reason_codes"] == sorted(result["reason_codes"])
    assert result["ready_for_live"] is False
    assert result["read_only"] is True


def test_input_is_immutable_on_success_and_failure():
    value = _bundle()
    before = deepcopy(value)
    inspect_evidence_history(value)
    assert value == before
    value["entries"][0]["sha256"] = "0" * 64
    _assert_incomplete(value)


def test_failure_never_echoes_ids_payload_values_or_times():
    value = _bundle()
    value["entries"][0]["receipt_id"] = SECRET
    value["entries"][0]["revision_id"] = SECRET
    value["entries"][0]["observed_at"] = "bad-" + SECRET
    result = _assert_incomplete(value)
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    for raw in (SECRET, "0001", "lot-r2", "200", "2026-09-12T06:20:00+09:00"):
        assert raw not in rendered
