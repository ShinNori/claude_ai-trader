from contextlib import closing
from datetime import timedelta, timezone
import gc
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'),str(ROOT/'ops/tests'),str(ROOT/'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT
from test_migration import legacy
from test_v034_contracts import provenance
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError
from aitrader_ops.migrate import apply, check
from aitrader_ops.models import OpenOrderIn, PositionIn


def test_n01_migrated_snapshot_before_and_after_with_gc(legacy):
    """接続終了後の原本はgcと移行でも不変、移行済みの開始残高前は空。"""
    original = legacy.read_bytes()
    gc.collect()
    assert legacy.read_bytes() == original
    result = apply(legacy)
    gc.collect()
    assert legacy.read_bytes() == original
    ledger = Ledger(result['output'])
    try:
        empty = ledger.replay((AT-timedelta(microseconds=1)).astimezone(timezone.utc))
        assert empty.cash == 0 and not empty.positions and empty.reserved == 0
        assert ledger.replay(AT).positions['6857'].qty == 200
        assert ledger.replay_known(result['old_last_seq']) == ledger.view()
    finally:
        ledger.close()


@pytest.mark.parametrize('action',['APPLY','DISCARD'])
def test_n02_early_resolution_preserves_state_and_audits_rejection(make_ledger, action):
    """早い解決は残高と保留を維持し、拒否監査を残す（文書の監査なし記述との相違）。"""
    ledger = make_ledger()
    ledger.import_csv_fills([csv(at=AT)])
    ledger.create_notice(proposal(),at=AT)
    seq, balance = ledger.seq(), ledger.view()
    with pytest.raises(LedgerError):
        ledger.resolve_pending('csv1','buy1',AT-timedelta(microseconds=1),action)
    assert ledger.seq() == seq and ledger.view() == balance and 'csv1' in ledger.pending_rows()
    with closing(sqlite3.connect(ledger.path)) as con:
        assert con.execute('SELECT outcome,reason_code FROM ingest_attempts ORDER BY rowid DESC LIMIT 1').fetchone() == ('REJECTED','VALIDATION')
    ledger.resolve_pending('csv1','buy1',AT.astimezone(timezone.utc),action)
    assert ledger.pending_rows() == {}
    assert ledger.replay(AT) == ledger.replay_known(ledger.seq()) == ledger.view()


@pytest.mark.parametrize('at',[None, AT-timedelta(hours=1)])
def test_n03_cutover_trade_resolution_reopens_and_replays(make_ledger, at):
    ledger = make_ledger(initialize=False)
    ledger.init_snapshot(1000000, [], [], AT, provenance=provenance())
    assert ledger.report(trade(at=at)).pending
    resolution = AT if at is None else at
    if at is not None:
        with pytest.raises(LedgerError):
            ledger.resolve_pending('fill1',None,at-timedelta(seconds=1),'DISCARD')
    ledger.resolve_pending('fill1',None,resolution,'DISCARD')
    reopened = Ledger(ledger.path)
    try:
        assert reopened.pending_rows() == {}
        assert reopened.replay(AT).cash == 1000000
        assert reopened.replay(AT-timedelta(microseconds=1)).cash == 0
        assert reopened.report(trade(at=at)).duplicate
    finally:
        reopened.close()


def test_n04_old_inverse_resolution_has_migration_location(make_ledger):
    ledger = make_ledger()
    ledger.import_csv_fills([csv(at=AT+timedelta(hours=1))])
    path = ledger.path
    ledger.close()
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    ['old-resolution','PENDING_RESOLVED',AT.isoformat(),json.dumps(dict(source_event_id='csv1',action='DISCARD',at=AT.isoformat())),AT.isoformat()])
    with pytest.raises(MigrationError) as error:
        Ledger(path)
    assert error.value.seq == 3 and error.value.kind == 'PENDING_RESOLVED'
    result = check(path)
    assert result['violation']['seq'] == 3 and result['violation']['kind'] == 'PENDING_RESOLVED'


def test_n05_external_orders_and_late_correction_before_snapshot(make_ledger):
    ledger = make_ledger(initialize=False)
    ledger.init_snapshot(1000000,[PositionIn('6857',100,1000.)],
                         [dict(code='6857',side='SELL',qty=100,limit_price=1000.,proposal_id='external')],
                         AT,provenance=provenance())
    assert ledger.report(trade(pid='external',kind='CORRECTION',at=AT-timedelta(hours=1))).pending
    ledger.create_notice(proposal(code='7203'),at=AT+timedelta(hours=1))
    empty = ledger.replay(AT-timedelta(seconds=1))
    assert empty.cash == empty.reserved == 0 and not empty.positions and not empty.reserved_shares
    assert ledger.replay(AT).reserved_shares == {'6857':100}
