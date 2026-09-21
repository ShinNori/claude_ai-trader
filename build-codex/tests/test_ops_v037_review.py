"""v0.3.7 independent boundary review; synthetic ledgers only."""
from contextlib import closing
from datetime import timedelta, timezone
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'ops/tests'),
                str(ROOT / 'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT
from test_v034_contracts import provenance
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError
from aitrader_ops.migrate import apply

H1, H2, H3, H4, H5, H6 = [AT + timedelta(hours=h) for h in range(1, 7)]
US = timedelta(microseconds=1)


def pending(l, eid='c1', qty=40, at=H1):
    assert l.import_csv_fills([csv('unknown', eid=eid, qty=qty, at=at,
                                  broker_order_id='broker-' + eid)]).pending == [eid]


def late(l):
    pending(l)
    l.create_notice(proposal('target'), at=H6)
    l.resolve_pending('c1', 'target', H2, 'APPLY')


@pytest.mark.parametrize('notice_at', [AT, H2, H4])
@pytest.mark.parametrize('timeless', [False, True])
def test_q01_partial_apply_notice_time_and_missing_time(make_ledger, notice_at, timeless):
    """通知作成が解決の前・同時・後でも部分約定の残数量を守り、時刻なし行を先取りしない。"""
    l = make_ledger()
    pending(l, at=None if timeless else H1)
    l.create_notice(proposal('target'), at=notice_at.astimezone(timezone(timedelta(hours=-5))))
    s_notice = l.seq()
    l.resolve_pending('c1', 'target', H2.astimezone(timezone.utc), 'APPLY')
    before = l.replay(H2 - US)
    assert before.cash == 1000000 and before.positions == {}
    assert before.reserved == (100200 if notice_at < H2 else 0)
    resolved = l.replay(H2)
    assert resolved.cash == 960000 and resolved.positions['6857'].qty == 40
    assert resolved.reserved == (60120 if notice_at <= H2 else 0)
    assert l.replay(H6) == l.view() == l.replay_known(l.seq())
    assert l.replay_known(s_notice).reserved == 100200
    with closing(Ledger(l.path)) as reopened:
        assert reopened.replay(H2) == resolved and reopened.pending_rows() == {}


@pytest.mark.parametrize('second_qty', [60, 70])
def test_q02_multiple_pending_apply_and_overflow(make_ledger, second_qty):
    """同一通知への40株＋60株は合算し、40株＋70株は保留と残高を維持して拒否する。"""
    l = make_ledger()
    pending(l)
    pending(l, 'c2', second_qty, H1 + timedelta(minutes=1))
    l.create_notice(proposal('target'), at=H6)
    l.resolve_pending('c1', 'target', H2, 'APPLY')
    balance, seq, rows = l.view(), l.seq(), l.pending_rows()
    if second_qty == 70:
        with pytest.raises(LedgerError):
            l.resolve_pending('c2', 'target', H3, 'APPLY')
        assert l.view() == balance and l.seq() == seq and l.pending_rows() == rows
        assert l.replay(H3).cash == 960000
    else:
        l.resolve_pending('c2', 'target', H3, 'APPLY')
        assert l.pending_rows() == {} and l.cash() == 900000
        assert l.replay(H3).positions['6857'].qty == 100
    assert l.replay(H2).positions['6857'].qty == 40 and l.replay(H2).reserved == 0
    assert l.replay(H6) == l.view()


@pytest.mark.parametrize('cancel_first', [False, True])
def test_q03_apply_correction_and_cancel(make_ledger, cancel_first):
    """APPLY後の訂正・取消は順序を入れ替えても原価を訂正し、予約を復活させない。"""
    l = make_ledger()
    late(l)
    correction_at, cancel_at = (H4, H3) if cancel_first else (H3, H4)
    operations = [('correction', correction_at), ('cancel', cancel_at)]
    for kind, at in sorted(operations, key=lambda item: item[1]):
        if kind == 'correction':
            result = l.report(trade('target', eid='fix', kind='CORRECTION', qty=40,
                                    price=900, replaces_event_id='c1', at=at))
        else:
            result = l.report(trade('target', eid='cancel', kind='CANCELLED', qty=60, price=0, at=at))
        assert result.applied
    assert l.replay(H2).cash == 960000 and l.replay(H2).reserved == 0
    assert l.replay(correction_at).cash == 964000
    assert l.replay(H5).reserved == 0
    assert l.replay(H6) == l.view() == l.replay_known(l.seq())
    assert l.positions()['6857'].avg_price == 900


def test_q04_context_notice_state_and_expiry_keep_zero_reservation(make_ledger):
    """context_only通知の承認・送信・期限切れで、未来の残60株予約を持ち込まない。"""
    l = make_ledger()
    late(l)
    l.set_notice_state('target', 'APPROVED', H3)
    l.set_notice_state('target', 'SENT', H3 + timedelta(minutes=1))
    assert l.expire_notices(H4) == ['target']
    for at in (H2, H3, H4, H5):
        v = l.replay(at)
        assert v.cash == 960000 and v.reserved == 0 and v.reserved_positions == {}
    assert l.replay(H6).reserved == 60120
    assert l.replay(H6) == l.view()


def test_q05_missing_notice_is_not_silently_ignored(make_ledger):
    """故障注入で確定APPLY先通知を消すと、時点再生と再起動は位置欠落を拒否する。"""
    l = make_ledger()
    late(l)
    with closing(sqlite3.connect(l.path)) as con, con:
        con.execute("DELETE FROM ledger_events WHERE kind='NOTICE_CREATED'")
    with pytest.raises(LedgerError):
        l.replay(H2)
    with pytest.raises(MigrationError):
        Ledger(l.path)


@pytest.mark.parametrize('at', [None, AT - US])
def test_q06_cutover_pending_trade_cannot_apply(make_ledger, at):
    """cutover遮断TRADEは遅着通知の補完対象が増えてもAPPLYで迂回できない。"""
    l = make_ledger(initialize=False)
    l.init_snapshot(1000000, [], [], AT, provenance=provenance())
    assert l.report(trade('target', at=at)).pending
    l.create_notice(proposal('target'), at=H6)
    before, seq, rows = l.view(), l.seq(), l.pending_rows()
    with pytest.raises(LedgerError):
        l.resolve_pending('fill1', 'target', H2, 'APPLY')
    assert l.view() == before and l.seq() == seq and l.pending_rows() == rows
    assert l.replay(H2).cash == 1000000 and l.replay(H2).reserved == 0


@pytest.mark.parametrize('history_before_marker', [False, True])
def test_q07_migration_boundary_keeps_partial_apply_history(tmp_path, history_before_marker):
    """同じ部分APPLY履歴が移行の旧規則区間または新規則区間にあっても時点残高は一致。"""
    source = tmp_path / 'original.sqlite'
    with closing(Ledger(source)) as l:
        l.init_snapshot(1000000, [], [], AT)
        if history_before_marker:
            late(l)
    result = apply(source)
    with closing(Ledger(result['output'])) as l:
        if not history_before_marker:
            late(l)
        assert l.replay(H2 - US).cash == 1000000 and l.replay(H2 - US).reserved == 0
        assert l.replay(H2).cash == 960000 and l.replay(H2).reserved == 0
        assert l.replay(H6) == l.view() == l.replay_known(l.seq())
        old = l.replay_known(result['old_last_seq'])
        assert old.cash == (960000 if history_before_marker else 1000000)


def test_q08_discard_id_resent_to_another_notice_stays_discarded(make_ledger):
    """DISCARD済みIDを別通知へ再送しても約定・確定APPLY参照を作らない。"""
    l = make_ledger()
    pending(l)
    l.resolve_pending('c1', None, H2, 'DISCARD')
    l.create_notice(proposal('other'), at=H4)
    before, seq = l.view(), l.seq()
    result = l.import_csv_fills([csv('other', eid='c1', qty=40, at=H1, broker_order_id='broker-c1')])
    assert result.skipped == ['c1'] and l.seq() == seq and l.view() == before
    assert l.replay(H3).cash == 1000000 and l.replay(H3).reserved == 0
    with closing(Ledger(l.path)) as reopened:
        assert reopened.pending_rows() == {} and reopened.view() == before


def test_q09_backdated_correction_before_apply_has_valid_replay(make_ledger):
    """v0.3.8採用契約: 解決前の訂正は拒否し、同時刻なら受理する。"""
    l = make_ledger()
    late(l)
    before, seq = l.view(), l.seq()
    result = l.report(trade('target', eid='early-fix', kind='CORRECTION', qty=40,
                           price=900, replaces_event_id='c1', at=H1 + timedelta(minutes=30)))
    assert not result.applied and '訂正の業務時刻' in result.error
    assert l.view() == before and l.seq() == seq and l.replay() == before
    assert l.replay(H1 + timedelta(minutes=30)).cash == 1000000
    assert l.replay(H2).cash == 960000
    assert l.report(trade('target', eid='equal-fix', kind='CORRECTION', qty=40,
                          price=900, replaces_event_id='c1', at=H2)).applied
    assert l.cash() == 964000 and l.replay(H2).cash == 964000

def test_q10_notice_state_before_resolution_has_valid_replay(make_ledger):
    """遅着通知への状態変更がAPPLYより前なら、その中間時点も例外なく再生できるべき。"""
    l = make_ledger()
    late(l)
    l.set_notice_state('target', 'APPROVED', H1 + timedelta(minutes=30))
    l.set_notice_state('target', 'SENT', H1 + timedelta(minutes=40))
    l.expire_notices(H1 + timedelta(minutes=50))
    before = l.replay(H1 + timedelta(minutes=45))
    assert before.cash == 1000000 and before.positions == {} and before.reserved == 0
    assert l.replay(H2).cash == 960000 and l.replay(H2).reserved == 0
    assert l.replay(H6) == l.view()
