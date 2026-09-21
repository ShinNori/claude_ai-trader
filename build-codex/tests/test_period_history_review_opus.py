"""Claude Opus 第10回レビュー: 期間証拠履歴 API への独立反証。

対象は新規実装 aitrader/period_evidence_history_fixture.py。
契約は PERIOD_EVIDENCE_TIMING_PLAN.md（20 理由・判定順序・選択と有効候補・14 キー出力）。

Codex の製品試験とは独立に、次を固定する。

- 人工例の 14 キー（digest を含む）と、空配列 digest の値。
- 半開区間の 4 境界と、期限外の訂正から旧版へ戻らないこと。
- digest が期間 payload の自己 hash（entry.sha256）を使い、行 hash ではないこと。
- 未来 entry も構造検証されること。NO_OP が時刻逆行検査より先であること。
- 再取得が初出・系列末尾・選択・digest を変えないこと。
- revision 同一性 4 項のいずれの差でも REVISION_CONFLICT になること。
- 2 欠陥を同時に持つ束での理由の優先順。
- 無作為破壊 300 束で、14 キー・閉じた 20 理由・不成功時 6 件数 0・digest null・
  入力不変・決定性が崩れないこと。
- 既存 4 API を壊しても、この API が影響を受けないこと。

実装コード・既存試験の期待値・共通仕様・合成データ・既存例は変更していない。
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path

import pytest

import aitrader.evidence_history_fixture as history_v1
import aitrader.strict_validity as validity_v1
from aitrader.period_evidence_history_fixture import inspect_period_evidence_history as inspect
from aitrader.strict_input import _canonical

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "period_evidence_history_valid.json"
DECISION_AT = "2026-09-12T06:50:00+09:00"
KEYS = {
    "mode", "source_origin", "status", "reason_codes", "receipt_count", "revision_count",
    "noop_receipt_count", "series_count", "selected_series_count", "future_revision_count",
    "selection_sha256", "ready_for_live", "current_signal", "read_only",
}
CLOSED = {
    "INVALID_BUNDLE", "INVALID_MODE", "INVALID_ORIGIN", "INVALID_DECISION_AT", "INVALID_ENTRIES",
    "INVALID_ENTRY", "INVALID_IDENTIFIER", "INVALID_SUBJECT", "INVALID_TIMESTAMPS",
    "RECORDED_AT_REVERSED", "INVALID_PAYLOAD", "INVALID_HASH", "PAYLOAD_HASH_MISMATCH",
    "INVALID_SUPERSEDES", "RECEIPT_CONFLICT", "REVISION_CONFLICT", "SELECTION_HASH_INVALID",
    "PERIOD_INTERVAL_INVALID", "SERIES_REFERENCE_INVALID", "PERIOD_OVERLAP_AT_DECISION",
}
COUNTS = ("receipt_count", "revision_count", "noop_receipt_count", "series_count",
          "selected_series_count", "future_revision_count")


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


EMPTY_DIGEST = _hash([])


@pytest.fixture()
def bundle() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _entry(bundle: dict, group: str) -> dict:
    return [e for e in bundle["entries"] if e["subject"]["group"] == group][0]


def _reseal(entry: dict) -> dict:
    entry["sha256"] = _hash(entry["payload"])
    return entry


def _new_entry(source: dict, *, suffix: str, when: str, series_id: str | None = None,
               supersedes=None, payload: dict | None = None) -> dict:
    entry = copy.deepcopy(source)
    entry["receipt_id"] = f"opus-r10-{suffix}-receipt"
    entry["revision_id"] = f"opus-r10-{suffix}-revision"
    entry["series_id"] = series_id or source["series_id"]
    entry["observed_at"] = when
    entry["recorded_at"] = when
    entry["supersedes"] = supersedes
    if payload is not None:
        entry["payload"] = payload
    return _reseal(entry)


def _check(result: dict) -> dict:
    assert set(result) == KEYS
    assert result["mode"] == "period_evidence_history_fixture_v1"
    assert result["source_origin"] == "offline_fixture"
    assert (result["ready_for_live"], result["current_signal"], result["read_only"]) == (
        False, False, True)
    reasons = result["reason_codes"]
    assert set(reasons) <= CLOSED
    if reasons:
        assert len(reasons) == 1
        assert result["status"] == "DATA_INCOMPLETE"
        assert [result[name] for name in COUNTS] == [0, 0, 0, 0, 0, 0]
        assert result["selection_sha256"] is None
    else:
        assert result["status"] == "VERIFIED_OFFLINE_PERIOD_HISTORY"
        assert result["selection_sha256"] is not None
    return result


# --- 人工例と digest -----------------------------------------------------------


def test_example_returns_the_documented_fourteen_keys(bundle):
    result = _check(inspect(bundle))
    assert [result[name] for name in COUNTS] == [3, 3, 0, 2, 2, 1]
    assert result["selection_sha256"] == (
        "ba6486dd33d76cf9594f6e43795f45c70349bee87e40fa6f13e0abcb01d186ca")


def test_digest_uses_the_period_payload_self_hash(bundle):
    """射影の sha256 は entry.sha256。行 hash（record_sha256）ではない。"""
    projection = []
    for group in ("events", "lots"):
        entry = _entry(bundle, group)
        projection.append({
            "group": group, "code": "0001", "series_id": entry["series_id"],
            "revision_id": entry["revision_id"], "sha256": entry["sha256"],
        })
    result = inspect(bundle)
    assert result["selection_sha256"] == _hash(projection)
    row_based = copy.deepcopy(projection)
    for item, group in zip(row_based, ("events", "lots")):
        item["sha256"] = _entry(bundle, group)["payload"]["record_sha256"]
    assert result["selection_sha256"] != _hash(row_based)


@pytest.mark.parametrize("valid_from,valid_until,is_candidate", [
    (DECISION_AT, "2026-09-13T00:00:00+09:00", True),
    ("2026-09-10T00:00:00+09:00", DECISION_AT, False),
    ("2026-09-12T06:50:01+09:00", "2026-09-13T00:00:00+09:00", False),
    ("2026-09-10T00:00:00+09:00", "2026-09-12T06:49:59+09:00", False),
])
def test_half_open_interval_boundaries(bundle, valid_from, valid_until, is_candidate):
    single = _entry(bundle, "events")
    single["payload"] = dict(single["payload"],
                             valid_from=valid_from, valid_until=valid_until)
    bundle["entries"] = [_reseal(single)]
    result = _check(inspect(bundle))
    assert result["selected_series_count"] == 1
    assert (result["selection_sha256"] != EMPTY_DIGEST) is is_candidate


def test_no_candidate_still_succeeds_with_the_empty_digest(bundle):
    single = _entry(bundle, "events")
    single["payload"] = dict(single["payload"],
                             valid_from="2026-09-20T00:00:00+09:00",
                             valid_until="2026-09-30T00:00:00+09:00")
    bundle["entries"] = [_reseal(single)]
    result = _check(inspect(bundle))
    assert result["reason_codes"] == []
    assert result["selected_series_count"] == 1
    assert result["selection_sha256"] == EMPTY_DIGEST


def test_expired_selection_does_not_fall_back_to_the_older_revision(bundle):
    """判定前に記録された訂正が区間外なら、その系列は候補なし。初版へ戻らない。"""
    lots = _entry(bundle, "lots")
    correction = _new_entry(
        lots, suffix="expired", when="2026-09-11T00:00:00+09:00",
        supersedes={"revision_id": lots["revision_id"], "sha256": lots["sha256"]},
        payload=dict(lots["payload"], valid_from="2026-09-20T00:00:00+09:00",
                     valid_until="2026-09-30T00:00:00+09:00"))
    events = _entry(bundle, "events")
    bundle["entries"] = [lots, correction, events]
    result = _check(inspect(bundle))
    assert result["selected_series_count"] == 2
    assert result["selection_sha256"] == _hash([{
        "group": "events", "code": "0001", "series_id": events["series_id"],
        "revision_id": events["revision_id"], "sha256": events["sha256"],
    }])


def test_two_candidates_for_one_subject_reject_everything(bundle):
    events = _entry(bundle, "events")
    twin = _new_entry(events, suffix="twin", when="2026-09-11T18:00:00+09:00",
                      series_id="opus-r10-events-series-2")
    bundle["entries"] = [events, twin]
    result = _check(inspect(bundle))
    assert result["reason_codes"] == ["PERIOD_OVERLAP_AT_DECISION"]


def test_observed_before_but_recorded_after_is_not_selected(bundle):
    single = _entry(bundle, "events")
    single["observed_at"] = "2026-09-11T17:00:00+09:00"
    single["recorded_at"] = "2026-09-13T09:00:00+09:00"
    bundle["entries"] = [single]
    result = _check(inspect(bundle))
    assert result["selected_series_count"] == 0
    assert result["future_revision_count"] == 1
    assert result["selection_sha256"] == EMPTY_DIGEST


# --- 未来 entry・NO_OP・再取得 -------------------------------------------------


@pytest.mark.parametrize("damage,expected", [
    ("payload_keys", "INVALID_PAYLOAD"),
    ("self_hash", "PAYLOAD_HASH_MISMATCH"),
    ("self_reference", "INVALID_SUPERSEDES"),
])
def test_future_entries_are_validated_too(bundle, damage, expected):
    events = _entry(bundle, "events")
    future = _new_entry(events, suffix="future", when="2026-09-14T09:00:00+09:00",
                        series_id="opus-r10-future-series")
    if damage == "payload_keys":
        future["payload"] = dict(future["payload"], extra=1)
        future["sha256"] = _hash(future["payload"])
    elif damage == "self_hash":
        future["sha256"] = "a" * 64
    else:
        future["supersedes"] = {"revision_id": future["revision_id"],
                                "sha256": future["sha256"]}
    bundle["entries"] = [events, future]
    assert _check(inspect(bundle))["reason_codes"] == [expected]


def test_noop_is_handled_before_the_reversal_check(bundle):
    lots = _entry(bundle, "lots")
    events = _entry(bundle, "events")
    replay = copy.deepcopy(lots)
    bundle["entries"] = [lots, events, replay]
    result = _check(inspect(bundle))
    assert result["reason_codes"] == []
    assert result["noop_receipt_count"] == 1
    assert result["receipt_count"] == 2


def test_reacquiring_a_revision_changes_nothing(bundle):
    baseline = inspect(copy.deepcopy(bundle))
    events = _entry(bundle, "events")
    again = copy.deepcopy(events)
    again["receipt_id"] = "opus-r10-again-receipt"
    again["observed_at"] = again["recorded_at"] = "2026-09-14T09:00:00+09:00"
    bundle["entries"].append(again)
    result = _check(inspect(bundle))
    assert result["revision_count"] == baseline["revision_count"]
    assert result["future_revision_count"] == baseline["future_revision_count"]
    assert result["selected_series_count"] == baseline["selected_series_count"]
    assert result["selection_sha256"] == baseline["selection_sha256"]
    assert result["receipt_count"] == baseline["receipt_count"] + 1


@pytest.mark.parametrize("field", ["series_id", "subject", "payload", "supersedes"])
def test_revision_identity_is_four_fields(bundle, field):
    events = _entry(bundle, "events")
    lots = _entry(bundle, "lots")
    again = copy.deepcopy(events)
    again["receipt_id"] = "opus-r10-identity-receipt"
    again["observed_at"] = again["recorded_at"] = "2026-09-13T09:00:00+09:00"
    if field == "series_id":
        again["series_id"] = "opus-r10-other-series"
    elif field == "subject":
        again["subject"] = {"group": "lots", "code": "0002"}
        again["payload"] = dict(again["payload"], group="lots", code="0002")
        _reseal(again)
    elif field == "payload":
        again["payload"] = dict(again["payload"], valid_until="2026-09-15T00:00:00+09:00")
        _reseal(again)
    else:
        again["supersedes"] = {"revision_id": lots["revision_id"], "sha256": lots["sha256"]}
    bundle["entries"].append(again)
    assert _check(inspect(bundle))["reason_codes"] == ["REVISION_CONFLICT"]


# --- 系列と理由の優先順 ---------------------------------------------------------


def test_known_series_second_initial_revision_is_invalid_supersedes(bundle):
    events = _entry(bundle, "events")
    twin = _new_entry(events, suffix="second-initial", when="2026-09-11T18:00:00+09:00")
    bundle["entries"] = [events, twin]
    assert _check(inspect(bundle))["reason_codes"] == ["INVALID_SUPERSEDES"]


def test_subject_change_in_a_known_series_wins_over_supersedes(bundle):
    events = _entry(bundle, "events")
    other = _new_entry(events, suffix="subject-change", when="2026-09-11T18:00:00+09:00")
    other["subject"] = {"group": "lots", "code": "0001"}
    other["payload"] = dict(other["payload"], group="lots")
    _reseal(other)
    bundle["entries"] = [events, other]
    assert _check(inspect(bundle))["reason_codes"] == ["SERIES_REFERENCE_INVALID"]


def test_cross_series_supersedes_is_series_reference_invalid(bundle):
    lots = _entry(bundle, "lots")
    events = _entry(bundle, "events")
    other = _new_entry(events, suffix="cross", when="2026-09-11T18:00:00+09:00",
                       series_id="opus-r10-cross-series",
                       supersedes={"revision_id": lots["revision_id"], "sha256": lots["sha256"]})
    bundle["entries"] = [lots, events, other]
    assert _check(inspect(bundle))["reason_codes"] == ["SERIES_REFERENCE_INVALID"]


@pytest.mark.parametrize("damage,expected", [
    ("entry_keys_and_identifier", "INVALID_ENTRY"),
    ("identifier_and_subject", "INVALID_IDENTIFIER"),
    ("subject_and_payload", "INVALID_SUBJECT"),
    ("timestamps_and_payload", "INVALID_TIMESTAMPS"),
    ("payload_and_interval", "INVALID_PAYLOAD"),
    ("interval_and_self_hash", "PERIOD_INTERVAL_INVALID"),
])
def test_first_failing_check_wins(bundle, damage, expected):
    entry = _entry(bundle, "events")
    bundle["entries"] = [entry]
    if damage == "entry_keys_and_identifier":
        entry["extra"] = 1
        entry["receipt_id"] = " "
    elif damage == "identifier_and_subject":
        entry["receipt_id"] = " "
        entry["subject"] = {"group": "prices", "code": "0001"}
    elif damage == "subject_and_payload":
        entry["subject"] = {"group": "prices", "code": "0001"}
        entry["payload"] = dict(entry["payload"], extra=1)
    elif damage == "timestamps_and_payload":
        entry["observed_at"] = "2026-09-13T00:00:00+09:00"
        entry["payload"] = dict(entry["payload"], extra=1)
    elif damage == "payload_and_interval":
        entry["payload"] = dict(entry["payload"], extra=1,
                                valid_until="2026-09-01T00:00:00+09:00")
    else:
        entry["payload"] = dict(entry["payload"], valid_until="2026-09-01T00:00:00+09:00")
        entry["sha256"] = "b" * 64
    assert _check(inspect(bundle))["reason_codes"] == [expected]


# --- 無作為破壊と独立性 ---------------------------------------------------------


def test_random_damage_keeps_the_closed_contract(bundle):
    rng = random.Random(20260914)
    values = [None, 0, 1, "", "x", [], {}, True, 1.5, "not-a-time", "0" * 64, "0" * 63,
              "０００１", " 0001", "lots", "prices", DECISION_AT]
    for _ in range(300):
        value = copy.deepcopy(bundle)
        for _ in range(rng.randint(1, 3)):
            targets = [value]
            entries = value.get("entries")
            entry = entries[0] if isinstance(entries, list) and entries else None
            if isinstance(entry, dict):
                for node in (entry, entry.get("subject"), entry.get("payload")):
                    if isinstance(node, dict) and node:
                        targets.append(node)
            target = rng.choice(targets)
            key = rng.choice(list(target))
            if rng.getrandbits(1):
                target[key] = rng.choice(values)
            else:
                del target[key]
        snapshot = json.dumps(value, sort_keys=True, default=str)
        result = _check(inspect(value))
        assert json.dumps(value, sort_keys=True, default=str) == snapshot
        assert inspect(value) == result


def test_period_api_does_not_depend_on_the_four_existing_apis(bundle, monkeypatch):
    """既存 4 API を壊しても期間履歴 API は影響を受けない。"""
    def explode(*args, **kwargs):
        raise AssertionError("既存 API を呼んではならない")

    monkeypatch.setattr(history_v1, "inspect_evidence_history", explode)
    monkeypatch.setattr(validity_v1, "inspect_strict_validity", explode)
    result = _check(inspect(bundle))
    assert result["reason_codes"] == []
