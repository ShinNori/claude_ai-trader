import json
from datetime import date, datetime, timedelta

import pytest

from aitrader.judges import CommandJudge, MockJudge, parse_verdict, run_judges
from aitrader.models import JST, GateResult, Proposal, packet_hash
from aitrader.notify import Outbox, format_notice


def make_proposal(**overrides) -> Proposal:
    as_of = date(2026, 9, 24)
    expires_at = datetime(2026, 9, 25, 8, 59, tzinfo=JST)
    kwargs = dict(
        proposal_id="A1-20260924-1111-01",
        packet_hash="",
        code="1111",
        side="BUY",
        qty=100,
        lot_size=100,
        limit_price=1005.0,
        exec_condition="OPENING_LIMIT",
        account_type="CASH",
        strategy="margin_bucket_long",
        strategy_version="v1",
        as_of=as_of,
        snapshot_id="snap1",
        policy_version="p1",
        expires_at=expires_at,
        reason="test",
        events={"next_earnings_date": "UNKNOWN", "margin_regulated": "UNKNOWN"},
    )
    kwargs.update(overrides)
    p = Proposal(**kwargs)
    return Proposal(**{**p.__dict__, "packet_hash": packet_hash(p)})


NOW = datetime(2026, 9, 24, 18, 0, tzinfo=JST)


def test_parse_verdict_fenced_json():
    p = make_proposal()
    raw = "here you go:\n```json\n{\"decision\": \"approve\", \"reason\": \"ok\", \"risks\": [\"r1\"], \"confidence\": 0.8}\n```\nthanks"
    v = parse_verdict(raw, judge="claude", proposal=p, received_at=NOW)
    assert v.decision == "APPROVE"
    assert v.risks == ("r1",)
    assert v.confidence == 0.8
    assert v.proposal_id == p.proposal_id
    assert v.packet_hash == p.packet_hash


def test_parse_verdict_prose_wrapped_json():
    p = make_proposal()
    raw = "I think this is fine. {\"decision\": \"REJECT\", \"reason\": \"risky\"} that's my answer."
    v = parse_verdict(raw, judge="codex", proposal=p, received_at=NOW)
    assert v.decision == "REJECT"


def test_parse_verdict_dict_input():
    p = make_proposal()
    raw = {"decision": "ABSTAIN", "reason": "unsure"}
    v = parse_verdict(raw, judge="claude", proposal=p, received_at=NOW)
    assert v.decision == "ABSTAIN"


def test_parse_verdict_none_is_invalid():
    p = make_proposal()
    v = parse_verdict(None, judge="claude", proposal=p, received_at=NOW)
    assert v.decision == "INVALID"
    assert v.reason


def test_parse_verdict_lowercase_decision():
    p = make_proposal()
    v = parse_verdict({"decision": "approve"}, judge="claude", proposal=p, received_at=NOW)
    assert v.decision == "APPROVE"


def test_parse_verdict_unknown_decision():
    p = make_proposal()
    v = parse_verdict({"decision": "MAYBE"}, judge="claude", proposal=p, received_at=NOW)
    assert v.decision == "INVALID"


def test_parse_verdict_changed_qty_is_invalid():
    p = make_proposal()
    v = parse_verdict({"decision": "APPROVE", "qty": p.qty + 100}, judge="claude", proposal=p, received_at=NOW)
    assert v.decision == "INVALID"
    assert "qty" in v.reason


def test_parse_verdict_changed_packet_hash_is_invalid():
    p = make_proposal()
    v = parse_verdict({"decision": "APPROVE", "packet_hash": "deadbeef"}, judge="claude", proposal=p, received_at=NOW)
    assert v.decision == "INVALID"
    assert "packet_hash" in v.reason


def test_parse_verdict_confidence_out_of_range_is_none():
    p = make_proposal()
    v = parse_verdict({"decision": "APPROVE", "confidence": 1.5}, judge="claude", proposal=p, received_at=NOW)
    assert v.decision == "APPROVE"
    assert v.confidence is None


def test_parse_verdict_unparsable_string():
    p = make_proposal()
    v = parse_verdict("not json at all, just text", judge="claude", proposal=p, received_at=NOW)
    assert v.decision == "INVALID"


def test_run_judges_one_verdict_each():
    p = make_proposal()
    judges = [
        MockJudge("claude", decision="APPROVE"),
        MockJudge("codex", decision="REJECT"),
    ]
    verdicts = run_judges(p, judges, NOW)
    assert len(verdicts) == 2
    names = {v.judge for v in verdicts}
    assert names == {"claude", "codex"}
    for v in verdicts:
        assert v.proposal_id == p.proposal_id
        assert v.packet_hash == p.packet_hash


def test_mock_judge_raw_override():
    p = make_proposal()
    j = MockJudge("claude", raw_override="{\"decision\": \"reject\"}")
    verdicts = run_judges(p, [j], NOW)
    assert verdicts[0].decision == "REJECT"


def test_command_judge_success():
    p = make_proposal()
    j = CommandJudge(
        "claude",
        ["python", "-c", "import sys;sys.stdin.read();print('{\"decision\":\"approve\"}')"],
        timeout_s=5.0,
    )
    verdicts = run_judges(p, [j], NOW)
    assert verdicts[0].decision == "APPROVE"


def test_command_judge_timeout_returns_invalid():
    p = make_proposal()
    j = CommandJudge("claude", ["sleep", "5"], timeout_s=0.5)
    verdicts = run_judges(p, [j], NOW)
    assert verdicts[0].decision == "INVALID"


def test_format_notice_contains_key_fields():
    p = make_proposal()
    gate = GateResult(allowed=True, category="NEW", reasons=[], reserve_amount=100500)
    text = format_notice(p, gate)
    assert p.code in text
    assert str(p.qty) in text
    assert str(p.limit_price) in text
    assert "2026-09-25" in text
    assert "発注は人間が行い" in text
    assert "自動発注なし" in text


def test_format_notice_not_allowed_lists_reasons():
    p = make_proposal()
    gate = GateResult(allowed=False, category="NEW", reasons=["理由A", "理由B"], reserve_amount=0)
    text = format_notice(p, gate)
    assert "理由A" in text
    assert "理由B" in text


def test_outbox_enqueue_and_list_roundtrip(tmp_path):
    p = make_proposal()
    gate = GateResult(allowed=True, category="NEW", reasons=[], reserve_amount=100500)
    ob = Outbox(tmp_path)
    path = ob.enqueue(p, gate, now=NOW)
    assert path.exists()
    items = ob.list(as_of=p.as_of)
    assert len(items) == 1
    assert items[0]["proposal"]["proposal_id"] == p.proposal_id
    assert items[0]["allowed"] is True

    items_all = ob.list()
    assert len(items_all) == 1


def test_outbox_enqueue_overwrite(tmp_path):
    p = make_proposal()
    gate1 = GateResult(allowed=True, category="NEW", reasons=[], reserve_amount=1)
    gate2 = GateResult(allowed=False, category="NEW", reasons=["x"], reserve_amount=0)
    ob = Outbox(tmp_path)
    ob.enqueue(p, gate1, now=NOW)
    ob.enqueue(p, gate2, now=NOW + timedelta(minutes=1))
    items = ob.list(as_of=p.as_of)
    assert len(items) == 1
    assert items[0]["allowed"] is False
    assert items[0]["reasons"] == ["x"]
