"""Claude Opus 第8回レビュー: 複数銘柄での件数分配のうち、まだ固定されていない組合せ。

Codex の test_binding_multicode_codex.py（6 件）は 2 銘柄 4 subject を扱うが、
extra_subject_count は常に 0、銘柄数も 2 までである。ここでは
「余分 subject × 複数銘柄」「3 銘柄 6 subject」「4 件数すべて非ゼロ」
「corrected が 2 subject に跨る」「複数銘柄での段2〜4 の打切り」を固定する。

実装コード・既存試験の期待値・共通仕様・合成データ・専用例は変更していない。
実測の結果、実装はいずれの組合せでも契約どおりだった（第8回レビュー本文を参照）。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from aitrader.history_validity_binding import inspect_history_validity_binding as inspect
from aitrader.strict_input import _canonical, inspect_strict_input
from aitrader.evidence_history_fixture import inspect_evidence_history

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "history_validity_binding_valid.json"


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@pytest.fixture()
def bundle() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _add_code(bundle: dict, code: str) -> dict:
    """expected_codes へ 1 銘柄足し、原本・履歴・期間証拠も揃える。"""
    data = bundle["input"]
    document = data["source_documents"][0]
    payload = document["payload"]
    data["expected_codes"].append(code)
    for group in ("prices", "publications", "lots", "events"):
        row = copy.deepcopy(payload[group]["0001"])
        row["code"] = code
        payload[group][code] = row
        reference = copy.deepcopy(data["sources"][group]["0001"])
        reference["pointer"] = f"/{group}/{code}"
        data["sources"][group][code] = reference
    document["sha256"] = _hash(payload)
    for group in ("lots", "events"):
        row = copy.deepcopy(payload[group][code])
        bundle["history"]["entries"].append({
            "receipt_id": f"opus-r8-{group}-{code}-receipt",
            "revision_id": f"opus-r8-{group}-{code}-revision",
            "subject": {"group": group, "code": code},
            "observed_at": "2026-09-11T17:01:00+09:00",
            "recorded_at": "2026-09-11T17:01:00+09:00",
            "payload": row, "sha256": _hash(row), "supersedes": None,
        })
        period = {
            "group": group, "code": code, "record_sha256": _hash(row),
            "valid_from": "2026-09-01T00:00:00+09:00",
            "valid_until": "2026-10-01T00:00:00+09:00",
        }
        bundle["validity_documents"].append({
            "id": f"opus-r8-doc-{group}-{code}", "sha256": _hash(period), "payload": period})
    return bundle


def _spare_subject(bundle: dict, group: str, code: str, *, when: str) -> None:
    """必須集合の外にある subject を履歴だけへ足す（期間証拠は足さない）。"""
    row = ({"code": code, "lot_size": 100} if group == "lots"
           else {"code": code, "next_earnings_at": None, "margin_regulated": False})
    bundle["history"]["entries"].append({
        "receipt_id": f"opus-r8-spare-{group}-{code}-receipt",
        "revision_id": f"opus-r8-spare-{group}-{code}-revision",
        "subject": {"group": group, "code": code},
        "observed_at": when, "recorded_at": when,
        "payload": row, "sha256": _hash(row), "supersedes": None,
    })


def _future_correction(bundle: dict, group: str, code: str) -> None:
    head = [e for e in bundle["history"]["entries"]
            if e["subject"] == {"group": group, "code": code}][-1]
    row = copy.deepcopy(head["payload"])
    if "lot_size" in row:
        row["lot_size"] += 100
    else:
        row["margin_regulated"] = not row["margin_regulated"]
    bundle["history"]["entries"].append({
        "receipt_id": f"opus-r8-corr-{group}-{code}-receipt",
        "revision_id": f"opus-r8-corr-{group}-{code}-revision",
        "subject": {"group": group, "code": code},
        "observed_at": "2026-09-14T09:00:00+09:00",
        "recorded_at": "2026-09-14T09:00:00+09:00",
        "payload": row, "sha256": _hash(row),
        "supersedes": {"revision_id": head["revision_id"], "sha256": head["sha256"]},
    })


def _drop_subject(bundle: dict, group: str, code: str) -> None:
    bundle["history"]["entries"] = [
        entry for entry in bundle["history"]["entries"]
        if entry["subject"] != {"group": group, "code": code}]


def _break_row_hash(bundle: dict, group: str, code: str) -> None:
    entry = [e for e in bundle["history"]["entries"]
             if e["subject"] == {"group": group, "code": code}][-1]
    if "lot_size" in entry["payload"]:
        entry["payload"]["lot_size"] += 100
    else:
        entry["payload"]["margin_regulated"] = not entry["payload"]["margin_regulated"]
    entry["sha256"] = _hash(entry["payload"])


def _counts(result: dict) -> tuple:
    return (result["required_subject_count"], result["matched_subject_count"],
            result["extra_subject_count"], result["corrected_after_as_of_count"])


def test_two_codes_stay_healthy(bundle):
    result = inspect(_add_code(bundle, "0002"))
    assert result["reason_codes"] == []
    assert _counts(result) == (4, 4, 0, 0)


def test_spare_subject_does_not_lower_matched(bundle):
    value = _add_code(bundle, "0002")
    _spare_subject(value, "lots", "0003", when="2026-09-11T17:02:00+09:00")
    result = inspect(value)
    assert result["reason_codes"] == ["EXTRA_SUBJECT_PRESENT"]
    assert _counts(result) == (4, 4, 1, 0)


def test_two_spare_subjects_are_counted_separately(bundle):
    value = _add_code(bundle, "0002")
    _spare_subject(value, "lots", "0003", when="2026-09-11T17:02:00+09:00")
    _spare_subject(value, "events", "0003", when="2026-09-11T17:03:00+09:00")
    result = inspect(value)
    assert result["reason_codes"] == ["EXTRA_SUBJECT_PRESENT"]
    assert _counts(result) == (4, 4, 2, 0)


def test_future_only_spare_subject_is_still_extra(bundle):
    value = _add_code(bundle, "0002")
    _spare_subject(value, "lots", "0003", when="2026-09-14T09:00:00+09:00")
    result = inspect(value)
    assert result["reason_codes"] == ["EXTRA_SUBJECT_PRESENT"]
    assert _counts(result) == (4, 4, 1, 0)


def test_three_codes_split_two_reasons_across_subjects(bundle):
    value = _add_code(_add_code(bundle, "0002"), "0003")
    _drop_subject(value, "events", "0002")
    _break_row_hash(value, "lots", "0003")
    assert inspect_strict_input(value["input"])["status"] == "VERIFIED_OFFLINE_INPUT"
    assert inspect_evidence_history(value["history"])["status"] == "VERIFIED_OFFLINE_HISTORY"
    result = inspect(value)
    assert result["reason_codes"] == ["HISTORY_SELECTION_MISSING", "ROW_HASH_MISMATCH"]
    assert _counts(result) == (6, 4, 0, 0)


def test_all_four_counts_are_non_zero_together(bundle):
    value = _add_code(bundle, "0002")
    _break_row_hash(value, "lots", "0002")
    _spare_subject(value, "events", "0003", when="2026-09-11T17:02:00+09:00")
    _future_correction(value, "lots", "0001")
    result = inspect(value)
    assert result["reason_codes"] == ["EXTRA_SUBJECT_PRESENT", "ROW_HASH_MISMATCH"]
    assert _counts(result) == (4, 3, 1, 1)
    assert result["selection_sha256"] is not None


def test_corrections_on_two_subjects_do_not_block_success(bundle):
    value = _add_code(bundle, "0002")
    _future_correction(value, "lots", "0001")
    _future_correction(value, "events", "0002")
    result = inspect(value)
    assert result["reason_codes"] == []
    assert _counts(result) == (4, 4, 0, 2)


def test_missing_subject_and_two_corrections_coexist(bundle):
    value = _add_code(bundle, "0002")
    _future_correction(value, "lots", "0001")
    _future_correction(value, "lots", "0002")
    _drop_subject(value, "events", "0002")
    result = inspect(value)
    assert result["reason_codes"] == ["HISTORY_SELECTION_MISSING"]
    assert _counts(result) == (4, 3, 0, 2)


def test_time_mismatch_on_four_subjects_reports_three_counts(bundle):
    value = _add_code(bundle, "0002")
    # recorded_at は入力順に逆行できないので、余分 subject を訂正より先に置く。
    _spare_subject(value, "lots", "0003", when="2026-09-11T17:02:00+09:00")
    _future_correction(value, "lots", "0001")
    value["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    result = inspect(value)
    assert result["reason_codes"] == ["DECISION_TIME_MISMATCH"]
    assert _counts(result) == (4, 0, 1, 1)
    assert result["selection_sha256"] is None


def test_format_violation_on_four_subjects_zeroes_every_count(bundle):
    value = _add_code(bundle, "0002")
    _spare_subject(value, "lots", "abcd", when="2026-09-11T17:02:00+09:00")
    result = inspect(value)
    assert result["reason_codes"] == ["CODE_FORMAT_INVALID"]
    assert _counts(result) == (0, 0, 0, 0)
    assert result["selection_sha256"] is None
