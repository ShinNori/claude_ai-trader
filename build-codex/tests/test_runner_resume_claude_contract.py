"""Claude 第25回 独立反証: managed-v1 の明示的再開（D16 v2）で Codex の 31 試験が触れていない角度。

対象: aitrader/runner_resume.py と aitrader/runner.py の再開分岐。製品・既存試験・期待値は変更しない。
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "ops"), str(ROOT / "build-codex"), str(ROOT / "common/tests/phase2")]

from aitrader import runner, runner_resume
from aitrader.db import connect, init
from aitrader.managed_stop import initialize_managed_mock
from aitrader.packet import Proposal, packet_hash
from aitrader.runner import RunError, Valuation, mock_verdicts, run
from aitrader_ops.ledger import Ledger
from aitrader_ops.models import JST, CsvFill, PositionIn, Verdict

DAY = date(2026, 9, 8)
AS_OF = date(2026, 9, 7)
NOW = datetime(2026, 9, 8, 7, 5, tzinfo=JST)
SETTINGS = {"line": {"allowed_user_id": "resume-claude", "monthly_budget": 10}}


def proposal(pid="resume-buy", *, side="BUY", code="7203", price=100.0):
    value = Proposal(pid, "", code, side, 100, 100, price, "OPENING_LIMIT", "CASH", "resume", "v1", AS_OF,
                     "snapshot", "review-v1", datetime(2026, 9, 8, 8, 59, tzinfo=JST), "resume fixture",
                     {"next_earnings_date": None, "margin_regulated": False})
    return replace(value, packet_hash=packet_hash(value))


def make_home(tmp_path, *, cash=1_000_000):
    home = tmp_path / "managed"
    positions = [PositionIn("6857", 300, 100.0), PositionIn("7203", 300, 100.0)]
    initialize_managed_mock(home, cash, positions, NOW - timedelta(days=1), settings=SETTINGS)
    init(home)
    with connect(home) as db:
        for offset in range(-3, 4):
            day = DAY + timedelta(days=offset)
            db.execute("INSERT INTO calendar VALUES(?,?)", [day, day.weekday() < 5])
        db.executemany("INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)",
                       [("7203", AS_OF, 100.0), ("6857", AS_OF, 100.0)])
    return home


def execute(home, p=None, *, run_id="resume-run", now=NOW, votes=None, unresolved=False):
    p = p or proposal()
    votes = votes if votes is not None else mock_verdicts([p], run_id, NOW)
    return run(home, run_id, DAY, [p], votes, now, Valuation(1_060_000, 1_060_000), unresolved_unconfirmed=unresolved)


def crash_c2(home, monkeypatch, p=None):
    p = p or proposal()
    original = Ledger.set_notice_state
    monkeypatch.setattr(Ledger, "set_notice_state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("C2")))
    with pytest.raises(RuntimeError, match="C2"):
        execute(home, p)
    monkeypatch.setattr(Ledger, "set_notice_state", original)
    return p


def ledger_snapshot(home, pid="resume-buy"):
    with closing(Ledger(home / "ledger.sqlite")) as ledger:
        return ledger.seq(), ledger.notice(pid)["notice_state"], ledger.reserved(), dict(ledger.view().reserved_shares)


def journal_snapshot(home, pid="resume-buy"):
    with sqlite3.connect(home / "orchestration.sqlite") as db:
        state, result = db.execute("SELECT state,result FROM candidates WHERE pid=?", [pid]).fetchone()
        outbox = db.execute("SELECT count(*) FROM outbox WHERE key LIKE '%:CANDIDATE'").fetchone()[0]
        manifest = json.loads(db.execute("SELECT manifest FROM runs WHERE id='resume-run'").fetchone()[0])
    return state, json.loads(result), outbox, manifest


def test_changed_verdicts_on_resume_are_refused_by_input_immutability_before_any_write(monkeypatch, tmp_path):
    """再開は同 run_id・同入力に限る: 判定を差し替えた再実行は request_hash 検査で拒否され、台帳・journal に書かない。"""
    home = make_home(tmp_path)
    p = crash_c2(home, monkeypatch)
    before_l, before_j = ledger_snapshot(home), journal_snapshot(home)
    votes = {p.proposal_id: [Verdict("claude", p.proposal_id, p.packet_hash, "APPROVE", (), "", None, NOW, "mock", "mock", "resume-run"),
                             Verdict("codex", p.proposal_id, p.packet_hash, "REJECT", (), "", None, NOW, "mock", "mock", "resume-run")]}
    with pytest.raises(RunError, match="同じrun_idの入力変更はできません"):
        execute(home, p, now=NOW + timedelta(minutes=1), votes=votes)
    assert ledger_snapshot(home) == before_l and journal_snapshot(home)[:3] == before_j[:3]
    # 判定ヘルパー単体: REJECT 判定は REVIEW_INVALID（不許可ではなく無効扱い）で拒否される
    with pytest.raises(runner_resume.ResumeRefused) as exc:
        runner_resume.validate_verdicts(p, votes[p.proposal_id], run_id="resume-run", execution_day=DAY,
                                        now=NOW, deadline=datetime(2026, 9, 8, 7, 15, tzinfo=JST), aware=runner.aware)
    assert exc.value.code == "REVIEW_INVALID"


def test_excluded_view_does_not_change_valuation(tmp_path):
    home = make_home(tmp_path)
    p = proposal()
    with closing(Ledger(home / "ledger.sqlite")) as ledger:
        ledger.create_notice(p, at=NOW)
        view = ledger.view()
        notice = ledger.notice(p.proposal_id)
    excluded = runner_resume.reservation_excluded_view(view, notice, p)
    marks = {"7203": 100.0, "6857": 100.0}
    valuation = Valuation(1_060_000, 1_060_000)
    assert valuation.calculate(excluded, marks) == valuation.calculate(view, marks)
    assert excluded.available == view.available + notice["reserve"] and excluded.cash == view.cash
    assert view.positions == excluded.positions and view.reserved_positions != excluded.reserved_positions


def test_resumed_item_keeps_intent_gate_as_original_without_aliasing(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    p = crash_c2(home, monkeypatch)
    intent_gate = copy.deepcopy(journal_snapshot(home)[1]["gate"])
    assert intent_gate["allowed"] is True
    result = execute(home, p, now=NOW + timedelta(minutes=1))
    item = result["candidates"][0]
    assert item["resume"]["from_state"] == "CREATED" and item["resume"]["gate_reevaluated"] is True
    assert item["resume"]["original_gate"] == intent_gate
    assert item["gate"]["allowed"] is True and item["gate"] is not item["resume"]["original_gate"]
    saved_state, saved_item, outbox, manifest = journal_snapshot(home)
    assert saved_state == "APPROVED" and saved_item["resume"]["original_gate"] == intent_gate and outbox == 1
    assert manifest["resumes"][0]["from_states"] == {p.proposal_id: "CREATED"} and manifest["started_at"] == NOW.isoformat()


def test_other_run_cannot_adopt_interrupted_candidate(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    p = crash_c2(home, monkeypatch)
    before = ledger_snapshot(home)
    with pytest.raises(RunError):
        execute(home, p, run_id="another-run", votes=mock_verdicts([p], "another-run", NOW), now=NOW + timedelta(minutes=1))
    assert ledger_snapshot(home) == before and journal_snapshot(home)[0] == "INTENT"


def _add_pending_row(home):
    """台帳に保留行（未知 proposal の CSV 約定）を作り、入力を変えずに「未照合あり」の状態にする。"""
    with closing(Ledger(home / "ledger.sqlite")) as ledger:
        ledger.import_csv_fills([CsvFill("pending-1", "unknown-pid", "6857", "SELL", 100, 100.0, 0.0,
                                         NOW - timedelta(hours=1), "csv", "o-1")])
        assert ledger.pending_rows()


def test_sell_c2_resumes_with_pending_ledger_rows_while_buy_c2_is_refused(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    sell = proposal("resume-sell", side="SELL", code="6857")
    crash_c2(home, monkeypatch, sell)
    _add_pending_row(home)
    result = execute(home, sell, now=NOW + timedelta(minutes=1))
    assert result["candidates"][0]["resume"]["from_state"] == "CREATED" and result["status"] == "CANDIDATES"
    assert ledger_snapshot(home, "resume-sell")[1] == "APPROVED"
    buy_home = make_home(tmp_path / "buy")
    buy = crash_c2(buy_home, monkeypatch)
    _add_pending_row(buy_home)
    with pytest.raises(RunError, match="NEEDS_RECONCILIATION: UNRESOLVED_LEDGER"):
        execute(buy_home, buy, now=NOW + timedelta(minutes=1))
    assert ledger_snapshot(buy_home)[1] == "CREATED"
    failure = json.loads((buy_home / "runs" / DAY.isoformat() / "resume-run" / "failure.json").read_text(encoding="utf-8"))
    assert failure["status"] == "SYSTEM_ERROR" and failure["detail"] == "NEEDS_RECONCILIATION: UNRESOLVED_LEDGER"


def test_resume_adds_exactly_one_ledger_event_and_is_idempotent(monkeypatch, tmp_path):
    home = make_home(tmp_path)
    p = crash_c2(home, monkeypatch)
    seq0 = ledger_snapshot(home)[0]
    first = execute(home, p, now=NOW + timedelta(minutes=1))
    seq1 = ledger_snapshot(home)[0]
    assert seq1 == seq0 + 1
    second = execute(home, p, now=NOW + timedelta(minutes=2))
    assert second == first and ledger_snapshot(home)[0] == seq1 and journal_snapshot(home)[2] == 1
