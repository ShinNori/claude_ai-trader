"""v0.3.5 independent review; production ops files are read-only."""
from contextlib import closing
from datetime import timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'ops/tests'), str(ROOT/'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT
from test_v034_contracts import provenance, settlement
from aitrader_ops.ledger import Ledger, LedgerError
from aitrader_ops.models import PositionIn


def ingest(ledger, source, eid='old', at=None, **changes):
    if source == 'TRADE':
        return ledger.report(trade(eid=eid, at=at, **changes))
    return ledger.import_csv_fills([csv(eid=eid, at=at, **changes)])


@pytest.mark.parametrize('source', ['TRADE','CSV_FILL'])
@pytest.mark.parametrize('resend', ['TRADE','CSV_FILL'])
def test_l01_discard_replay_sync_cross_kind_and_conflict(make_ledger, source, resend):
    """DISCARD記憶の別ハンドル同期、交差再送、監査優先順位と再起動を確認する。"""
    ledger = make_ledger(initialize=False)
    ledger.init_snapshot(1000000, [], [], AT, provenance=provenance())
    ledger.create_notice(proposal(), at=AT)
    late = AT-timedelta(seconds=1)
    ingest(ledger, source, at=late)
    before = ledger.seq()
    reader = Ledger(ledger.path)
    try:
        ledger.resolve_pending('old', None, AT+timedelta(seconds=1), 'DISCARD')
        after = ledger.seq()
        for action in ('DISCARD','APPLY'):
            with pytest.raises(LedgerError):
                ledger.resolve_pending('old','buy1',AT+timedelta(seconds=2),action)
        result = ingest(reader, resend, at=late)
        assert result.duplicate if resend == 'TRADE' else result.skipped == ['old']
        assert reader.pending_rows() == {} and reader.seq() == after
        with closing(sqlite3.connect(ledger.path)) as con:
            row = con.execute('SELECT outcome,reason_code FROM ingest_attempts ORDER BY rowid DESC LIMIT 1').fetchone()
        assert row == ('DUPLICATE', 'DISCARDED' if resend == source else 'ID_PAYLOAD_CONFLICT')
        ingest(reader, source, at=late, price=999.)
        with closing(sqlite3.connect(ledger.path)) as con:
            assert con.execute('SELECT reason_code FROM ingest_attempts ORDER BY rowid DESC LIMIT 1').fetchone()[0] == 'ID_PAYLOAD_CONFLICT'
        assert ledger.replay_known(before) == ledger.replay_known(after) == ledger.replay(AT) == ledger.replay()
    finally:
        reader.close()
    reopened = Ledger(ledger.path)
    try:
        result = ingest(reopened, source, at=late)
        assert result.duplicate if source == 'TRADE' else result.skipped == ['old']
        assert reopened.pending_rows() == {} and reopened.cash() == 1000000
    finally:
        reopened.close()


@pytest.mark.parametrize('source',['TRADE','CSV_FILL'])
def test_l02_before_snapshot_is_empty_even_with_late_pending(make_ledger, source):
    """開始残高前の時点再生は、後着したcutover前保留行があっても空であるべき。"""
    ledger = make_ledger(initialize=False)
    ledger.init_snapshot(1000000, [], [], AT, provenance=provenance())
    ingest(ledger, source, at=AT-timedelta(hours=2))
    view = ledger.replay(AT-timedelta(hours=1))
    assert view.cash == 0 and view.positions == {}


def test_l03_discard_before_pending_business_time_replays(make_ledger):
    """保留より早いDISCARDは拒否し、同時刻以降の解決と中間時点の残高を再生できる。"""
    ledger = make_ledger()
    ledger.import_csv_fills([csv(at=AT+timedelta(hours=2))])
    seq, balance, pending = ledger.seq(), ledger.view(), ledger.pending_rows()
    with pytest.raises(LedgerError):
        ledger.resolve_pending('csv1', None, AT, 'DISCARD')
    assert ledger.seq() == seq and ledger.view() == balance and ledger.pending_rows() == pending
    ledger.resolve_pending('csv1', None, AT+timedelta(hours=2), 'DISCARD')
    assert ledger.pending_rows() == {} and ledger.view() == balance
    assert ledger.replay(AT+timedelta(hours=1)).cash == ledger.cash()
    assert ledger.replay(AT+timedelta(hours=2)) == ledger.replay_known(ledger.seq())


@pytest.mark.parametrize('source',['TRADE','CSV_FILL'])
def test_l04_raw_id_collision_rolls_back_and_continues(make_ledger, source):
    """故障注入の履歴ID衝突を公開APIのerrorへ畳み、後続の正当な約定を処理する。"""
    ledger = make_ledger()
    ledger.create_notice(proposal(qty=200),at=AT)
    prefix = 'trade' if source == 'TRADE' else 'csv'
    with closing(sqlite3.connect(ledger.path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    [prefix+':collision','ADJUST',AT.isoformat(),json.dumps(dict(kind='DEPOSIT',amount=0)),AT.isoformat()])
    seq = ledger.seq()
    if source == 'CSV_FILL':
        result = ledger.import_csv_fills([csv(eid='collision'),csv(eid='valid',broker_order_id='o2')])
        assert result.errors.get('collision') and result.applied == ['valid']
    else:
        result = ledger.report(trade(eid='collision'))
        assert result.error and not result.applied
        assert ledger.report(trade(eid='valid',broker_order_id='o2')).applied
    assert ledger.seq() == seq+1 and ledger.cash() == 900000
    assert ledger.replay() == ledger.view()


@pytest.mark.parametrize('padding',[' ', '\u3000','\n'])
def test_l05_strip_includes_unicode_and_newline(make_ledger, padding):
    """実装のstr.stripは全角空白と改行も同一視する（引き渡しの対象外記述とは相違）。"""
    ledger = make_ledger()
    data = settlement()
    ledger.adjust('DIVIDEND',700,'6857',None,AT,json.dumps(data))
    data['source_event_id'] = padding+data['source_event_id']+padding
    with pytest.raises(LedgerError):
        ledger.adjust('DIVIDEND',700,'6857',None,AT,json.dumps(data))
    assert ledger.cash() == 1000700


@pytest.mark.parametrize('kind',['DEPOSIT','WITHDRAW','FEE'])
def test_l06_settlement_scope_rejects_other_kinds(make_ledger, kind):
    ledger = make_ledger()
    with pytest.raises(LedgerError):
        ledger.adjust(kind,700,'6857',None,AT,json.dumps(settlement()))
    assert ledger.seq() == 1 and ledger.cash() == 1000000


def test_l07_equal_cutover_other_offset_and_future_rejection(make_ledger):
    ledger = make_ledger(initialize=False)
    data = provenance(); data['cutover_at'] = AT.astimezone(timezone.utc).isoformat()
    ledger.init_snapshot(1000000,[],[],AT,provenance=data)
    assert ledger.replay(AT).cash == 1000000
    other = make_ledger(initialize=False)
    data['cutover_at'] = (AT+timedelta(microseconds=1)).isoformat()
    with pytest.raises(LedgerError):
        other.init_snapshot(1000000,[],[],AT,provenance=data)
    assert other.seq() == 0


def test_l08_repurchase_rejects_post_exit_and_cashout_remains_separate(make_ledger):
    """全量売却後でも再購入後はPOST_EXIT精算を拒否し、保有中cashout経路は別管理。"""
    ledger = make_ledger(positions=[PositionIn('6857',100,1000.)])
    ledger.create_notice(proposal('sell',side='SELL'),at=AT)
    assert ledger.report(trade('sell',eid='sell')).applied
    ledger.create_notice(proposal(),at=AT)
    assert ledger.report(trade(eid='buy',broker_order_id='buy')).applied
    with pytest.raises(LedgerError):
        ledger.adjust('DIVIDEND',700,'6857',None,AT,json.dumps(settlement()))
    ledger.adjust('FRACTIONAL_CASHOUT',700,'6857',None,AT,'held cashout')
    assert ledger.positions()['6857'].qty == 100 and ledger.cash() == 1000700
