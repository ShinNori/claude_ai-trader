"""Claude Opus 第6回レビュー: R5-01〜05 で確定した読みを機械可読に固定する。

結合 API / CLI・内部共用選択モデルは未実装である。本ファイルは
HISTORY_VALIDITY_BINDING_PLAN.md の確定後の記述どおりに段1〜段5 を参照実装し、
第5回で二読に割れていた箇所が一意に決まったことを固定する。

- R5-01: 段2 は依存 API を呼ばず history の dict 性・mode キーの有無・値だけを見る。
  mode 値差とキー過不足が併存しても段2 で HISTORY_MODE_MISMATCH 単独。
- R5-02: history.source_origin 差、input.mode / input.source_origin 差は段3 の
  INVALID_BUNDLE 単独。最上位封筒の版差は段1。
- R5-03: corrected は as_of 時点で選択のある必須 subject に限る。未来初版＋未来訂正版
  だけの subject は 0。別 subject の選択欠落と corrected>0 は併存し得る。
- R5-04: 時点不一致では strict_validity を呼ばない（遅延 INPUT_* 防御に入らない）。
- R5-05: 読取境界は 1,048,576 バイトちょうどまで通し、超過で読取自体を拒否する。

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
from aitrader.packet_cli import _mapping
from aitrader.strict_input import _aware, _canonical, inspect_strict_input
from aitrader.strict_validity import inspect_strict_validity

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


# --- 参照実装（確定後の文書どおり。結合 API の実装ではない） -----------------


def _select(history: dict, at: str) -> dict[tuple[str, str], str]:
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


def _corrected(history: dict, at: str, required: set) -> int:
    """as_of 時点で選択のある必須 subject のうち、判定後初出の訂正版を持つ数。"""
    moment = _aware(at)
    selected = set(_select(history, at))
    seen: set[str] = set()
    corrected: set = set()
    for entry in history["entries"]:
        revision_id = entry["revision_id"]
        if revision_id in seen:
            continue
        seen.add(revision_id)
        subject = (entry["subject"]["group"], entry["subject"]["code"])
        if subject not in required or subject not in selected:
            continue
        if entry["supersedes"] is None or _aware(entry["recorded_at"]) <= moment:
            continue
        corrected.add(subject)
    return len(corrected)


def _counts(reason: str) -> dict:
    return {
        "reason_codes": [reason],
        "required_subject_count": 0,
        "matched_subject_count": 0,
        "extra_subject_count": 0,
        "corrected_after_as_of_count": 0,
        "selection_sha256": None,
    }


def _classify(bundle: dict, calls: list | None = None) -> dict:
    """段1→段2→段3→段5 の参照実装。段4（F-01 書式）は対象外。

    calls には strict_validity を呼んだ回数を記録する（R5-04 の確認用）。
    """
    calls = [] if calls is None else calls
    if (
        not isinstance(bundle, dict)
        or set(bundle) != {"mode", "source_origin", "input", "history", "validity_documents"}
        or bundle.get("mode") != BINDING_MODE
        or bundle.get("source_origin") != ORIGIN
    ):
        return _counts("INVALID_BUNDLE")

    history = bundle["history"]
    # 段2: 依存 API を呼ばず、dict 性・mode キーの有無・値だけを直接見る。
    if isinstance(history, dict) and "mode" in history and history["mode"] != HISTORY_MODE:
        return _counts("HISTORY_MODE_MISMATCH")

    if inspect_strict_input(bundle["input"])["status"] != "VERIFIED_OFFLINE_INPUT":
        return _counts("INVALID_BUNDLE")
    if inspect_evidence_history(history)["status"] != "VERIFIED_OFFLINE_HISTORY":
        return _counts("INVALID_BUNDLE")
    documents = bundle["validity_documents"]
    if not isinstance(documents, list):
        return _counts("INVALID_BUNDLE")

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
    corrected = _corrected(history, as_of, required)

    if _aware(as_of) != _aware(history["decision_at"]):
        # 段5 は時点一致を先に判定する。ここで打ち切り、部品を呼ばない。
        return {
            "reason_codes": ["DECISION_TIME_MISMATCH"],
            "required_subject_count": len(required),
            "matched_subject_count": 0,
            "extra_subject_count": extra,
            "corrected_after_as_of_count": corrected,
            "selection_sha256": None,
        }

    validity = inspect_strict_validity(
        {
            "mode": "strict_validity_fixture_v1",
            "source_origin": ORIGIN,
            "input": bundle["input"],
            "validity_documents": documents,
        }
    )
    calls.append(validity["status"])
    if any(reason.startswith("INPUT_") for reason in validity["reason_codes"]):
        return _counts("INVALID_BUNDLE")

    selected = _select(history, as_of)
    matched = len(required & set(selected))
    reasons = set()
    if matched < len(required):
        reasons.add("HISTORY_SELECTION_MISSING")
    if extra:
        reasons.add("EXTRA_SUBJECT_PRESENT")
    if validity["status"] != "VERIFIED_OFFLINE_VALIDITY":
        reasons.add("VALIDITY_NOT_SATISFIED")
    return {
        "reason_codes": sorted(reasons),
        "required_subject_count": len(required),
        "matched_subject_count": matched,
        "extra_subject_count": extra,
        "corrected_after_as_of_count": corrected,
        "selection_sha256": inspect_evidence_history(history)["selection_sha256"],
    }


def _future_entry(group: str, code: str, payload: dict, supersedes, suffix: str, when: str) -> dict:
    return {
        "receipt_id": f"opus-r6-{group}-{suffix}-receipt",
        "revision_id": f"opus-r6-{group}-{suffix}-revision",
        "subject": {"group": group, "code": code},
        "observed_at": when,
        "recorded_at": when,
        "payload": payload,
        "sha256": _digest(payload),
        "supersedes": supersedes,
    }


def _head(history: dict, group: str, code: str) -> dict:
    return [
        entry
        for entry in history["entries"]
        if entry["subject"] == {"group": group, "code": code}
    ][-1]


# --- R5-01: 段2 はキー集合に依らず mode 値だけで決まる ----------------------


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda h: h.update(extra_key=1), id="余分キー"),
        pytest.param(lambda h: h.pop("decision_at"), id="decision_at欠落"),
        pytest.param(lambda h: h.pop("entries"), id="entries欠落"),
        pytest.param(lambda h: None, id="キー集合はv1のまま"),
    ],
)
def test_mode_value_difference_stops_at_stage2_regardless_of_key_set(bundle, mutate):
    bundle["history"]["mode"] = "evidence_history_fixture_v2"
    mutate(bundle["history"])
    result = _classify(bundle)
    assert result["reason_codes"] == ["HISTORY_MODE_MISMATCH"]
    assert result["required_subject_count"] == 0
    assert result["selection_sha256"] is None


def test_dependency_still_calls_those_bundles_structural(bundle):
    """段2 が依存 API の分類と意図的に分かれる境界を残しておく。"""
    history = copy.deepcopy(bundle["history"])
    history["mode"] = "evidence_history_fixture_v2"
    history["extra_key"] = 1
    assert inspect_evidence_history(history)["reason_codes"] == ["INVALID_BUNDLE"]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda h: h.pop("mode"), id="modeキー欠落"),
        pytest.param(lambda h: h.update(extra_key=1), id="正しいmode＋余分キー"),
    ],
)
def test_structural_history_falls_to_stage3(bundle, mutate):
    mutate(bundle["history"])
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


@pytest.mark.parametrize("value", [[], "x", 3, None])
def test_non_dict_history_falls_to_stage3(bundle, value):
    bundle["history"] = value
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


# --- R5-02: mode 以外の版差・origin 差は段3 -------------------------------


def test_history_origin_difference_is_stage3(bundle):
    bundle["history"]["source_origin"] = "live"
    assert inspect_evidence_history(bundle["history"])["reason_codes"] == ["INVALID_ORIGIN"]
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


def test_input_mode_difference_is_stage3(bundle):
    bundle["input"]["mode"] = "strict_input_v2"
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


def test_input_origin_difference_is_stage3(bundle):
    bundle["input"]["source_origin"] = "live"
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


def test_binding_envelope_difference_is_stage1(bundle):
    bundle["mode"] = "history_validity_binding_fixture_v2"
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


def test_no_tenth_reason_code_is_introduced(bundle):
    closed = {
        "DECISION_TIME_MISMATCH", "HISTORY_SELECTION_MISSING", "ROW_HASH_MISMATCH",
        "VALIDITY_NOT_SATISFIED", "EXTRA_SUBJECT_PRESENT", "CODE_FORMAT_INVALID",
        "IDENTIFIER_FORMAT_INVALID", "HISTORY_MODE_MISMATCH", "INVALID_BUNDLE",
    }
    variants = [
        lambda b: b["history"].update(source_origin="live"),
        lambda b: b["input"].update(mode="strict_input_v2"),
        lambda b: b["history"].update(mode="evidence_history_fixture_v2"),
        lambda b: b.update(mode="history_validity_binding_fixture_v2"),
        lambda b: b["history"].pop("mode"),
    ]
    for mutate in variants:
        value = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        mutate(value)
        assert set(_classify(value)["reason_codes"]) <= closed


# --- R5-03: corrected は選択のある必須 subject に限る ----------------------


def test_future_only_subject_with_correction_is_not_counted(bundle):
    history = bundle["history"]
    history["entries"] = [
        entry for entry in history["entries"] if entry["subject"]["group"] != "events"
    ]
    first = {"code": "0001", "next_earnings_at": None, "margin_regulated": False}
    history["entries"].append(
        _future_entry("events", "0001", first, None, "first", "2026-09-13T09:01:00+09:00")
    )
    head = history["entries"][-1]
    second = {"code": "0001", "next_earnings_at": None, "margin_regulated": True}
    history["entries"].append(
        _future_entry(
            "events", "0001", second,
            {"revision_id": head["revision_id"], "sha256": head["sha256"]},
            "second", "2026-09-13T09:02:00+09:00",
        )
    )
    assert inspect_evidence_history(history)["status"] == "VERIFIED_OFFLINE_HISTORY"
    result = _classify(bundle)
    assert result["reason_codes"] == ["HISTORY_SELECTION_MISSING"]
    assert result["corrected_after_as_of_count"] == 0


def test_selected_subject_corrected_later_is_counted(bundle):
    history = bundle["history"]
    head = _head(history, "lots", "0001")
    payload = {"code": "0001", "lot_size": 200}
    history["entries"].append(
        _future_entry(
            "lots", "0001", payload,
            {"revision_id": head["revision_id"], "sha256": head["sha256"]},
            "correction", "2026-09-13T09:01:00+09:00",
        )
    )
    result = _classify(bundle)
    assert result["reason_codes"] == []
    assert result["matched_subject_count"] == 2
    assert result["corrected_after_as_of_count"] == 1


def test_missing_subject_and_positive_corrected_can_coexist(bundle):
    history = bundle["history"]
    head = _head(history, "lots", "0001")
    payload = {"code": "0001", "lot_size": 200}
    history["entries"] = [
        entry for entry in history["entries"] if entry["subject"]["group"] != "events"
    ]
    history["entries"].append(
        _future_entry(
            "lots", "0001", payload,
            {"revision_id": head["revision_id"], "sha256": head["sha256"]},
            "correction", "2026-09-13T09:01:00+09:00",
        )
    )
    result = _classify(bundle)
    assert "HISTORY_SELECTION_MISSING" in result["reason_codes"]
    assert result["corrected_after_as_of_count"] == 1
    assert result["matched_subject_count"] == 1


def test_reacquired_revision_is_not_counted(bundle):
    history = bundle["history"]
    head = _head(history, "lots", "0001")
    payload = {"code": "0001", "lot_size": 200}
    correction = _future_entry(
        "lots", "0001", payload,
        {"revision_id": head["revision_id"], "sha256": head["sha256"]},
        "correction", "2026-09-13T09:01:00+09:00",
    )
    history["entries"].append(correction)
    duplicate = dict(correction)
    duplicate["receipt_id"] = "opus-r6-lots-correction-receipt-2"
    duplicate["observed_at"] = "2026-09-13T09:03:00+09:00"
    duplicate["recorded_at"] = "2026-09-13T09:03:00+09:00"
    history["entries"].append(duplicate)
    assert inspect_evidence_history(history)["status"] == "VERIFIED_OFFLINE_HISTORY"
    assert _classify(bundle)["corrected_after_as_of_count"] == 1


# --- R5-04: 時点不一致では部品を呼ばない ----------------------------------


def test_time_mismatch_skips_strict_validity(bundle):
    bundle["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    calls: list = []
    result = _classify(bundle, calls)
    assert result["reason_codes"] == ["DECISION_TIME_MISMATCH"]
    assert result["matched_subject_count"] == 0
    assert result["selection_sha256"] is None
    assert calls == []


def test_matching_time_does_call_strict_validity(bundle):
    calls: list = []
    result = _classify(bundle, calls)
    assert result["reason_codes"] == []
    assert calls == ["VERIFIED_OFFLINE_VALIDITY"]
    assert result["selection_sha256"] is not None


def test_time_mismatch_still_reports_three_counts(bundle):
    history = bundle["history"]
    head = _head(history, "lots", "0001")
    payload = {"code": "0001", "lot_size": 200}
    history["entries"].append(
        _future_entry(
            "lots", "0001", payload,
            {"revision_id": head["revision_id"], "sha256": head["sha256"]},
            "correction", "2026-09-13T09:01:00+09:00",
        )
    )
    history["decision_at"] = "2026-09-12T06:51:00+09:00"
    result = _classify(bundle)
    assert result["reason_codes"] == ["DECISION_TIME_MISMATCH"]
    assert result["required_subject_count"] == 2
    assert result["extra_subject_count"] == 0
    assert result["corrected_after_as_of_count"] == 1


def test_broken_validity_documents_still_lose_to_time_mismatch(bundle):
    bundle["validity_documents"] = []
    bundle["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    calls: list = []
    assert _classify(bundle, calls)["reason_codes"] == ["DECISION_TIME_MISMATCH"]
    assert calls == []


def test_empty_history_entries_are_structural_not_missing(bundle):
    """空の期間証拠は段5 の欠落、空の履歴 entries は段3 の構造破損（R6-04）。"""
    bundle["history"]["entries"] = []
    assert inspect_evidence_history(bundle["history"])["reason_codes"] == ["INVALID_ENTRIES"]
    assert _classify(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


def test_empty_validity_documents_map_to_not_satisfied_when_time_matches(bundle):
    bundle["validity_documents"] = []
    result = _classify(bundle)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert result["matched_subject_count"] == 2


# --- R5-05: 読取境界 -------------------------------------------------------


@pytest.mark.parametrize("excess", [0, 1])
def test_shared_reader_boundary_is_exactly_one_mib(tmp_path, excess):
    raw = (
        Path(__file__).resolve().parents[1] / "examples" / "history_validity_binding_valid.json"
    ).read_bytes()
    padded = raw + b" " * (1048576 + excess - len(raw))
    source = tmp_path / "binding.json"
    source.write_bytes(padded)
    if excess:
        with pytest.raises(ValueError):
            _mapping(str(source))
    else:
        assert _mapping(str(source))["mode"] == BINDING_MODE
    assert source.read_bytes() == padded


def test_example_is_unchanged_and_still_verifies(bundle):
    assert set(bundle) == {"mode", "source_origin", "input", "history", "validity_documents"}
    assert inspect_strict_input(bundle["input"])["status"] == "VERIFIED_OFFLINE_INPUT"
    assert inspect_evidence_history(bundle["history"])["status"] == "VERIFIED_OFFLINE_HISTORY"
