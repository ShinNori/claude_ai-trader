"""Opus 第4回: 結合 段3 の期間証拠「事前形検査」が構造破損を段5へ漏らさないことを固定する。

[結合案](../HISTORY_VALIDITY_BINDING_PLAN.md)の第3回節は、期間証拠の構造/型/自己hash破損を
段3で INVALID_BUNDLE とし、欠落・重複subject・行hash差・期間外は段5で
VALIDITY_NOT_SATISFIED とすると決めた。この試験は、その段3検査の参照実装を試験内に置き、
「段3を通った束に対して strict_validity_fixture_v1 が構造側の理由を返さない」ことを
損傷種別ごとと無作為生成の両方で確かめる。結合API本体は未実装であり、これは契約の
自己整合性の固定であって、実装の検証ではない。実装コードと既存試験の期待値は変更していない。
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import random
from pathlib import Path

import pytest

from aitrader.strict_validity import inspect_strict_validity

# 段3を通過した後に観測されてよい理由（＝結合で VALIDITY_NOT_SATISFIED へ写像するもの）
UNSATISFIED = {
    "VALIDITY_SET_MISMATCH", "VALIDITY_SUBJECT_DUPLICATE", "VALIDITY_SUBJECT_INVALID",
    "VALIDITY_RECORD_MISMATCH", "VALIDITY_PERIOD_INVALID",
    "VALIDITY_NOT_YET_EFFECTIVE", "VALIDITY_EXPIRED",
}
# 段3で止めるべき理由。段5で観測されてよいのは「空リスト＝証拠ゼロ件」のときだけ。
STRUCTURAL = {"INVALID_VALIDITY_DOCUMENTS", "VALIDITY_HASH_MISMATCH"}
PAYLOAD_KEYS = {"group", "code", "record_sha256", "valid_from", "valid_until"}
HEX = set("0123456789abcdef")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _hex64(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def _aware(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _trimmed(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def stage3_form_ok(documents):
    """結合案 段3「期間証拠の事前形検査」の参照実装。空リストは欠落として通す。"""
    if not isinstance(documents, list):
        return False
    if not documents:
        return True
    identifiers = []
    for document in documents:
        if not isinstance(document, dict) or set(document) != {"id", "sha256", "payload"}:
            return False
        if not _trimmed(document["id"]) or not _hex64(document["sha256"]):
            return False
        payload = document["payload"]
        if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
            return False
        if not isinstance(payload["group"], str) or not isinstance(payload["code"], str):
            return False
        if not _hex64(payload["record_sha256"]):
            return False
        if not _aware(payload["valid_from"]) or not _aware(payload["valid_until"]):
            return False
        try:
            if document["sha256"] != digest(payload):
                return False
        except (TypeError, ValueError, OverflowError, RecursionError):
            return False
        identifiers.append(document["id"])
    return len(identifiers) == len(set(identifiers))


def _example():
    path = Path(__file__).resolve().parents[1] / "examples" / "strict_validity_valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _reseal(bundle):
    for document in bundle.get("validity_documents", []):
        if isinstance(document, dict) and "payload" in document:
            try:
                document["sha256"] = digest(document["payload"])
            except (TypeError, ValueError, OverflowError, RecursionError):
                pass


def _damage(mutate, reseal):
    bundle = _example()
    assert inspect_strict_validity(bundle)["status"] == "VERIFIED_OFFLINE_VALIDITY"
    mutate(bundle)
    if reseal:
        _reseal(bundle)
    before = deepcopy(bundle)
    result = inspect_strict_validity(bundle)
    assert bundle == before
    return bundle, result


def _first(bundle):
    return bundle["validity_documents"][0]


@pytest.mark.parametrize("mutate,reseal", [
    (lambda b: b.update(validity_documents={}), False),
    (lambda b: b["validity_documents"].__setitem__(0, "x"), False),
    (lambda b: _first(b).update(extra=1), False),
    (lambda b: _first(b).update(id=""), True),
    (lambda b: _first(b).update(id=" spaced "), True),
    (lambda b: _first(b).update(sha256="zz"), False),
    (lambda b: _first(b).update(sha256="0" * 64), False),
    (lambda b: _first(b).update(payload=[]), True),
    (lambda b: _first(b)["payload"].update(extra=True), True),
    (lambda b: _first(b)["payload"].pop("valid_until"), True),
    (lambda b: _first(b)["payload"].update(group=1), True),
    (lambda b: _first(b)["payload"].update(code=None), True),
    (lambda b: _first(b)["payload"].update(record_sha256=123), True),
    (lambda b: _first(b)["payload"].update(valid_from=5), True),
    (lambda b: _first(b)["payload"].update(valid_from="2026-09-10T00:00:00"), True),
    (lambda b: b["validity_documents"][1].update(id=b["validity_documents"][0]["id"]), True),
])
def test_structural_damage_is_stopped_at_stage_three(mutate, reseal):
    bundle, _ = _damage(mutate, reseal)
    assert stage3_form_ok(bundle.get("validity_documents")) is False


@pytest.mark.parametrize("mutate,reseal", [
    (lambda b: b["validity_documents"].pop(), False),
    (lambda b: b["validity_documents"].append(dict(
        id="another-identifier",
        sha256=digest(b["validity_documents"][0]["payload"]),
        payload=deepcopy(b["validity_documents"][0]["payload"]))), False),
    (lambda b: _first(b)["payload"].update(group="prices"), True),
    (lambda b: _first(b)["payload"].update(code="9999"), True),
    (lambda b: _first(b)["payload"].update(record_sha256="a" * 64), True),
    (lambda b: _first(b)["payload"].update(valid_from="2026-09-20T00:00:00+09:00"), True),
    (lambda b: _first(b)["payload"].update(valid_until="2026-09-11T00:00:00+09:00"), True),
])
def test_unsatisfied_damage_passes_stage_three_and_stays_unsatisfied(mutate, reseal):
    bundle, result = _damage(mutate, reseal)
    assert stage3_form_ok(bundle["validity_documents"]) is True
    assert result["status"] == "DATA_INCOMPLETE"
    assert set(result["reason_codes"]) <= UNSATISFIED


def test_empty_list_is_the_only_structural_reason_left_after_stage_three():
    """空リストだけは段3を通り、段5で証拠欠落として現れる。"""
    bundle, result = _damage(lambda b: b.update(validity_documents=[]), False)
    assert stage3_form_ok(bundle["validity_documents"]) is True
    assert result["reason_codes"] == ["INVALID_VALIDITY_DOCUMENTS"]
    assert result["expected_evidence_count"] == 2
    assert result["verified_evidence_count"] == 0


@pytest.mark.parametrize("seed", range(6))
def test_random_damage_never_leaks_a_structural_reason_past_stage_three(seed):
    rng = random.Random(seed)
    values = [None, 0, 1, True, "", " x ", "x", "0" * 63, "0" * 64, "A" * 64,
              "2026-09-10T00:00:00", "2026-09-10T00:00:00+09:00", "prices", "lots",
              "0001", "9999", [], {}, 1.5]
    for _ in range(120):
        bundle = _example()
        documents = bundle["validity_documents"]
        for _ in range(rng.randint(1, 3)):
            target = rng.choice(documents) if documents else None
            action = rng.choice(["doc_key", "drop_doc_key", "payload_key", "drop_payload_key",
                                 "extra_payload_key", "duplicate", "drop", "replace", "empty"])
            if action == "empty":
                bundle["validity_documents"] = documents = []
                continue
            if not documents:
                continue
            if action == "duplicate":
                documents.append(deepcopy(rng.choice(documents)))
                continue
            if action == "drop":
                documents.pop(rng.randrange(len(documents)))
                continue
            if action == "replace":
                documents[rng.randrange(len(documents))] = rng.choice(values)
                continue
            if not isinstance(target, dict):
                continue
            if action == "doc_key":
                target[rng.choice(["id", "sha256", "payload"])] = rng.choice(values)
            elif action == "drop_doc_key":
                target.pop(rng.choice(["id", "sha256", "payload"]), None)
            elif isinstance(target.get("payload"), dict):
                key = rng.choice(sorted(PAYLOAD_KEYS))
                if action == "payload_key":
                    target["payload"][key] = rng.choice(values)
                elif action == "drop_payload_key":
                    target["payload"].pop(key, None)
                else:
                    target["payload"]["extra"] = rng.choice(values)
        if rng.random() < 0.5:
            _reseal(bundle)
        if not stage3_form_ok(bundle.get("validity_documents")):
            continue
        reasons = set(inspect_strict_validity(bundle)["reason_codes"])
        if reasons == {"INVALID_VALIDITY_DOCUMENTS"} and bundle["validity_documents"] == []:
            continue
        assert not (reasons & STRUCTURAL), (sorted(reasons), bundle["validity_documents"])
