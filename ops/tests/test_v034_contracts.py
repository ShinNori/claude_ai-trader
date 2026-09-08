import json
import sqlite3
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'build-codex'), str(ROOT/'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT
from aitrader_ops.ledger import Ledger, LedgerError


def provenance():
    return dict(method='RECONCILED_OPENING', source_path='synthetic-old.sqlite', source_sha256='a'*64,
                source_last_seq=4, reconciliation_id='rec1', reconciliation_record_path='synthetic-record.json',
                reconciliation_record_sha256='b'*64, actor='test-operator', reconciled_at=AT.isoformat(),
                reason='synthetic reconciliation', cutover_at=AT.isoformat(), previous_policy_version='3')


def settlement():
    return dict(category='POST_EXIT_SETTLEMENT', settlement_type='FRACTIONAL_CASHOUT', code='6857',
                source_event_id='statement-1', source_document_sha256='c'*64, reconciliation_id='rec1',
                actor='test-operator', received_at=AT.isoformat(), reason='synthetic cashout')


@pytest.mark.parametrize('missing', list(provenance()))
def test_j01_provenance_required_keys(make_ledger, missing):
    ledger = make_ledger(initialize=False)
    value = provenance(); del value[missing]
    with pytest.raises(LedgerError):
        ledger.init_snapshot(1000000, [], [], AT, provenance=value)
    assert ledger.seq() == 0


@pytest.mark.parametrize('field,value', [('source_sha256','bad'), ('source_last_seq',True),
    ('source_last_seq',0), ('actor',' '), ('cutover_at','2026-09-08T08:00:00'),
    ('reconciled_at',None)])
def test_j02_provenance_types(make_ledger, field, value):
    ledger = make_ledger(initialize=False)
    data = provenance(); data[field] = value
    with pytest.raises(LedgerError):
        ledger.init_snapshot(1000000, [], [], AT, provenance=data)
    assert ledger.seq() == 0


@pytest.mark.parametrize('source', ['TRADE','CSV_FILL'])
@pytest.mark.parametrize('at', [AT-timedelta(microseconds=1), None, AT])
def test_j03_cutover_persistence_and_boundary(make_ledger, source, at):
    ledger = make_ledger(initialize=False)
    ledger.init_snapshot(1000000, [], [], AT, provenance=provenance())
    ledger.create_notice(proposal(), at=AT)
    if source == 'TRADE':
        result = ledger.report(trade(eid='late', at=at))
        assert result.pending == (at != AT)
    else:
        result = ledger.import_csv_fills([csv(eid='late', at=at)])
        assert bool(result.pending) == (at != AT)
    if at != AT:
        assert ledger.cash() == 1000000
        assert ledger.pending_rows()['late']['_reason']
        with pytest.raises(LedgerError):
            ledger.resolve_pending('late', 'buy1', AT, 'APPLY')
        seq = ledger.seq()
        ledger.report(trade(eid='late', at=AT))
        assert ledger.seq() == seq
    else:
        assert ledger.cash() == 900000
    with closing(sqlite3.connect(ledger.path)) as con:
        payload = json.loads(con.execute("SELECT payload FROM ledger_events WHERE kind='SNAPSHOT'").fetchone()[0])
        assert payload['provenance'] == provenance()
        outcome = con.execute('SELECT outcome FROM ingest_attempts ORDER BY rowid LIMIT 1').fetchone()[0]
        assert outcome == ('PENDING' if at != AT else 'APPLIED')
    reopened = Ledger(ledger.path)
    try:
        assert reopened.view() == ledger.view() == ledger.replay() == ledger.replay_known(ledger.seq())
        assert reopened.pending_rows() == ledger.pending_rows()
    finally:
        reopened.close()
    if at != AT:
        ledger.resolve_pending('late', None, AT, 'DISCARD', actor='test', reason='in opening balance')
        assert ledger.pending_rows() == {} and ledger.cash() == 1000000


@pytest.mark.parametrize('missing', [k for k in settlement() if k != 'category'])
def test_j04_settlement_required_keys(make_ledger, missing):
    ledger = make_ledger()
    data = settlement(); del data[missing]
    with pytest.raises(LedgerError):
        ledger.adjust('DIVIDEND',700,'6857',None,AT,json.dumps(data))
    assert ledger.cash() == 1000000 and ledger.seq() == 1


@pytest.mark.parametrize('field,value', [('code','7203'), ('source_event_id',' '),
    ('source_document_sha256','x'*64), ('received_at','bad'), ('settlement_type','UNKNOWN')])
def test_j05_settlement_invalid_values(make_ledger, field, value):
    ledger = make_ledger()
    data = settlement(); data[field] = value
    with pytest.raises(LedgerError):
        ledger.adjust('DIVIDEND',700,'6857',None,AT,json.dumps(data))
    assert ledger.cash() == 1000000


def test_j06_settlement_dedup_across_handles_and_restart(make_ledger):
    ledger = make_ledger()
    other = Ledger(ledger.path)
    note = json.dumps(settlement())
    try:
        ledger.adjust('DIVIDEND',700,'6857',None,AT,note)
        with pytest.raises(LedgerError):
            other.adjust('DIVIDEND',800,'6857',None,AT,note)
        assert other.cash() == ledger.cash() == 1000700
    finally:
        other.close()
    reopened = Ledger(ledger.path)
    try:
        with pytest.raises(LedgerError):
            reopened.adjust('DIVIDEND',700,'6857',None,AT,note)
        assert reopened.view() == ledger.replay() == ledger.replay_known(ledger.seq())
        for text in ('not json', '{broken', '[]', '{"category":"OTHER"}'):
            reopened.adjust('DIVIDEND',1,'6857',None,AT,text)
        assert reopened.cash() == 1000704
    finally:
        reopened.close()


def test_j07_pending_audit_failure_rolls_back(make_ledger):
    ledger = make_ledger(initialize=False)
    ledger.init_snapshot(1000000, [], [], AT, provenance=provenance())
    with closing(sqlite3.connect(ledger.path)) as con, con:
        con.execute("CREATE TRIGGER deny BEFORE INSERT ON ingest_attempts BEGIN SELECT RAISE(FAIL,'audit failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        ledger.report(trade(at=AT-timedelta(days=1)))
    assert ledger.seq() == 1 and ledger.pending_rows() == {} and ledger.cash() == 1000000
