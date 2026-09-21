"""Claude Opus 第7回レビュー: 結合 v1 実装への独立反証。

対象は aitrader/history_validity_binding.py と共用化後の
aitrader/evidence_history_fixture.py。契約は HISTORY_VALIDITY_BINDING_PLAN.md。

この試験は Codex の製品試験とは独立に書いた差分照合・不変条件・注入試験である。

- 段5 の理由と 4 件数を、契約から独立に組んだ参照実装と突き合わせる（無作為 200 束）。
- どの束でも 13 キー・閉じた 9 理由・固定フラグ・段ごとの件数規則・入力不変・決定性が崩れない。
- 段4 の書式検査が 6 箇所と全 ID 参照へ届き、段5 より先に打ち切る。
- 段1/段2 は部品を呼ばず、時点不一致では strict_validity を呼ばない。
- 内部が想定外の例外を上げても INVALID_BUNDLE 単独へ倒れる（6 種 × 9 箇所）。

実装コード・既存試験の期待値・共通仕様・合成データ・専用例は変更していない。
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path

import pytest

import aitrader.history_validity_binding as binding
from aitrader.evidence_history_fixture import inspect_evidence_history
from aitrader.history_validity_binding import inspect_history_validity_binding as inspect
from aitrader.strict_input import _aware, _canonical, _resolve, inspect_strict_input
from aitrader.strict_validity import inspect_strict_validity

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "history_validity_binding_valid.json"
KEYS = {
    "mode", "source_origin", "status", "selection_sha256", "reason_codes",
    "required_subject_count", "matched_subject_count", "extra_subject_count",
    "corrected_after_as_of_count", "period_evidence_timed", "ready_for_live",
    "current_signal", "read_only",
}
CLOSED = {
    "DECISION_TIME_MISMATCH", "HISTORY_SELECTION_MISSING", "ROW_HASH_MISMATCH",
    "VALIDITY_NOT_SATISFIED", "EXTRA_SUBJECT_PRESENT", "CODE_FORMAT_INVALID",
    "IDENTIFIER_FORMAT_INVALID", "HISTORY_MODE_MISMATCH", "INVALID_BUNDLE",
}
EARLY = {"INVALID_BUNDLE", "HISTORY_MODE_MISMATCH", "CODE_FORMAT_INVALID",
         "IDENTIFIER_FORMAT_INVALID"}


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@pytest.fixture()
def bundle() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _entry(group: str, code: str, payload: dict, *, when: str, suffix: str, supersedes=None) -> dict:
    return {
        "receipt_id": f"opus-r7-{suffix}-receipt", "revision_id": f"opus-r7-{suffix}-revision",
        "subject": {"group": group, "code": code}, "observed_at": when, "recorded_at": when,
        "payload": payload, "sha256": _hash(payload), "supersedes": supersedes,
    }


def _head(history: dict, group: str) -> dict:
    return [e for e in history["entries"] if e["subject"]["group"] == group][-1]


def _reseal_documents(bundle: dict) -> None:
    for document in bundle["validity_documents"]:
        document["sha256"] = _hash(document["payload"])


# --- 契約から独立に組んだ段5 の参照実装 ---------------------------------------


def _reference_stage5(bundle: dict):
    data, history = bundle["input"], bundle["history"]
    as_of = _aware(data["as_of"])
    required = {(g, c) for g in ("lots", "events") for c in data["expected_codes"]}
    originals = {d["id"]: d["payload"] for d in data["source_documents"]}
    rows = {
        subject: _resolve(
            originals[data["sources"][subject[0]][subject[1]]["document_id"]],
            data["sources"][subject[0]][subject[1]]["pointer"],
        )
        for subject in required
    }
    seen: set[str] = set()
    revisions = []
    for entry in history["entries"]:
        if entry["revision_id"] in seen:
            continue
        seen.add(entry["revision_id"])
        revisions.append(entry)
    subjects = {(e["subject"]["group"], e["subject"]["code"]) for e in revisions}
    selected = {}
    for entry in revisions:
        if _aware(entry["recorded_at"]) <= as_of:
            selected[(entry["subject"]["group"], entry["subject"]["code"])] = entry["sha256"]
    corrected = {
        (e["subject"]["group"], e["subject"]["code"]) for e in revisions
        if _aware(e["recorded_at"]) > as_of and e["supersedes"] is not None
        and (e["subject"]["group"], e["subject"]["code"]) in selected
    }
    counts = (len(required), len(subjects - required), len(corrected & required))
    if as_of != _aware(history["decision_at"]):
        return ["DECISION_TIME_MISMATCH"], counts[0], 0, counts[1], counts[2], None
    validity = inspect_strict_validity({
        "mode": "strict_validity_fixture_v1", "source_origin": "offline_fixture",
        "input": data, "validity_documents": bundle["validity_documents"],
    })
    reasons = set()
    if validity["status"] != "VERIFIED_OFFLINE_VALIDITY":
        reasons.add("VALIDITY_NOT_SATISFIED")
    if counts[1]:
        reasons.add("EXTRA_SUBJECT_PRESENT")
    periods: dict = {}
    for document in bundle["validity_documents"]:
        payload = document["payload"]
        periods.setdefault((payload["group"], payload["code"]), []).append(payload)
    matched = 0
    for subject in required:
        if subject not in selected:
            reasons.add("HISTORY_SELECTION_MISSING")
            continue
        row_hash = _hash(rows[subject])
        if selected[subject] != row_hash:
            reasons.add("ROW_HASH_MISMATCH")
            continue
        evidence = periods.get(subject, [])
        if (len(evidence) == 1 and evidence[0]["record_sha256"] == row_hash
                and _aware(evidence[0]["valid_from"]) <= as_of
                and as_of < _aware(evidence[0]["valid_until"])):
            matched += 1
    projection = [
        {
            "group": subject[0], "code": subject[1],
            "revision_id": [
                e for e in revisions
                if (e["subject"]["group"], e["subject"]["code"]) == subject
                and _aware(e["recorded_at"]) <= as_of
            ][-1]["revision_id"],
            "sha256": selected[subject],
        }
        for subject in sorted(selected)
    ]
    return sorted(reasons), counts[0], matched, counts[1], counts[2], _hash(projection)


def _observed(result: dict):
    return (
        result["reason_codes"], result["required_subject_count"],
        result["matched_subject_count"], result["extra_subject_count"],
        result["corrected_after_as_of_count"], result["selection_sha256"],
    )


def _check_invariants(result: dict) -> None:
    assert set(result) == KEYS
    assert result["mode"] == "history_validity_binding_fixture_v1"
    assert result["source_origin"] == "offline_fixture"
    assert (result["period_evidence_timed"], result["ready_for_live"],
            result["current_signal"], result["read_only"]) == (False, False, False, True)
    reasons = result["reason_codes"]
    assert set(reasons) <= CLOSED
    assert reasons == sorted(set(reasons))
    assert (result["status"] == "VERIFIED_OFFLINE_BINDING") == (reasons == [])
    counts = [result[name] for name in (
        "required_subject_count", "matched_subject_count", "extra_subject_count",
        "corrected_after_as_of_count")]
    if set(reasons) & EARLY:
        assert set(reasons) <= EARLY
        if set(reasons) & {"INVALID_BUNDLE", "HISTORY_MODE_MISMATCH"}:
            assert len(reasons) == 1
        assert counts == [0, 0, 0, 0]
        assert result["selection_sha256"] is None
    elif "DECISION_TIME_MISMATCH" in reasons:
        assert reasons == ["DECISION_TIME_MISMATCH"]
        assert result["matched_subject_count"] == 0
        assert result["selection_sha256"] is None
        assert result["required_subject_count"] > 0
    else:
        assert result["selection_sha256"] is not None
        assert result["required_subject_count"] > 0
        assert result["matched_subject_count"] <= result["required_subject_count"]
        if reasons == []:
            assert result["matched_subject_count"] == result["required_subject_count"]
            assert result["extra_subject_count"] == 0


# --- 無作為束での差分照合と不変条件 -------------------------------------------


def _perturb(bundle: dict, rng: random.Random) -> None:
    history = bundle["history"]
    choice = rng.randrange(9)
    if choice == 0:
        history["decision_at"] = rng.choice([
            "2026-09-12T06:50:00+09:00", "2026-09-11T21:50:00+00:00",
            "2026-09-12T06:51:00+09:00", "2026-09-10T00:00:00+09:00"])
    elif choice == 1:
        entry = rng.choice(history["entries"])
        if entry["subject"]["group"] == "lots":
            entry["payload"]["lot_size"] = rng.choice([100, 200, 300])
        else:
            entry["payload"]["margin_regulated"] = bool(rng.getrandbits(1))
        entry["sha256"] = _hash(entry["payload"])
    elif choice == 2:
        group = rng.choice(["lots", "events"])
        candidates = [e for e in history["entries"] if e["subject"]["group"] == group]
        if candidates:
            head = candidates[-1]
            payload = copy.deepcopy(head["payload"])
            if "lot_size" in payload:
                payload["lot_size"] += 100
            else:
                payload["margin_regulated"] = not payload["margin_regulated"]
            history["entries"].append(_entry(
                group, head["subject"]["code"], payload,
                when=rng.choice(["2026-09-12T06:40:00+09:00", "2026-09-14T09:00:00+09:00"]),
                suffix=f"c{rng.randrange(10 ** 6)}",
                supersedes={"revision_id": head["revision_id"], "sha256": head["sha256"]},
            ))
    elif choice == 3 and len(history["entries"]) > 1:
        history["entries"].pop(rng.randrange(len(history["entries"])))
    elif choice == 4:
        history["entries"].append(_entry(
            "lots", "0002", {"code": "0002", "lot_size": 100},
            when=rng.choice(["2026-09-11T17:02:00+09:00", "2026-09-14T09:00:00+09:00"]),
            suffix=f"x{rng.randrange(10 ** 6)}",
        ))
    elif choice == 5 and bundle["validity_documents"]:
        payload = rng.choice(bundle["validity_documents"])["payload"]
        payload["valid_from"] = rng.choice([
            "2026-09-01T00:00:00+09:00", "2026-09-12T06:50:00+09:00", "2026-09-13T00:00:00+09:00"])
        payload["valid_until"] = rng.choice([
            "2026-10-01T00:00:00+09:00", "2026-09-12T06:50:00+09:00", "2026-09-01T00:00:00+09:00"])
        _reseal_documents(bundle)
    elif choice == 6 and bundle["validity_documents"]:
        rng.choice(bundle["validity_documents"])["payload"]["record_sha256"] = rng.choice(
            ["a" * 64, "b" * 64])
        _reseal_documents(bundle)
    elif choice == 7 and bundle["validity_documents"]:
        if rng.getrandbits(1):
            bundle["validity_documents"].pop(rng.randrange(len(bundle["validity_documents"])))
        else:
            extra = copy.deepcopy(rng.choice(bundle["validity_documents"]))
            extra["id"] = f"opus-r7-dup-{rng.randrange(10 ** 6)}"
            bundle["validity_documents"].append(extra)
    elif bundle["validity_documents"]:
        rng.choice(bundle["validity_documents"])["payload"]["group"] = rng.choice(
            ["lots", "events", "prices"])
        _reseal_documents(bundle)


def test_random_bundles_match_an_independent_stage5_reference(bundle):
    rng = random.Random(20260913)
    reached = 0
    for _ in range(200):
        value = copy.deepcopy(bundle)
        for _ in range(rng.randint(1, 3)):
            _perturb(value, rng)
        snapshot = json.dumps(value, sort_keys=True)
        result = inspect(value)
        assert json.dumps(value, sort_keys=True) == snapshot
        assert inspect(value) == result
        _check_invariants(result)
        if inspect_strict_input(value["input"])["status"] != "VERIFIED_OFFLINE_INPUT":
            continue
        if result["reason_codes"] == ["INVALID_BUNDLE"]:
            continue
        reached += 1
        assert _observed(result) == _reference_stage5(copy.deepcopy(value))
    assert reached > 100


def test_random_structural_damage_never_leaves_the_closed_contract(bundle):
    rng = random.Random(777)
    values = [None, 0, 1, "", "x", [], {}, True, "not-a-time", "0" * 64, "a" * 129,
              "0001", "０００１", "evidence_history_fixture_v2", "live"]
    for _ in range(300):
        value = copy.deepcopy(bundle)
        for _ in range(rng.randint(1, 3)):
            candidates = [value]
            for path in (("input",), ("history",), ("history", "entries", 0),
                         ("validity_documents", 0), ("validity_documents", 0, "payload")):
                node = value
                for key in path:
                    if isinstance(node, dict) and key in node:
                        node = node[key]
                    elif isinstance(node, list) and isinstance(key, int) and len(node) > key:
                        node = node[key]
                    else:
                        node = None
                        break
                if isinstance(node, dict) and node:
                    candidates.append(node)
            target = rng.choice(candidates)
            if isinstance(target, dict) and target:
                key = rng.choice(list(target))
                if rng.getrandbits(1):
                    target[key] = rng.choice(values)
                else:
                    del target[key]
        snapshot = json.dumps(value, sort_keys=True, default=str)
        result = inspect(value)
        assert json.dumps(value, sort_keys=True, default=str) == snapshot
        _check_invariants(result)


# --- 段4 が 6 箇所と全 ID 参照へ届く ------------------------------------------


def _recode(bundle: dict, new: str) -> None:
    old = "0001"
    data = bundle["input"]
    data["expected_codes"] = [new]
    document = data["source_documents"][0]
    for group in ("prices", "publications", "lots", "events"):
        row = document["payload"][group].pop(old)
        row["code"] = new
        document["payload"][group][new] = row
        reference = data["sources"][group].pop(old)
        reference["pointer"] = reference["pointer"].replace(old, new)
        data["sources"][group][new] = reference
    document["sha256"] = _hash(document["payload"])
    for entry in bundle["history"]["entries"]:
        entry["subject"]["code"] = new
        entry["payload"]["code"] = new
        entry["sha256"] = _hash(entry["payload"])
    for validity in bundle["validity_documents"]:
        payload = validity["payload"]
        payload["code"] = new
        payload["record_sha256"] = _hash(document["payload"][payload["group"]][new])
    _reseal_documents(bundle)


@pytest.mark.parametrize("code,expected", [
    ("0001", []), ("00001", []), ("ABCDE", []),
    ("abcd", ["CODE_FORMAT_INVALID"]), ("000001", ["CODE_FORMAT_INVALID"]),
    ("001", ["CODE_FORMAT_INVALID"]), ("０００１", ["CODE_FORMAT_INVALID"]),
])
def test_code_format_applies_to_the_input_and_history_places(bundle, code, expected):
    _recode(bundle, code)
    assert inspect(bundle)["reason_codes"] == expected


def test_code_format_reaches_an_extra_history_subject(bundle):
    bundle["history"]["entries"].append(_entry(
        "lots", "abcd", {"code": "abcd", "lot_size": 100},
        when="2026-09-11T17:02:00+09:00", suffix="badcode"))
    assert inspect_evidence_history(bundle["history"])["status"] == "VERIFIED_OFFLINE_HISTORY"
    assert inspect(bundle)["reason_codes"] == ["CODE_FORMAT_INVALID"]


def test_code_format_reaches_an_extra_validity_document(bundle):
    payload = {"group": "lots", "code": "ab", "record_sha256": "0" * 64,
               "valid_from": "2026-09-01T00:00:00+09:00",
               "valid_until": "2026-10-01T00:00:00+09:00"}
    bundle["validity_documents"].append({"id": "opus-r7-doc", "sha256": _hash(payload),
                                         "payload": payload})
    assert inspect(bundle)["reason_codes"] == ["CODE_FORMAT_INVALID"]


@pytest.mark.parametrize("identifier,expected", [
    ("a" * 128, []), ("a" * 129, ["IDENTIFIER_FORMAT_INVALID"]),
    ("bad id", ["IDENTIFIER_FORMAT_INVALID"]), ("-lead", ["IDENTIFIER_FORMAT_INVALID"]),
    ("識別子", ["IDENTIFIER_FORMAT_INVALID"]),
])
def test_identifier_format_reaches_receipt_ids(bundle, identifier, expected):
    bundle["history"]["entries"][0]["receipt_id"] = identifier
    assert inspect(bundle)["reason_codes"] == expected


@pytest.mark.parametrize("identifier,expected", [
    ("a" * 128, []), ("a" * 129, ["IDENTIFIER_FORMAT_INVALID"]),
    (".leading", ["IDENTIFIER_FORMAT_INVALID"]),
])
def test_identifier_format_reaches_validity_document_ids(bundle, identifier, expected):
    bundle["validity_documents"][0]["id"] = identifier
    assert inspect(bundle)["reason_codes"] == expected


def test_identifier_format_reaches_source_document_references(bundle):
    bundle["input"]["source_documents"][0]["id"] = "a" * 129
    for group in bundle["input"]["sources"].values():
        for reference in group.values():
            reference["document_id"] = "a" * 129
    assert inspect_strict_input(bundle["input"])["status"] == "VERIFIED_OFFLINE_INPUT"
    assert inspect(bundle)["reason_codes"] == ["IDENTIFIER_FORMAT_INVALID"]


def test_identifier_format_reaches_a_superseded_reference(bundle):
    head = _head(bundle["history"], "lots")
    head["revision_id"] = "opus r7 head"
    head["receipt_id"] = "opus-r7-head"
    bundle["history"]["entries"].append(_entry(
        "lots", "0001", {"code": "0001", "lot_size": 200},
        when="2026-09-14T09:00:00+09:00", suffix="corr",
        supersedes={"revision_id": head["revision_id"], "sha256": head["sha256"]}))
    assert inspect(bundle)["reason_codes"] == ["IDENTIFIER_FORMAT_INVALID"]


def test_stage4_stops_before_stage5(bundle):
    bundle["history"]["entries"][0]["receipt_id"] = "bad id"
    bundle["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    bundle["validity_documents"] = []
    result = inspect(bundle)
    assert result["reason_codes"] == ["IDENTIFIER_FORMAT_INVALID"]
    assert result["selection_sha256"] is None


def test_stage3_stops_before_stage4(bundle):
    bundle["history"]["entries"][0]["receipt_id"] = "bad id"
    bundle["validity_documents"][0]["payload"]["opus_extra"] = 1
    _reseal_documents(bundle)
    assert inspect(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


# --- 呼出しの有無 -------------------------------------------------------------


def test_stage1_and_stage2_call_no_component(bundle, monkeypatch):
    calls: list = []
    monkeypatch.setattr(binding, "inspect_strict_input",
                        lambda value: calls.append("input"))
    monkeypatch.setattr(binding, "_validate_history", lambda value: calls.append("history"))
    monkeypatch.setattr(binding, "inspect_strict_validity",
                        lambda value: calls.append("validity"))
    envelope = copy.deepcopy(bundle)
    envelope["mode"] = "history_validity_binding_fixture_v2"
    assert inspect(envelope)["reason_codes"] == ["INVALID_BUNDLE"]
    version = copy.deepcopy(bundle)
    version["history"]["mode"] = "evidence_history_fixture_v2"
    version["history"]["opus_extra"] = 1
    assert inspect(version)["reason_codes"] == ["HISTORY_MODE_MISMATCH"]
    assert calls == []


def test_time_mismatch_never_calls_validity(bundle, monkeypatch):
    calls: list = []
    real = binding.inspect_strict_validity
    monkeypatch.setattr(binding, "inspect_strict_validity",
                        lambda value: (calls.append(1), real(value))[1])
    mismatch = copy.deepcopy(bundle)
    mismatch["history"]["decision_at"] = "2026-09-12T06:51:00+09:00"
    mismatch["validity_documents"] = []
    assert inspect(mismatch)["reason_codes"] == ["DECISION_TIME_MISMATCH"]
    assert calls == []
    assert inspect(copy.deepcopy(bundle))["reason_codes"] == []
    assert calls == [1]


def test_late_input_reason_discards_every_count(bundle, monkeypatch):
    monkeypatch.setattr(binding, "inspect_strict_validity", lambda value: {
        "status": "DATA_INCOMPLETE", "reason_codes": ["INPUT_SOURCE_REFERENCE_INVALID"]})
    result = inspect(bundle)
    assert result["reason_codes"] == ["INVALID_BUNDLE"]
    assert [result[name] for name in (
        "required_subject_count", "matched_subject_count", "extra_subject_count",
        "corrected_after_as_of_count")] == [0, 0, 0, 0]
    assert result["selection_sha256"] is None


@pytest.mark.parametrize("name", [
    "_hash", "_validity_shape", "_format_reasons", "_resolve", "_aware",
    "inspect_strict_input", "inspect_strict_validity", "_validate_history", "_canonical",
])
@pytest.mark.parametrize("error", [
    KeyError, IndexError, TypeError, ValueError, OverflowError, RecursionError,
])
def test_documented_exceptions_fail_closed(bundle, monkeypatch, name, error):
    def explode(*args, **kwargs):
        raise error("injected")

    monkeypatch.setattr(binding, name, explode)
    result = inspect(bundle)
    assert result["reason_codes"] == ["INVALID_BUNDLE"]
    assert result["selection_sha256"] is None


# --- 段5 の個別境界 -----------------------------------------------------------


def test_success_digest_is_the_public_history_digest(bundle):
    result = inspect(bundle)
    assert result["status"] == "VERIFIED_OFFLINE_BINDING"
    assert result["selection_sha256"] == inspect_evidence_history(
        bundle["history"])["selection_sha256"]


def test_all_future_history_keeps_a_digest_but_matches_nothing(bundle):
    history = bundle["history"]
    for entry in history["entries"]:
        entry["observed_at"] = "2026-09-14T09:00:00+09:00"
        entry["recorded_at"] = "2026-09-14T09:00:00+09:00"
    result = inspect(bundle)
    assert result["reason_codes"] == ["HISTORY_SELECTION_MISSING"]
    assert result["matched_subject_count"] == 0
    assert result["selection_sha256"] is not None


def test_matched_can_equal_required_while_the_bundle_fails(bundle):
    payload = {"group": "lots", "code": "0002", "record_sha256": "0" * 64,
               "valid_from": "2026-09-01T00:00:00+09:00",
               "valid_until": "2026-10-01T00:00:00+09:00"}
    bundle["validity_documents"].append({"id": "opus-r7-extra", "sha256": _hash(payload),
                                         "payload": payload})
    result = inspect(bundle)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert result["matched_subject_count"] == result["required_subject_count"] == 2
    assert result["extra_subject_count"] == 0


def test_repeated_receipt_keeps_the_binding_successful(bundle):
    bundle["history"]["entries"].append(copy.deepcopy(bundle["history"]["entries"][0]))
    assert inspect(bundle)["status"] == "VERIFIED_OFFLINE_BINDING"


@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), 10 ** 400,
])
def test_non_finite_and_huge_numbers_fail_closed(bundle, value):
    bundle["input"]["source_documents"][0]["payload"]["lots"]["0001"]["lot_size"] = value
    assert inspect(bundle)["reason_codes"] == ["INVALID_BUNDLE"]


@pytest.mark.parametrize("value", [None, [], "x", 3])
def test_non_mapping_bundles_fail_closed(value):
    result = inspect(value)
    assert result["reason_codes"] == ["INVALID_BUNDLE"]
    _check_invariants(result)


# --- 複数銘柄（専用例は 1 銘柄しか含まない） ---------------------------------


def _two_codes(bundle: dict, second: str = "0002") -> dict:
    data = bundle["input"]
    document = data["source_documents"][0]
    payload = document["payload"]
    data["expected_codes"] = ["0001", second]
    for group in ("prices", "publications", "lots", "events"):
        row = copy.deepcopy(payload[group]["0001"])
        row["code"] = second
        payload[group][second] = row
        reference = copy.deepcopy(data["sources"][group]["0001"])
        reference["pointer"] = f"/{group}/{second}"
        data["sources"][group][second] = reference
    document["sha256"] = _hash(payload)
    for group in ("lots", "events"):
        bundle["history"]["entries"].append(_entry(
            group, second, copy.deepcopy(payload[group][second]),
            when="2026-09-11T17:01:00+09:00", suffix=f"{group}-{second}"))
        period = {
            "group": group, "code": second,
            "record_sha256": _hash(payload[group][second]),
            "valid_from": "2026-09-01T00:00:00+09:00",
            "valid_until": "2026-10-01T00:00:00+09:00",
        }
        bundle["validity_documents"].append({
            "id": f"opus-r7-doc-{group}-{second}", "sha256": _hash(period), "payload": period})
    return bundle


@pytest.mark.parametrize("second", ["0002", "ABCDE"])
def test_two_subjects_verify_with_four_required(bundle, second):
    result = inspect(_two_codes(bundle, second))
    assert result["reason_codes"] == []
    assert result["required_subject_count"] == result["matched_subject_count"] == 4
    assert result["extra_subject_count"] == 0


def test_one_expired_period_only_lowers_matched_by_one(bundle):
    value = _two_codes(bundle)
    document = [d for d in value["validity_documents"]
                if d["payload"]["code"] == "0002" and d["payload"]["group"] == "lots"][0]
    document["payload"]["valid_until"] = "2026-09-02T00:00:00+09:00"
    _reseal_documents(value)
    result = inspect(value)
    assert result["reason_codes"] == ["VALIDITY_NOT_SATISFIED"]
    assert (result["required_subject_count"], result["matched_subject_count"]) == (4, 3)


def test_one_missing_selection_among_four_subjects(bundle):
    value = _two_codes(bundle)
    value["history"]["entries"] = [
        entry for entry in value["history"]["entries"]
        if not (entry["subject"]["code"] == "0002" and entry["subject"]["group"] == "events")
    ]
    result = inspect(value)
    assert result["reason_codes"] == ["HISTORY_SELECTION_MISSING"]
    assert result["matched_subject_count"] == 3


def test_one_row_hash_difference_among_four_subjects(bundle):
    value = _two_codes(bundle)
    entry = [e for e in value["history"]["entries"]
             if e["subject"]["code"] == "0002" and e["subject"]["group"] == "lots"][0]
    entry["payload"]["lot_size"] = 200
    entry["sha256"] = _hash(entry["payload"])
    result = inspect(value)
    assert result["reason_codes"] == ["ROW_HASH_MISMATCH"]
    assert result["matched_subject_count"] == 3
