"""台帳の契約テスト。未定義のイベント入力型は属性レコードを使用。"""
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as Record

import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[3]/'ops'),str(Path(__file__).resolve().parents[3]/'build-codex')]
pytest.importorskip('aitrader_ops',reason='Claude担当のopsパッケージはまだ未実装')
from aitrader_ops.ledger import Ledger, LedgerNotInitialized
from aitrader_ops.models import Proposal
from aitrader.packet import packet_hash

JST=timezone(timedelta(hours=9)); AT=datetime(2026,9,8,8,0,tzinfo=JST)

def proposal():
    from dataclasses import replace
    p=Proposal(proposal_id='A1-20260908-6857-01',packet_hash='',code='6857',side='BUY',qty=100,
        lot_size=100,limit_price=1000.,exec_condition='OPENING_LIMIT',account_type='CASH',
        strategy='margin_bucket_long',strategy_version='v1',as_of=date(2026,9,7),snapshot_id='s1',
        policy_version='p1',expires_at=AT.replace(minute=59),reason='テスト',
        events={'next_earnings_date':None,'margin_regulated':False})
    return replace(p,packet_hash=packet_hash(p))

def event(kind='FILLED',id='e1',qty=100,price=1000,fee=0,source='line',broker='b1',at=AT+timedelta(hours=1)):
    return Record(event_id=id,proposal_id=proposal().proposal_id,kind=kind,qty=qty,price=price,
                  fee=fee,at=at,source=source,broker_order_id=broker)

def csv_fill(id='csv1',broker='b1'):
    # CsvFill schema is proposed in common/ISSUES.md; this is input data, not an implementation mock.
    return Record(event_id=id,proposal_id=proposal().proposal_id,code='6857',side='BUY',qty=100,
                  price=1000,fee=0,at=AT+timedelta(hours=1),source='csv',broker_order_id=broker)

@pytest.fixture
def ledger(tmp_path):
    l=Ledger(tmp_path/'ledger.sqlite'); l.init_snapshot(1000000,[],[],AT-timedelta(days=1))
    p=proposal(); l.create_notice(p, at=AT)
    l.set_notice_state(p.proposal_id,'APPROVED',AT)
    l.set_notice_state(p.proposal_id,'SENT',AT)
    return l

def balance(l):
    return l.cash(),l.reserved(),l.available(),{c:(p.qty,p.avg_price) for c,p in l.positions().items()}

def assert_ok(result):
    assert not result.error

def test_snapshot_required(tmp_path):
    """初期保有を知らない台帳が通知や約定を受け付けない。"""
    l=Ledger(tmp_path/'ledger.sqlite')
    with pytest.raises(LedgerNotInitialized): l.create_notice(proposal())
    with pytest.raises(LedgerNotInitialized): l.report(event())

def test_event_id_idempotence(ledger):
    """LINE再送で同じ買付が二回計上されない。"""
    assert_ok(ledger.report(event())); once=balance(ledger)
    ledger.report(event()); ledger.report(event())
    assert balance(ledger)==once
    assert ledger.cash()==900000 and ledger.positions()['6857'].qty==100

def test_same_id_changed_payload_does_not_mutate(ledger):
    """同一IDの金額だけ変えた再送が残高を書き換えない。"""
    assert_ok(ledger.report(event())); once=balance(ledger)
    ledger.report(event(price=999)); assert balance(ledger)==once

def test_repeat_csv(ledger):
    """同じCSVの再取込を重複売買にしない。"""
    ledger.import_csv_fills([csv_fill()]); once=balance(ledger)
    ledger.import_csv_fills([csv_fill()]); assert balance(ledger)==once
    assert ledger.cash()==900000 and ledger.positions()['6857'].qty==100

@pytest.mark.parametrize('csv_first',[False,True])
def test_csv_and_line_reverse_order(ledger,csv_first):
    """CSVとLINEの到着順序が逆でも同じ約定を一度だけ反映する。"""
    if csv_first:
        ledger.import_csv_fills([csv_fill()]); ledger.report(event())
    else:
        ledger.report(event()); ledger.import_csv_fills([csv_fill()])
    assert ledger.cash()==900000 and ledger.positions()['6857'].qty==100
    assert ledger.reserved()==0

def test_ambiguous_csv_does_not_add(ledger):
    """証券IDがなく既存約定に似た行は追加計上せず照合待ちにする。"""
    ledger.report(event(broker=None)); once=balance(ledger)
    result=ledger.import_csv_fills([csv_fill(broker=None)])
    assert balance(ledger)==once
    assert result.pending

def test_partial_then_cancel(ledger):
    """40株約定して残60株取消なら40株分だけ現金が減る。"""
    assert_ok(ledger.report(event('PARTIAL',qty=40)))
    assert_ok(ledger.report(event('CANCELLED',id='e2',qty=60,price=0)))
    assert ledger.cash()==960000 and ledger.positions()['6857'].qty==40 and ledger.reserved()==0

def test_chained_partial_and_fee(ledger):
    """部分約定の累積数量・単価・手数料を三回の増分から集計する。"""
    for kind,id,qty,price,fee in [('PARTIAL','e1',20,990,10),('PARTIAL','e2',30,1000,20),('FILLED','e3',50,980,30)]:
        assert_ok(ledger.report(event(kind,id,qty,price,fee)))
    assert ledger.positions()['6857'].qty==100
    assert ledger.cash()==1000000-(20*990+30*1000+50*980+60)
    assert ledger.reserved()==0

@pytest.mark.parametrize('seconds,expired',[(-1,False),(0,False),(1,True)])
def test_notice_expiry_keeps_reservation(ledger,seconds,expired):
    """通知期限の一秒境界でも実注文と予約を消さない。"""
    before=balance(ledger); p=proposal()
    ids=ledger.expire_notices(p.expires_at+timedelta(seconds=seconds))
    assert (p.proposal_id in ids) is expired
    assert balance(ledger)==before
    assert p.proposal_id in ledger.unconfirmed(AT+timedelta(days=1))

def test_unconfirmed_next_jst_morning(ledger):
    """翌06:50をUTC前日と誤認して未確認を自動見送りにしない。"""
    morning=datetime(2026,9,9,6,50,tzinfo=JST)
    assert proposal().proposal_id in ledger.unconfirmed(morning.astimezone(timezone.utc))

def test_late_fill_after_expiry(ledger):
    """通知期限後に届いた期限内注文の約定報告は受け付ける。"""
    ledger.expire_notices(AT+timedelta(hours=2))
    assert_ok(ledger.report(event(at=AT+timedelta(hours=3))))
    assert ledger.positions()['6857'].qty==100 and ledger.reserved()==0

def test_correction_reverses_previous_fill(ledger):
    """約定単価の訂正で元の買付を残したまま二重に現金を減らさない。"""
    ledger.report(event())
    assert_ok(ledger.report(event('CORRECTION',id='fix1',price=900)))
    assert ledger.cash()==910000 and ledger.positions()['6857'].qty==100
    once=balance(ledger); ledger.report(event('CORRECTION',id='fix1',price=900)); assert balance(ledger)==once

def test_report_rejects_negative_available_atomically(ledger):
    """予算超過の報告を一部だけ反映して負の余力を作らない。"""
    before=balance(ledger); result=ledger.report(event(price=20000))
    assert result.error and balance(ledger)==before and ledger.available()>=0

@pytest.mark.parametrize('qty,price,fee',[(101,1000,0),(-1,1000,0),(100,-1,0),(100,1000,-1)])
def test_invalid_fill_is_atomic(ledger,qty,price,fee):
    """超過株数・負数の報告が台帳へ部分反映されない。"""
    before=balance(ledger); result=ledger.report(event(qty=qty,price=price,fee=fee))
    assert result.error and balance(ledger)==before

def test_duplicate_notice_does_not_double_reserve(ledger):
    """同じ通知IDの再作成で予約額が倍にならない。"""
    before=balance(ledger)
    with pytest.raises(Exception): ledger.create_notice(proposal())
    assert balance(ledger)==before

def test_skipped_releases_reserve(ledger):
    """明示的な見送りのときだけ未確認の予約を解放する。"""
    assert ledger.reserved()==100200
    assert_ok(ledger.report(event('SKIPPED',qty=0,price=0)))
    assert ledger.reserved()==0 and ledger.cash()==1000000 and not ledger.positions()

def test_reopen_preserves_state(ledger,tmp_path):
    """プロセス再起動に相当する再接続で予約や保有を失わない。"""
    ledger.report(event('PARTIAL',qty=40)); before=balance(ledger)
    reopened=Ledger(tmp_path/'ledger.sqlite'); assert balance(reopened)==before

def test_changes_append_events_and_correction_keeps_history(ledger,tmp_path):
    """訂正処理で監査履歴を削除・上書きしない。"""
    def rows():
        with sqlite3.connect(tmp_path/'ledger.sqlite') as c:
            return c.execute('SELECT * FROM ledger_events').fetchall()
    before=rows(); assert before
    ledger.report(event()); filled=rows()
    ledger.report(event('CORRECTION',id='fix',price=900)); fixed=rows()
    assert len(fixed)>len(filled)>len(before)
    assert all(row in fixed for row in filled)

def test_stop_order_and_adjustment_audit(ledger,tmp_path):
    """逆指値記録と入金で既存株数を変えず変更履歴を追加する。"""
    ledger.report(event())
    ledger.set_stop_order('6857',True,930,AT+timedelta(hours=2))
    assert ledger.positions()['6857'].stop_order
    ledger.adjust('DEPOSIT',10000,None,None,AT+timedelta(hours=3),'テスト入金')
    assert ledger.cash()==910000 and ledger.positions()['6857'].qty==100

def test_ordered_is_not_a_fill(ledger):
    """発注報告だけで保有株を増やしたり現金を消費しない。"""
    before=balance(ledger)
    assert_ok(ledger.report(event('ORDERED',qty=100)))
    assert balance(ledger)==before

def test_reordered_old_order_notice_cannot_undo_fill(ledger):
    """遅れて到着した古い発注報告が約定済みを未約定へ戻さない。"""
    assert_ok(ledger.report(event())); before=balance(ledger)
    ledger.report(event('ORDERED',id='late-order',at=AT+timedelta(minutes=30)))
    assert balance(ledger)==before and ledger.reserved()==0

def test_partial_chain_survives_retry(ledger):
    """同じ部分約定イベントが混ざって再送されても100株に収まる。"""
    a=event('PARTIAL',id='p1',qty=40); b=event('FILLED',id='p2',qty=60)
    for ev in (a,a,b,a,b): ledger.report(ev)
    assert ledger.positions()['6857'].qty==100 and ledger.cash()==900000

def test_correction_history_values_remain_recoverable(ledger,tmp_path):
    """訂正後も元報告を履歴に保持し再接続でも訂正後残高を維持する。"""
    ledger.report(event()); ledger.report(event('CORRECTION',id='fix2',price=900))
    restored=Ledger(tmp_path/'ledger.sqlite')
    assert restored.cash()==910000 and restored.positions()['6857'].qty==100

def test_cannot_fill_more_than_remaining(ledger):
    """40株の部分約定後に追加100株を受け付けて予定数量を超えない。"""
    ledger.report(event('PARTIAL',qty=40)); before=balance(ledger)
    result=ledger.report(event('FILLED',id='overflow',qty=100))
    assert result.error and balance(ledger)==before
