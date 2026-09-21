"""Claude 第12回 独立反証: 行履歴 v2 / 結合 v2 の、既存49件が触れていない境界だけ。

対象: aitrader/evidence_history_fixture_v2.py, aitrader/history_validity_binding_v2.py。
既存試験・期待値・例・製品コードは変更しない。
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random

import pytest

from aitrader import history_validity_binding_v2 as api
from aitrader.evidence_history_fixture_v2 import inspect_evidence_history as inspect_v2
from aitrader.history_validity_binding import inspect_history_validity_binding as inspect_v1_binding
from aitrader.strict_input import _aware, _canonical

ROOT = Path(__file__).parents[1]
EXPECTED = json.loads((Path(__file__).with_name("binding_v2_expected.json")).read_text(encoding="utf-8"))
HEX64 = set("0123456789abcdef")


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def rehash(entry):
    entry["sha256"] = _hash(entry["payload"])
    return entry


def history_bundle():
    value = json.loads((ROOT / "examples/evidence_history_valid.json").read_text(encoding="utf-8"))
    value["mode"] = "evidence_history_fixture_v2"
    return value


def binding_bundle():
    return json.loads((ROOT / "examples/history_validity_binding_v2_valid.json").read_text(encoding="utf-8"))


def spell(instant, offset_hours=9, fraction=""):
    """Render an aware instant in another zone; fraction is '' | '.0' | '.000000'."""
    local = instant.astimezone(timezone(timedelta(hours=offset_hours)))
    base = local.strftime("%Y-%m-%dT%H:%M:%S")
    if offset_hours == 0:
        zone = "Z"
    else:
        sign = "+" if offset_hours >= 0 else "-"
        zone = f"{sign}{abs(offset_hours):02d}:00"
    return f"{base}{fraction}{zone}"


# ---------------------------------------------------------------- 行履歴 v2

def test_zero_fraction_and_zone_spelling_replay_is_noop():
    value = history_bundle()
    first = value["entries"][0]
    replay = deepcopy(first)
    replay["observed_at"] = spell(_aware(first["observed_at"]), 9, ".000000")
    replay["recorded_at"] = spell(_aware(first["recorded_at"]), 0, ".0")
    value["entries"].append(replay)
    result = inspect_v2(value)
    assert result["reason_codes"] == []
    assert result["noop_receipt_count"] == 1


def test_observed_after_recorded_is_detected_across_zones():
    value = history_bundle()
    first = value["entries"][0]
    recorded = _aware(first["recorded_at"])
    first["observed_at"] = spell(recorded + timedelta(seconds=1), 0)
    assert inspect_v2(value)["reason_codes"] == ["INVALID_TIMESTAMPS"]


def test_recorded_reversal_is_detected_across_zones():
    value = history_bundle()
    assert len(value["entries"]) >= 2
    earlier = _aware(value["entries"][0]["recorded_at"]) - timedelta(seconds=1)
    second = value["entries"][1]
    second["observed_at"] = spell(earlier, -5)
    second["recorded_at"] = spell(earlier, 0)
    assert inspect_v2(value)["reason_codes"] == ["RECORDED_AT_REVERSED"]


def test_replay_with_changed_payload_is_receipt_conflict_before_hash_check():
    """正規化は 2 path だけ。payload 差は receipt 同一性で先に拒否され、hash 検査に達しない。"""
    value = history_bundle()
    replay = deepcopy(value["entries"][0])
    replay["observed_at"] = spell(_aware(replay["observed_at"]), 0)
    replay["payload"] = dict(replay["payload"])
    key = next(iter(replay["payload"]))
    replay["payload"][key] = "changed"  # sha256 はそのまま（不一致だが到達しない）
    value["entries"].append(replay)
    assert inspect_v2(value)["reason_codes"] == ["RECEIPT_CONFLICT"]


def test_decision_at_keeps_loose_aware_grammar_by_contract():
    """契約どおり decision_at は v1 の _aware のまま（7桁秒未満も受理）。entry 側と非対称であることを固定する。"""
    value = history_bundle()
    value["decision_at"] = value["decision_at"].replace("+09:00", ".1234567+09:00")
    assert inspect_v2(value)["reason_codes"] == []
    value = history_bundle()
    value["entries"][0]["recorded_at"] = value["entries"][0]["recorded_at"].replace("+09:00", ".1234567+09:00")
    assert inspect_v2(value)["reason_codes"] == ["INVALID_TIMESTAMPS"]


def test_identifier_failure_precedes_timestamp_failure():
    value = history_bundle()
    value["entries"][0]["receipt_id"] = " padded "
    value["entries"][0]["observed_at"] = "bad"
    assert inspect_v2(value)["reason_codes"] == ["INVALID_IDENTIFIER"]


def test_timestamp_failure_precedes_reversal_and_payload_failure():
    value = history_bundle()
    second = value["entries"][1]
    second["observed_at"] = "2026-09-12T06:50:00"  # zone 欠落
    second["recorded_at"] = "2000-01-01T00:00:00Z"  # 逆行
    second["payload"] = {"broken": True}
    assert inspect_v2(value)["reason_codes"] == ["INVALID_TIMESTAMPS"]


@pytest.mark.parametrize("bad", [None, 20260912, ["2026-09-12T06:50:00Z"], {"at": "x"}, True])
def test_non_string_timestamps_are_invalid_timestamps_not_exceptions(bad):
    value = history_bundle()
    value["entries"][0]["recorded_at"] = bad
    assert inspect_v2(value)["reason_codes"] == ["INVALID_TIMESTAMPS"]


@pytest.mark.parametrize("observed, reasons", [
    ("0001-01-01T00:59:59+01:00", ["INVALID_TIMESTAMPS"]),  # UTC は 0000-12-31T23:59:59 → 範囲外
    ("0001-01-01T01:00:00+01:00", []),                      # UTC はちょうど 0001-01-01T00:00:00 → 範囲内
    ("0001-01-01T00:00:00-01:00", []),                      # 負のオフセットは UTC が進むので範囲内
    ("0001-01-01T00:00:00Z", []),
])
def test_year_one_exact_overflow_boundary(observed, reasons):
    value = history_bundle()
    value["entries"][0]["observed_at"] = observed
    assert inspect_v2(value)["reason_codes"] == reasons


def test_year_9999_with_positive_offset_is_accepted_on_last_entry():
    value = history_bundle()
    last = value["entries"][-1]
    last["observed_at"] = last["recorded_at"] = "9999-12-31T23:59:59.999999+09:00"
    result = inspect_v2(value)
    assert result["reason_codes"] == []
    assert result["future_revision_count"] >= 1


def test_whole_history_respelled_in_utc_gives_identical_output_and_no_mutation():
    jst = history_bundle()
    utc = deepcopy(jst)
    utc["decision_at"] = spell(_aware(utc["decision_at"]), 0)
    for entry in utc["entries"]:
        entry["observed_at"] = spell(_aware(entry["observed_at"]), 0, ".000000")
        entry["recorded_at"] = spell(_aware(entry["recorded_at"]), -5)
    before = deepcopy(utc)
    assert inspect_v2(utc) == inspect_v2(jst)
    assert utc == before


# ---------------------------------------------------------------- 結合 v2

def test_three_instants_equal_across_spellings_including_period_side():
    value = binding_bundle()
    as_of = _aware(value["input"]["as_of"])
    value["history"]["decision_at"] = spell(as_of, 0)
    value["period_history"]["decision_at"] = spell(as_of, -5, ".000000")
    assert api.inspect_history_validity_binding(value) == EXPECTED["E1"]


def test_stage2_non_dict_history_does_not_hide_period_mode_mismatch():
    value = binding_bundle()
    value["history"] = []
    value["period_history"]["mode"] = "period_evidence_history_fixture_v0"
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["PERIOD_HISTORY_MODE_MISMATCH"]
    value = binding_bundle()
    value["history"]["mode"] = None
    assert api.inspect_history_validity_binding(value)["reason_codes"] == ["HISTORY_MODE_MISMATCH"]


def test_empty_period_entries_is_structural_invalid_bundle():
    value = binding_bundle()
    value["period_history"]["entries"] = []
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["INVALID_BUNDLE"]
    assert result["period_series_count"] == 0 and result["selection_sha256"] is None


def _overlap_entry(value):
    entry = deepcopy(value["period_history"]["entries"][0])
    entry.update(receipt_id="claude-overlap-receipt", revision_id="claude-overlap-revision",
                 series_id="claude-overlap-series",
                 observed_at="2026-09-11T18:00:00+09:00", recorded_at="2026-09-11T18:00:00+09:00")
    entry["payload"].update(valid_from="2026-09-11T00:00:00+09:00", valid_until="2026-09-15T00:00:00+09:00")
    return rehash(entry)


def test_code_format_in_history_precedes_period_overlap():
    value = binding_bundle()
    value["period_history"]["entries"].insert(2, _overlap_entry(value))
    row = value["history"]["entries"][0]
    row["subject"]["code"] = row["payload"]["code"] = "ab"
    rehash(row)
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["CODE_FORMAT_INVALID"]
    assert result["period_series_count"] == 0 and result["selection_sha256"] is None


def test_row_hash_mismatch_short_circuits_even_without_period_candidate():
    value = binding_bundle()
    row = value["history"]["entries"][0]
    row["payload"]["lot_size"] = 200
    rehash(row)
    value["period_history"]["entries"] = [value["period_history"]["entries"][1]]  # events のみ
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["ROW_HASH_MISMATCH"]
    assert result["matched_subject_count"] == 1
    assert result["period_series_count"] == 1 and result["period_candidate_count"] == 1
    assert result["selection_sha256"] == EXPECTED["E7"]["selection_sha256"]


def test_overlap_plus_missing_row_reports_both_reasons_sorted():
    value = binding_bundle()
    value["history"]["entries"] = value["history"]["entries"][:1]  # lots のみ
    value["period_history"]["entries"].insert(2, _overlap_entry(value))
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["HISTORY_SELECTION_MISSING", "VALIDITY_NOT_SATISFIED"]
    assert result["matched_subject_count"] == 0
    assert result["period_series_count"] == 3 and result["period_candidate_count"] == 0
    assert result["selection_sha256"] == EXPECTED["E6"]["selection_sha256"]


def test_row_only_future_extra_subject_is_reported():
    value = binding_bundle()
    extra = deepcopy(value["history"]["entries"][0])
    extra.update(receipt_id="claude-row-extra-receipt", revision_id="claude-row-extra-revision",
                 observed_at="2026-09-20T00:00:00+09:00", recorded_at="2026-09-20T00:00:00+09:00")
    extra["subject"]["code"] = extra["payload"]["code"] = "0002"
    value["history"]["entries"].append(rehash(extra))
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["EXTRA_SUBJECT_PRESENT"]
    assert result["extra_subject_count"] == 1 and result["matched_subject_count"] == 2
    assert result["period_series_count"] == 2 and result["period_candidate_count"] == 2
    assert result["selection_sha256"] == EXPECTED["E1"]["selection_sha256"]


def test_correction_of_extra_subject_is_not_counted_as_corrected():
    value = binding_bundle()
    base = deepcopy(value["history"]["entries"][0])
    base.update(receipt_id="claude-x-receipt-1", revision_id="claude-x-revision-1")
    base["subject"]["code"] = base["payload"]["code"] = "0002"
    rehash(base)
    fix = deepcopy(base)
    fix.update(receipt_id="claude-x-receipt-2", revision_id="claude-x-revision-2",
               observed_at="2026-09-13T00:00:00+09:00", recorded_at="2026-09-13T00:00:00+09:00",
               supersedes={"revision_id": base["revision_id"], "sha256": base["sha256"]})
    fix["payload"]["lot_size"] = 200
    rehash(fix)
    value["history"]["entries"] += [base, fix]
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["EXTRA_SUBJECT_PRESENT"]
    assert result["extra_subject_count"] == 1
    assert result["corrected_after_as_of_count"] == 0
    assert result["matched_subject_count"] == 2


def _two_code_bundle():
    value = binding_bundle()
    strict = value["input"]
    document = strict["source_documents"][0]
    for group in ("prices", "publications", "lots", "events"):
        record = deepcopy(document["payload"][group]["0001"])
        record["code"] = "0002"
        document["payload"][group]["0002"] = record
        strict["sources"][group]["0002"] = {"document_id": document["id"], "pointer": f"/{group}/0002"}
    document["sha256"] = _hash(document["payload"])
    strict["expected_codes"] = ["0001", "0002"]
    row_hashes = {}
    for entry in list(value["history"]["entries"]):
        clone = deepcopy(entry)
        group = clone["subject"]["group"]
        clone.update(receipt_id=f"claude-{group}-0002-receipt", revision_id=f"claude-{group}-0002-revision")
        clone["subject"]["code"] = clone["payload"]["code"] = "0002"
        rehash(clone)
        row_hashes[group] = clone["sha256"]
        value["history"]["entries"].append(clone)
    for group in ("lots", "events"):
        period = deepcopy(value["period_history"]["entries"][0])
        period.update(receipt_id=f"claude-period-{group}-0002-receipt",
                      revision_id=f"claude-period-{group}-0002-revision",
                      series_id=f"claude-period-{group}-0002-series",
                      observed_at="2026-09-11T18:00:00+09:00", recorded_at="2026-09-11T18:00:00+09:00")
        period["subject"] = {"group": group, "code": "0002"}
        period["payload"].update(group=group, code="0002", record_sha256=row_hashes[group])
        value["period_history"]["entries"].insert(2, rehash(period))
    return value


def test_two_codes_success_required_is_groups_times_codes():
    value = _two_code_bundle()
    before = deepcopy(value)
    result = api.inspect_history_validity_binding(value)
    assert value == before
    assert result["reason_codes"] == []
    assert result["required_subject_count"] == 4 and result["matched_subject_count"] == 4
    assert result["extra_subject_count"] == 0
    assert result["period_series_count"] == 4 and result["period_candidate_count"] == 4
    assert result["period_evidence_timed"] is True
    assert result["ready_for_live"] is False and result["current_signal"] is False
    digest = result["selection_sha256"]
    assert len(digest) == 64 and set(digest) <= HEX64 and digest != EXPECTED["E1"]["selection_sha256"]
    # 行選択 digest は履歴 v2 単体 API の digest と同一（期間 digest ではない）
    assert digest == inspect_v2(value["history"])["selection_sha256"]


def test_two_codes_missing_one_period_subject_counts_precisely():
    value = _two_code_bundle()
    value["period_history"]["entries"] = [
        e for e in value["period_history"]["entries"] if e["series_id"] != "claude-period-events-0002-series"
    ]
    result = api.inspect_history_validity_binding(value)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert result["required_subject_count"] == 4 and result["matched_subject_count"] == 3
    assert result["period_series_count"] == 3 and result["period_candidate_count"] == 3
    assert result["period_evidence_timed"] is False


def test_v1_binding_is_untouched_by_v2():
    v1 = json.loads((ROOT / "examples/history_validity_binding_valid.json").read_text(encoding="utf-8"))
    assert inspect_v1_binding(v1)["status"] == "VERIFIED_OFFLINE_BINDING"
    assert inspect_v1_binding(binding_bundle())["reason_codes"] == ["INVALID_BUNDLE"]
    assert api.inspect_history_validity_binding(v1)["reason_codes"] == ["INVALID_BUNDLE"]


def test_spelling_invariance_fuzz_300_bundles():
    """仮説: 行履歴 v2 と三者時点の表記（zone・秒未満）をどう変えても E1 の出力は不変。"""
    rng = random.Random(20260917)
    offsets = [9, 0, -5, 14, -12, 5]
    fractions = ["", ".0", ".000000"]
    for _ in range(300):
        value = binding_bundle()
        instant = _aware(value["input"]["as_of"])
        value["history"]["decision_at"] = spell(instant, rng.choice(offsets), rng.choice(fractions))
        value["period_history"]["decision_at"] = spell(instant, rng.choice(offsets), rng.choice(fractions))
        for entry in value["history"]["entries"]:
            for key in ("observed_at", "recorded_at"):
                entry[key] = spell(_aware(entry[key]), rng.choice(offsets), rng.choice(fractions))
        before = deepcopy(value)
        assert api.inspect_history_validity_binding(value) == EXPECTED["E1"]
        assert value == before
