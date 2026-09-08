"""v0.3追加検証。共通受入テストを変更しない。"""
import sqlite3
import sys
from pathlib import Path
from datetime import timedelta

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'build-codex'), str(ROOT/'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT, balances
from aitrader_ops.ledger import Ledger, LedgerError


def fail_audit(l):
    with sqlite3.connect(l.path) as con:
        con.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON ingest_attempts BEGIN SELECT RAISE(FAIL,'injected'); END")


def test_csv_audit_failure_rolls_back_after_reopen(make_ledger):
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    before = balances(l); seq = l.seq(); fail_audit(l)
    with pytest.raises(sqlite3.DatabaseError):
        l.import_csv_fills([csv('buy1')])
    reopened = Ledger(l.path)
    try:
        assert balances(reopened) == before and reopened.seq() == seq
    finally:
        reopened.close()


def test_conflicting_id_is_audited_without_mutation(make_ledger):
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    assert l.report(trade()).applied
    before = balances(l)
    assert l.report(trade(price=900.)).duplicate
    with sqlite3.connect(l.path) as con:
        assert con.execute('SELECT reason_code FROM ingest_attempts ORDER BY rowid DESC LIMIT 1').fetchone()[0] == 'ID_PAYLOAD_CONFLICT'
    assert balances(l) == before


def test_discard_audit_actor_and_replay(make_ledger):
    l = make_ledger(); assert l.import_csv_fills([csv()]).pending
    l.resolve_pending('csv1', None, AT+timedelta(hours=1), 'DISCARD', actor='test-operator', reason='重複を確認')
    assert not l.pending_rows()
    with sqlite3.connect(l.path) as con:
        detail = con.execute('SELECT detail FROM ingest_attempts ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    assert 'test-operator' in detail
    reopened = Ledger(l.path)
    try:
        assert not reopened.pending_rows()
    finally:
        reopened.close()


def test_future_notice_context_has_no_reservation(make_ledger):
    l = make_ledger(); l.create_notice(proposal(), at=AT+timedelta(hours=2))
    assert l.report(trade(kind='PARTIAL', qty=40, at=AT+timedelta(hours=1))).applied
    historical = l.replay(AT+timedelta(hours=1, minutes=1))
    assert historical.cash == 960000 and historical.reserved == 0
    assert l.reserved() == 60120


def test_timeless_line_second_fill_requires_reconciliation(make_ledger):
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    assert l.report(trade(kind='PARTIAL', qty=50, at=None)).applied
    result = l.report(trade(eid='fill2', qty=50, at=None))
    assert result.error and not result.applied and not result.duplicate


def test_external_cancel_no_longer_unconfirmed(make_ledger):
    from aitrader_ops.models import OpenOrderIn
    l = make_ledger(orders=[OpenOrderIn('old', '6857', 'BUY', 100, 1000.)])
    assert l.unconfirmed(AT) == ['old']
    assert l.report(trade('old', kind='CANCELLED', price=0)).applied
    assert not l.unconfirmed(AT+timedelta(days=1))
