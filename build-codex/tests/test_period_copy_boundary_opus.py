"""Claude Opus 第9回レビュー: 期間履歴から結合 v1 への「写し」の境界。

期間証拠履歴 API（period_evidence_history_fixture_v1）はまだ実装されていない。
ここで固定するのは、PERIOD_EVIDENCE_TIMING_PLAN.md が定めた写し規則
（有効候補 1 件を {id: revision_id, sha256: 期間payloadのhash, payload: 同じpayload}
として validity_documents へ写す。0 候補は要素を作らない。余分や行 hash 不一致を隠さない）
を **実装済みの結合 v1 がどう受け取るか** である。

- 写し規則どおりなら結合 v1 は成功する（id が revision_id でも段4 を通る）。
- ただし revision_id が結合境界の ASCII 書式を満たさないと、期間履歴側で有効な候補でも
  段4 の IDENTIFIER_FORMAT_INVALID で必ず落ちる（第9回 R9-01）。
- 0 候補・余分 subject・行 hash 不一致は、いずれも結合 v1 の診断に現れる。
- 結合 v1 は候補の出所を検証しないため、期間履歴を経ていない候補でも成功する（限界の記録）。

実装コード・既存試験の期待値・共通仕様・合成データ・専用例は変更していない。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from aitrader.history_validity_binding import inspect_history_validity_binding as inspect
from aitrader.strict_input import _canonical

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "history_validity_binding_valid.json"
LOTS_REVISION = "artificial-period-lots-revision-1"
EVENTS_REVISION = "artificial-period-events-revision-1"


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@pytest.fixture()
def bundle() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _period_payload(bundle: dict, group: str, code: str = "0001") -> dict:
    """設計中の期間履歴が保持する payload（既存の専用例と同じ 5 キー）。"""
    row = bundle["input"]["source_documents"][0]["payload"][group][code]
    return {
        "group": group, "code": code, "record_sha256": _hash(row),
        "valid_from": "2026-09-10T00:00:00+09:00",
        "valid_until": "2026-09-13T00:00:00+09:00",
    }


def _copy_candidate(payload: dict, revision_id: str) -> dict:
    """写し規則: id は選択された期間 revision_id、sha256 は期間 payload の hash。"""
    return {"id": revision_id, "sha256": _hash(payload), "payload": payload}


def _copy_all(bundle: dict, *, lots_id: str = LOTS_REVISION,
              events_id: str = EVENTS_REVISION) -> dict:
    bundle["validity_documents"] = [
        _copy_candidate(_period_payload(bundle, "lots"), lots_id),
        _copy_candidate(_period_payload(bundle, "events"), events_id),
    ]
    return bundle


def _counts(result: dict) -> tuple:
    return (result["required_subject_count"], result["matched_subject_count"],
            result["extra_subject_count"], result["corrected_after_as_of_count"])


def test_copy_rule_reproduces_the_shipped_example(bundle):
    """写した payload と hash が、既存の専用例の期間証拠と一致する。"""
    original = {document["payload"]["group"]: document for document in bundle["validity_documents"]}
    for group in ("lots", "events"):
        payload = _period_payload(bundle, group)
        assert payload == original[group]["payload"]
        assert _hash(payload) == original[group]["sha256"]


def test_copied_candidates_pass_the_binding(bundle):
    result = inspect(_copy_all(bundle))
    assert result["status"] == "VERIFIED_OFFLINE_BINDING"
    assert result["reason_codes"] == []
    assert _counts(result) == (2, 2, 0, 0)


@pytest.mark.parametrize("revision_id", [
    "リビジョン-001", "revision 001", "rev​001", "_leading", "a" * 129,
])
def test_non_ascii_revision_id_cannot_be_copied_as_is(bundle, revision_id):
    """期間履歴が受理する ID でも、結合境界の書式を満たさなければ段4 で落ちる。"""
    result = inspect(_copy_all(bundle, lots_id=revision_id))
    assert result["reason_codes"] == ["IDENTIFIER_FORMAT_INVALID"]
    assert _counts(result) == (0, 0, 0, 0)
    assert result["selection_sha256"] is None


def test_ascii_revision_id_of_maximum_length_is_copyable(bundle):
    result = inspect(_copy_all(bundle, lots_id="a" * 128))
    assert result["reason_codes"] == []


def test_missing_candidate_is_reported_as_not_satisfied(bundle):
    """0 候補の subject は要素を作らない。結合 v1 では欠落として現れる。"""
    value = _copy_all(bundle)
    value["validity_documents"] = [
        document for document in value["validity_documents"]
        if document["payload"]["group"] != "events"
    ]
    result = inspect(value)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert _counts(result) == (2, 1, 0, 0)


def test_no_candidate_at_all_is_an_empty_list(bundle):
    value = _copy_all(bundle)
    value["validity_documents"] = []
    result = inspect(value)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert _counts(result) == (2, 0, 0, 0)


def test_spare_subject_candidate_is_not_hidden(bundle):
    """余分な subject の有効候補も落とさずに渡す。対象集合不一致で不成功になる。"""
    value = _copy_all(bundle)
    payload = {
        "group": "lots", "code": "0002", "record_sha256": "0" * 64,
        "valid_from": "2026-09-10T00:00:00+09:00",
        "valid_until": "2026-09-13T00:00:00+09:00",
    }
    value["validity_documents"].append(_copy_candidate(payload, "opus-r9-spare-revision"))
    result = inspect(value)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert _counts(result) == (2, 2, 0, 0)


def test_row_hash_mismatch_candidate_is_not_prefiltered(bundle):
    """行 hash が合わない候補も事前に絞り込まない。matched が 1 減る。"""
    value = _copy_all(bundle)
    document = [d for d in value["validity_documents"] if d["payload"]["group"] == "lots"][0]
    document["payload"]["record_sha256"] = "b" * 64
    document["sha256"] = _hash(document["payload"])
    result = inspect(value)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert _counts(result) == (2, 1, 0, 0)


def test_binding_cannot_tell_where_the_candidate_came_from(bundle):
    """結合 v1 は候補の出所を検証しない（設計どおりの限界。第9回 R9-05）。"""
    fabricated = copy.deepcopy(bundle)
    for document in fabricated["validity_documents"]:
        document["id"] = f"hand-made-{document['payload']['group']}"
    assert inspect(fabricated)["status"] == "VERIFIED_OFFLINE_BINDING"
    assert inspect(_copy_all(copy.deepcopy(bundle)))["status"] == "VERIFIED_OFFLINE_BINDING"


def test_expired_candidate_copied_faithfully_is_rejected(bundle):
    """区間外の版は有効候補にならない。仮に写しても結合 v1 が不適合にする。"""
    value = _copy_all(bundle)
    document = [d for d in value["validity_documents"] if d["payload"]["group"] == "lots"][0]
    document["payload"]["valid_until"] = "2026-09-11T00:00:00+09:00"
    document["sha256"] = _hash(document["payload"])
    result = inspect(value)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert _counts(result) == (2, 1, 0, 0)
