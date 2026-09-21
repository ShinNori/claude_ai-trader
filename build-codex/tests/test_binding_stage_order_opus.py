"""Claude Opus 第5回レビュー: 結合 v1 の段2/段5 の評価順を機械可読に固定する。

結合 API 本体は未実装である。本ファイルは HISTORY_VALIDITY_BINDING_PLAN.md の
記述どおりに段2・段5 を参照実装し、次の 3 点を固定する。

1. 段2 の三分（非 dict / mode キー欠落 / mode 値差）が、依存する
   ``inspect_evidence_history`` の分類と一致すること（R4-01 の採用確認）。
2. その一致が「history のキー集合が v1 と同じ」束に限られること。キー集合まで
   変わった版差の束では、文書どおりに mode だけを見る実装と、依存 API の理由を
   写像する実装とで結果が割れる（R5-01 の再現）。
3. 段5 は時点一致を先に判定し、不一致なら DECISION_TIME_MISMATCH 単独で打ち切る
   こと。件数は required/extra/corrected を返し matched=0・digest=null とする
   （R4-02 の採用確認）。corrected_after_as_of_count を as_of で評価する際、
   過去選択のない必須 subject の未来訂正を数えるかは文書が決めていない（R5-03）。

既存の実装コード・試験の期待値・共通仕様・合成データ・専用例は変更していない。
これは契約の自己整合性の固定であって、Codex 実装の検証ではない。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from aitrader.evidence_history_fixture import MODE as HISTORY_MODE
from aitrader.evidence_history_fixture import inspect_evidence_history
from aitrader.strict_input import _aware, _canonical, inspect_strict_input

BINDING_MODE = "history_validity_binding_fixture_v1"
ORIGIN = "offline_fixture"
EXAMPLE = (
    Path(__file__).resolve().parents[1] / "examples" / "history_validity_binding_valid.json"
)


@pytest.fixture()
def bundle() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


# --- 参照実装（文書の記述どおり。結合 API の実装ではない） -------------------


def _stage1(bundle: object) -> bool:
    return (
        isinstance(bundle, dict)
        and set(bundle) == {"mode", "source_origin", "input", "history", "validity_documents"}
        and bundle.get("mode") == BINDING_MODE
        and bundle.get("source_origin") == ORIGIN
    )


def _stage2_by_document(history: object) -> bool:
    """段2 の文言どおり: history が辞書で mode キーが存在し値が依存 v1 と異なる。"""
    return isinstance(history, dict) and "mode" in history and history["mode"] != HISTORY_MODE


def _stage2_by_delegation(history: object) -> bool:
    """依存 API の理由を写像する素直な実装: INVALID_MODE だけを段2 とする。"""
    return inspect_evidence_history(history)["reason_codes"] == ["INVALID_MODE"]


def _select(history: dict, at: str) -> dict[tuple[str, str], str]:
    """初出 recorded_at <= at の revision だけで subject ごとの選択を作る。"""
    moment = _aware(at)
    seen: set[str] = set()
    selected: dict[tuple[str, str], str] = {}
    for entry in history["entries"]:
        revision_id = entry["revision_id"]
        if revision_id in seen:
            continue
        seen.add(revision_id)
        subject = (entry["subject"]["group"], entry["subject"]["code"])
        if _aware(entry["recorded_at"]) <= moment:
            selected[subject] = _digest(entry["payload"])
    return selected


def _corrected(history: dict, at: str, required: set, *, selected_only: bool) -> int:
    """判定後に初出記録された訂正版（supersedes 非 null）を持つ必須 subject 数。"""
    moment = _aware(at)
    seen: set[str] = set()
    have_selection = set(_select(history, at))
    corrected: set = set()
    for entry in history["entries"]:
        revision_id = entry["revision_id"]
        if revision_id in seen:
            continue
        seen.add(revision_id)
        subject = (entry["subject"]["group"], entry["subject"]["code"])
        if subject not in required or entry["supersedes"] is None:
            continue
        if _aware(entry["recorded_at"]) <= moment:
            continue
        if selected_only and subject not in have_selection:
            continue
        corrected.add(subject)
    return len(corrected)


def _empty_counts(reason: str) -> dict:
    return {
        "reason_codes": [reason],
        "required_subject_count": 0,
        "matched_subject_count": 0,
        "extra_subject_count": 0,
        "corrected_after_as_of_count": 0,
        "selection_sha256": None,
    }


def _classify(bundle: dict, *, selected_only: bool = False) -> dict:
    """段1→段2→段3→段5 の参照実装。段4（F-01 書式）は未実装のため対象外。"""
    if not _stage1(bundle):
        return _empty_counts("INVALID_BUNDLE")
    history = bundle["history"]
    if _stage2_by_document(history):
        return _empty_counts("HISTORY_MODE_MISMATCH")
    if inspect_strict_input(bundle["input"])["status"] != "VERIFIED_OFFLINE_INPUT":
        return _empty_counts("INVALID_BUNDLE")
    if inspect_evidence_history(history)["status"] != "VERIFIED_OFFLINE_HISTORY":
        return _empty_counts("INVALID_BUNDLE")
    if not isinstance(bundle["validity_documents"], list):
        return _empty_counts("INVALID_BUNDLE")

    as_of = bundle["input"]["as_of"]
    required = {
        (group, code)
        for group in ("lots", "events")
        for code in bundle["input"]["expected_codes"]
    }
    subjects = {
        (entry["subject"]["group"], entry["subject"]["code"])
        for entry in history["entries"]
    }
    extra = len(subjects - required)
    corrected = _corrected(history, as_of, required, selected_only=selected_only)

    if _aware(as_of) != _aware(history["decision_at"]):
        # 段5 は時点一致を先に判定し、不一致ならここで打ち切る。
        return {
            "reason_codes": ["DECISION_TIME_MISMATCH"],
            "required_subject_count": len(required),
            "matched_subject_count": 0,
            "extra_subject_count": extra,
            "corrected_after_as_of_count": corrected,
            "selection_sha256": None,
        }

    selected = _select(history, history["decision_at"])
    matched = len(required & set(selected))
    reasons = set()
    if matched < len(required):
        reasons.add("HISTORY_SELECTION_MISSING")
    if extra:
        reasons.add("EXTRA_SUBJECT_PRESENT")
    return {
        "reason_codes": sorted(reasons),
        "required_subject_count": len(required),
        "matched_subject_count": matched,
        "extra_subject_count": extra,
        "corrected_after_as_of_count": corrected,
        "selection_sha256": inspect_evidence_history(history)["selection_sha256"],
    }


def _future_correction(history: dict, group: str, code: str, payload: dict) -> dict:
    """同じ subject の末尾版を参照する、判定後に記録された訂正版を作る。"""
    head = [
        entry
        for entry in history["entries"]
        if entry["subject"] == {"group": group, "code": code}
    ][-1]
    return {
        "receipt_id": f"opus-r5-{group}-correction-receipt",
        "revision_id": f"opus-r5-{group}-correction-revision",
        "subject": {"group": group, "code": code},
        "observed_at": "2026-09-13T09:00:00+09:00",
        "recorded_at": "2026-09-13T09:01:00+09:00",
        "payload": payload,
        "sha256": _digest(payload),
        "supersedes": {"revision_id": head["revision_id"], "sha256": head["sha256"]},
    }


# --- 段2 の三分が依存実装と一致する（R4-01 の採用確認） ---------------------


def test_history_non_dict_is_structural_in_dependency(bundle):
    for value in ([], "x", 3, None):
        assert inspect_evidence_history(value)["reason_codes"] == ["INVALID_BUNDLE"]


def test_history_missing_mode_key_is_structural_in_dependency(bundle):
    history = copy.deepcopy(bundle["history"])
    history.pop("mode")
    assert inspect_evidence_history(history)["reason_codes"] == ["INVALID_BUNDLE"]


@pytest.mark.parametrize(
    "value",
    ["evidence_history_fixture_v2", "", 123, None, ["evidence_history_fixture_v1"]],
)
def test_history_mode_value_difference_is_version_in_dependency(bundle, value):
    history = copy.deepcopy(bundle["history"])
    history["mode"] = value
    assert inspect_evidence_history(history)["reason_codes"] == ["INVALID_MODE"]


@pytest.mark.parametrize("value", ["evidence_history_fixture_v2", "", 123, None])
def test_stage2_and_dependency_agree_when_key_set_is_v1(bundle, value):
    history = copy.deepcopy(bundle["history"])
    history["mode"] = value
    assert _stage2_by_document(history) is True
    assert _stage2_by_delegation(history) is True


def test_stage2_does_not_fire_for_non_dict_or_missing_key(bundle):
    history = copy.deepcopy(bundle["history"])
    history.pop("mode")
    assert _stage2_by_document(history) is False
    assert _stage2_by_delegation(history) is False
    for value in ([], "x", None):
        assert _stage2_by_document(value) is False
        assert _stage2_by_delegation(value) is False


def test_binding_classifies_missing_mode_key_as_invalid_bundle(bundle):
    bundle["history"].pop("mode")
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


def test_binding_classifies_mode_value_difference_as_mode_mismatch(bundle):
    bundle["history"]["mode"] = "evidence_history_fixture_v2"
    result = _classify(bundle)
    assert result["reason_codes"] == ["HISTORY_MODE_MISMATCH"]
    assert result["required_subject_count"] == 0
    assert result["selection_sha256"] is None


# --- R5-01: キー集合まで違う版差の束で 2 つの実装が割れる -------------------


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda h: h.update(extra_key=1), id="余分キー"),
        pytest.param(lambda h: h.pop("decision_at"), id="decision_at欠落"),
    ],
)
def test_stage2_readings_diverge_when_key_set_differs(bundle, mutate):
    history = copy.deepcopy(bundle["history"])
    history["mode"] = "evidence_history_fixture_v2"
    mutate(history)
    # 依存 API は構造破損として扱う。
    assert inspect_evidence_history(history)["reason_codes"] == ["INVALID_BUNDLE"]
    # 文書どおりの段2 は版差として扱う。素直な写像実装は段2 を素通りする。
    assert _stage2_by_document(history) is True
    assert _stage2_by_delegation(history) is False


def test_history_origin_difference_has_no_mapped_reason(bundle):
    """source_origin 差は INVALID_ORIGIN。結合 9 種への写像先が文書にない（R5-02）。"""
    history = copy.deepcopy(bundle["history"])
    history["source_origin"] = "live"
    assert inspect_evidence_history(history)["reason_codes"] == ["INVALID_ORIGIN"]
    assert _stage2_by_document(history) is False


def test_input_mode_difference_falls_back_to_invalid_bundle(bundle):
    """input.mode の版差には専用理由がなく段3 の INVALID_BUNDLE になる（R5-02）。"""
    bundle["input"]["mode"] = "strict_input_v2"
    assert "INVALID_MODE" in inspect_strict_input(bundle["input"])["reason_codes"]
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


# --- 段5: 時点一致を先に判定する（R4-02 の採用確認） -----------------------


def test_matching_decision_time_succeeds(bundle):
    result = _classify(bundle)
    assert result["reason_codes"] == []
    assert result["required_subject_count"] == 2
    assert result["matched_subject_count"] == 2
    assert result["extra_subject_count"] == 0
    assert result["selection_sha256"] is not None


def test_decision_time_mismatch_is_reported_alone(bundle):
    bundle["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    bundle["history"]["entries"] = [
        entry
        for entry in bundle["history"]["entries"]
        if entry["subject"]["group"] != "events"
    ]
    result = _classify(bundle)
    # 必須 events/0001 の選択がないが、時点不一致で打ち切るので併記しない。
    assert result["reason_codes"] == ["DECISION_TIME_MISMATCH"]
    assert result["required_subject_count"] == 2
    assert result["matched_subject_count"] == 0
    assert result["extra_subject_count"] == 0
    assert result["selection_sha256"] is None


def test_same_moment_in_another_timezone_is_not_a_mismatch(bundle):
    bundle["history"]["decision_at"] = "2026-09-11T21:50:00+00:00"
    assert _classify(bundle)["reason_codes"] == []


def test_selection_missing_is_reported_when_times_match(bundle):
    bundle["history"]["entries"] = [
        entry
        for entry in bundle["history"]["entries"]
        if entry["subject"]["group"] != "events"
    ]
    result = _classify(bundle)
    assert result["reason_codes"] == ["HISTORY_SELECTION_MISSING"]
    assert result["matched_subject_count"] == 1
    assert result["selection_sha256"] is not None


# --- R5-03: corrected_after_as_of_count の評価境界 -------------------------


def test_correction_after_as_of_does_not_block_success(bundle):
    payload = {"code": "0001", "lot_size": 200}
    bundle["history"]["entries"].append(_future_correction(bundle["history"], "lots", "0001", payload))
    result = _classify(bundle)
    assert result["reason_codes"] == []
    assert result["matched_subject_count"] == 2
    assert result["corrected_after_as_of_count"] == 1


def test_future_first_revision_alone_is_not_a_correction(bundle):
    bundle["input"]["expected_codes"] = ["0001"]
    history = bundle["history"]
    history["entries"] = [
        entry for entry in history["entries"] if entry["subject"]["group"] != "events"
    ]
    payload = {"code": "0001", "next_earnings_at": None, "margin_regulated": False}
    history["entries"].append(
        {
            "receipt_id": "opus-r5-events-first-receipt",
            "revision_id": "opus-r5-events-first-revision",
            "subject": {"group": "events", "code": "0001"},
            "observed_at": "2026-09-13T09:00:00+09:00",
            "recorded_at": "2026-09-13T09:01:00+09:00",
            "payload": payload,
            "sha256": _digest(payload),
            "supersedes": None,
        }
    )
    result = _classify(bundle)
    assert result["reason_codes"] == ["HISTORY_SELECTION_MISSING"]
    assert result["corrected_after_as_of_count"] == 0


def test_future_only_subject_with_correction_is_ambiguous(bundle):
    """初版も訂正版も as_of 後の必須 subject を corrected に数えるかが未定義。"""
    history = bundle["history"]
    history["entries"] = [
        entry for entry in history["entries"] if entry["subject"]["group"] != "events"
    ]
    first = {"code": "0001", "next_earnings_at": None, "margin_regulated": False}
    history["entries"].append(
        {
            "receipt_id": "opus-r5-events-first-receipt",
            "revision_id": "opus-r5-events-first-revision",
            "subject": {"group": "events", "code": "0001"},
            "observed_at": "2026-09-13T09:00:00+09:00",
            "recorded_at": "2026-09-13T09:01:00+09:00",
            "payload": first,
            "sha256": _digest(first),
            "supersedes": None,
        }
    )
    second = {"code": "0001", "next_earnings_at": None, "margin_regulated": True}
    history["entries"].append(_future_correction(history, "events", "0001", second))
    assert inspect_evidence_history(history)["status"] == "VERIFIED_OFFLINE_HISTORY"

    literal = _classify(copy.deepcopy(bundle), selected_only=False)
    narrow = _classify(copy.deepcopy(bundle), selected_only=True)
    assert literal["reason_codes"] == ["HISTORY_SELECTION_MISSING"]
    assert narrow["reason_codes"] == ["HISTORY_SELECTION_MISSING"]
    # 文言（判定後に初出記録された訂正版が 1 件以上ある必須 subject）は 1、
    # 「選択のある必須 subject に限る」という読みは 0。どちらも現行契約に適合する。
    assert literal["corrected_after_as_of_count"] == 1
    assert narrow["corrected_after_as_of_count"] == 0


def test_reacquiring_a_revision_is_not_a_correction(bundle):
    payload = {"code": "0001", "lot_size": 200}
    history = bundle["history"]
    correction = _future_correction(history, "lots", "0001", payload)
    history["entries"].append(correction)
    duplicate = dict(correction)
    duplicate["receipt_id"] = "opus-r5-lots-correction-receipt-2"
    duplicate["recorded_at"] = "2026-09-13T09:02:00+09:00"
    duplicate["observed_at"] = "2026-09-13T09:02:00+09:00"
    history["entries"].append(duplicate)
    result = _classify(bundle)
    assert inspect_evidence_history(history)["status"] == "VERIFIED_OFFLINE_HISTORY"
    assert result["corrected_after_as_of_count"] == 1


def test_extra_subject_is_counted_without_selection(bundle):
    payload = {"code": "0002", "lot_size": 100}
    history = bundle["history"]
    history["entries"].append(
        {
            "receipt_id": "opus-r5-extra-receipt",
            "revision_id": "opus-r5-extra-revision",
            "subject": {"group": "lots", "code": "0002"},
            "observed_at": "2026-09-13T09:00:00+09:00",
            "recorded_at": "2026-09-13T09:01:00+09:00",
            "payload": payload,
            "sha256": _digest(payload),
            "supersedes": None,
        }
    )
    result = _classify(bundle)
    assert result["reason_codes"] == ["EXTRA_SUBJECT_PRESENT"]
    assert result["extra_subject_count"] == 1
    assert result["matched_subject_count"] == 2


def test_stage1_version_difference_is_invalid_bundle(bundle):
    bundle["mode"] = "history_validity_binding_fixture_v2"
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


# --- R5-04: strict_validity を呼ぶ段が未確定 -------------------------------


def test_reachable_input_reasons_are_observable_without_strict_validity(bundle):
    """段3 の INPUT_* は inspect_strict_input だけで観測できる。

    F-09 の防御が付ける INPUT_SOURCE_REFERENCE_INVALID / INPUT_INVALID_AS_OF は、
    strict_input の SOURCE_REFERENCE_INVALID / INVALID_AS_OF に INPUT_ を付けた
    ものと同じ綴りである。よって段3 で strict_validity を呼ぶ必要はなく、
    R4-06 の遅延観測は既存試験どおり検証器が退行したときだけの経路である。
    """
    bundle["input"]["sources"]["lots"]["0001"]["pointer"] = "/lots/9999"
    reasons = inspect_strict_input(bundle["input"])["reason_codes"]
    assert "SOURCE_REFERENCE_INVALID" in reasons
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]

    bundle["input"]["as_of"] = "not-a-time"
    assert "INVALID_AS_OF" in inspect_strict_input(bundle["input"])["reason_codes"]
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


def test_example_is_unchanged_and_still_verifies(bundle):
    assert set(bundle) == {"mode", "source_origin", "input", "history", "validity_documents"}
    assert inspect_strict_input(bundle["input"])["status"] == "VERIFIED_OFFLINE_INPUT"
    assert inspect_evidence_history(bundle["history"])["status"] == "VERIFIED_OFFLINE_HISTORY"
