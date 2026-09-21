"""Opus 第3回: 期間証拠の理由コードが結合の2分類へ一意に落ちるかを固定する。

[結合案](../HISTORY_VALIDITY_BINDING_PLAN.md)は、期間証拠の「構造/型/原本自己hash破損」を
INVALID_BUNDLE、「構造が正しい証拠の欠落・重複・行hash不一致・期間外」を
VALIDITY_NOT_SATISFIED へ分類すると決めている。この試験は strict_validity_fixture_v1 が
実際に返す理由コードを損傷種別ごとに固定し、その分類が既存の理由コードだけでは
決まらないこと（VALIDITY_SUBJECT_INVALID と VALIDITY_PERIOD_INVALID が両方の分類に
またがること）を機械可読な形で残す。現在の挙動の追認であって、是認ではない。
実装コードと既存試験の期待値は変更していない。
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from aitrader.strict_validity import inspect_strict_validity

STRUCTURE = "INVALID_BUNDLE"          # 結合が構造破損として扱うべき損傷
UNSATISFIED = "VALIDITY_NOT_SATISFIED"  # 結合が期間不適合として扱うべき損傷


def _example():
    path = Path(__file__).resolve().parents[1] / "examples" / "strict_validity_valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _reseal(document):
    document["sha256"] = hashlib.sha256(_canonical(document["payload"])).hexdigest()


def _damaged(mutate, reseal=True):
    bundle = _example()
    assert inspect_strict_validity(bundle)["status"] == "VERIFIED_OFFLINE_VALIDITY"
    document = bundle["validity_documents"][0]
    mutate(bundle, document)
    if reseal:
        _reseal(document)
    before = deepcopy(bundle)
    result = inspect_strict_validity(bundle)
    assert bundle == before
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["verified_evidence_count"] < result["expected_evidence_count"]
    return result["reason_codes"]


@pytest.mark.parametrize("intended,mutate", [
    (STRUCTURE, lambda b, d: d["payload"].update(record_sha256=123)),
    (STRUCTURE, lambda b, d: d["payload"].update(extra=True)),
    (UNSATISFIED, lambda b, d: d["payload"].update(group="prices")),
])
def test_subject_invalid_covers_both_intended_classes(intended, mutate):
    """同じ VALIDITY_SUBJECT_INVALID が構造破損と対象違いの双方で立つ。"""
    assert "VALIDITY_SUBJECT_INVALID" in _damaged(mutate)


@pytest.mark.parametrize("intended,mutate", [
    (STRUCTURE, lambda b, d: d["payload"].update(valid_from=5)),
    (STRUCTURE, lambda b, d: d["payload"].update(valid_from="2026-09-10T00:00:00")),
    (UNSATISFIED, lambda b, d: d["payload"].update(valid_from="2026-09-20T00:00:00+09:00")),
])
def test_period_invalid_covers_both_intended_classes(intended, mutate):
    """同じ VALIDITY_PERIOD_INVALID が型破損と期間の値不整合の双方で立つ。"""
    assert "VALIDITY_PERIOD_INVALID" in _damaged(mutate)


@pytest.mark.parametrize("expected,mutate,reseal", [
    ("INVALID_VALIDITY_DOCUMENTS", lambda b, d: b.update(validity_documents=[]), False),
    ("VALIDITY_HASH_MISMATCH", lambda b, d: d.update(sha256="0" * 64), False),
    ("VALIDITY_SET_MISMATCH", lambda b, d: b["validity_documents"].pop(), False),
    ("VALIDITY_RECORD_MISMATCH", lambda b, d: d["payload"].update(record_sha256="a" * 64), True),
    ("VALIDITY_EXPIRED", lambda b, d: d["payload"].update(valid_until="2026-09-11T00:00:00+09:00"), True),
])
def test_unambiguous_damages_keep_a_single_class(expected, mutate, reseal):
    """残りの損傷は片方の分類にしか現れない（結合の対応表の確定部分）。"""
    if expected == "INVALID_VALIDITY_DOCUMENTS":
        bundle = _example()
        bundle["validity_documents"] = []
        result = inspect_strict_validity(bundle)
        assert result["reason_codes"] == [expected]
        return
    assert expected in _damaged(mutate, reseal=reseal)
