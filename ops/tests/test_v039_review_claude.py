"""2026-09-09 第13回：ops v0.3.9（T06: EXPIRE の不明参照は契約どおりのエラー、T07: 時刻なし訂正は対象の有効時刻を継承）の独立試験
（Claude, Cowork セッション）。skip/xfail/条件緩和はしない。SELL 取得原価・部分合算・取消後の訂正・移行前後・時刻なし対象・再起動・
Q09/Q10・cutover 遮断の維持も確認する。"""
import json
import sqlite3
import sys
from contextlib import closing
from datetime import timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT  # noqa: E402,F401
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError  # noqa: E402
from aitrader_ops.migrate import check  # noqa: E402
from aitrader_ops.models import PositionIn  # noqa: E402

H1, H2, H3, H4, H5, H6 = [AT + timedelta(hours=h) for h in range(1, 7)]
US = timedelta(microseconds=1)
M = timedelta(minutes=1)


def _audit(path):
    with closing(sqlite3.connect(path)) as con, con:
        return con.execute('SELECT outcome, reason_code FROM ingest_attempts ORDER BY rowid DESC LIMIT 1').fetchone()


def _fix(l, target, eid, at, price=900., qty=40, pid='target'):
    return l.report(trade(pid, eid=eid, kind='CORRECTION', qty=qty, price=price, replaces_event_id=target, at=at))


def _late(l, qty=40):
    """09:00 保留 → 14:00 作成の通知 target → 10:00 に APPLY（有効時刻 10:00）"""
    assert l.import_csv_fills([csv('unknown', eid='c1', qty=qty, at=H1, broker_order_id='broker-c1')]).pending == ['c1']
    l.create_notice(proposal('target'), at=H6)
    l.resolve_pending('c1', 'target', H2, 'APPLY')
    assert l.cash() == 1000000 - qty * 1000


# ---- T06: EXPIRE の不明参照 -------------------------------------------------------------------------

def test_s01_expire_unknown_reference_is_a_contract_error_everywhere(make_ledger):
    """通知の作成・状態変更を消した故障注入: replay は LedgerError、再起動と migrate.check は EXPIRE の位置付き MigrationError。KeyError は漏れない。"""
    l = make_ledger()
    l.create_notice(proposal('target'), at=H6)
    l.set_notice_state('target', 'APPROVED', H4)
    l.set_notice_state('target', 'SENT', H4)
    assert l.expire_notices(H1) == ['target']
    with closing(sqlite3.connect(l.path)) as con, con:
        con.execute("DELETE FROM ledger_events WHERE kind IN ('NOTICE_CREATED','NOTICE_STATE')")
        expire_seq = con.execute("SELECT seq FROM ledger_events WHERE kind='EXPIRE'").fetchone()[0]
    with pytest.raises(LedgerError, match='通知 target がありません'):
        l.replay(H1)
    with pytest.raises(LedgerError):
        l.replay()
    with pytest.raises(MigrationError) as e:
        Ledger(l.path)
    assert e.value.kind == 'EXPIRE' and e.value.seq == expire_seq
    with pytest.raises(MigrationError) as e2:
        check(l.path)
    assert e2.value.kind == 'EXPIRE' and e2.value.seq == expire_seq


def test_s02_expire_with_known_ids_still_works_and_unknown_in_the_list_fails(make_ledger):
    """正常な EXPIRE（複数 ID）は従来どおり。列挙の一部だけ不明なら、その ID を名指しして失敗する。"""
    l = make_ledger()
    for pid, code in (('a', '6857'), ('b', '7203')):
        l.create_notice(proposal(pid, code=code), at=AT)
        l.set_notice_state(pid, 'APPROVED', AT)
        l.set_notice_state(pid, 'SENT', AT)
    assert set(l.expire_notices(H1)) == {'a', 'b'}
    assert l.replay(H1) == l.view() and l.replay(H1).reserved == 200400     # EXPIRED は予約を解放しない（実装どおり）
    with closing(sqlite3.connect(l.path)) as con, con:
        con.execute("DELETE FROM ledger_events WHERE kind IN ('NOTICE_CREATED','NOTICE_STATE') AND payload LIKE '%\"b\"%'")
    with pytest.raises(LedgerError, match='通知 b がありません'):
        l.replay(H1)


# ---- T07: 時刻なし訂正は対象の有効時刻を継承 ------------------------------------------------------------

def test_s03_timeless_correction_inherits_pending_apply_time(make_ledger):
    """保留 APPLY（有効 10:00）の時刻なし訂正は 10:00 に効く: 09:00・10:00 直前は未反映、10:00 で訂正後の残高、再起動一致。"""
    l = make_ledger()
    _late(l)
    assert _fix(l, 'c1', 'timeless', None).applied and l.cash() == 964000
    for at in (H1, H1 + 30 * M, H2 - US):
        v = l.replay(at)
        assert v.cash == 1000000 and v.positions == {} and v.reserved == 0, at   # 例外なし・先取りなし・予約なし
    assert l.replay(H2).cash == 964000 and l.replay(H2).positions['6857'].qty == 40 and l.replay(H2).reserved == 0
    assert l.replay(H6) == l.view() and l.replay() == l.view()
    with closing(Ledger(l.path)) as reopened:
        assert reopened.replay(H1).cash == 1000000 and reopened.replay(H2).cash == 964000
        assert reopened.replay_known(reopened.seq()) == l.view()


def test_s04_recorrection_of_timeless_correction_uses_inherited_time(make_ledger):
    """時刻なし訂正（有効 10:00 を継承）を 09:00 で再訂正すると拒否（残高・seq 不変・監査行）。10:00 ちょうど・以後は受理され各時点が再生できる。"""
    l = make_ledger()
    _late(l)
    assert _fix(l, 'c1', 'timeless', None).applied
    before, seq = l.view(), l.seq()
    r = _fix(l, 'timeless', 'early', H1, 950.)
    assert not r.applied and '訂正の業務時刻' in r.error and l.view() == before and l.seq() == seq
    assert _audit(l.path) == ('REJECTED', 'VALIDATION')
    assert l.replay(H1).cash == 1000000                                              # T07 の形でも 09:00 の再生が成立
    assert _fix(l, 'timeless', 'ontime', H2.astimezone(timezone.utc), 950.).applied and l.cash() == 962000
    assert l.replay(H2 - US).cash == 1000000 and l.replay(H2).cash == 962000 and l.replay(H6) == l.view()
    assert _fix(l, 'ontime', 'later', H3, 940.).applied and l.replay(H3 - US).cash == 962000 and l.replay(H3).cash == 962400


def test_s05_timeless_chain_and_direct_fill(make_ledger):
    """通常約定（10:00）の時刻なし訂正、その時刻なし再訂正も 10:00 を継承。日時付き訂正の基準も 10:00。"""
    l = make_ledger()
    l.create_notice(proposal('buy1'), at=AT)
    assert l.report(trade('buy1', eid='f1', at=H2)).applied
    assert _fix(l, 'f1', 't1', None, 900., qty=100, pid='buy1').applied
    assert _fix(l, 't1', 't2', None, 950., qty=100, pid='buy1').applied and l.cash() == 905000
    assert l.replay(H2 - US).cash == 1000000 and l.replay(H2).cash == 905000
    assert not _fix(l, 't2', 'early', H2 - US, 940., qty=100, pid='buy1').applied
    assert _fix(l, 't2', 'ok', H2, 940., qty=100, pid='buy1').applied and l.replay(H2).cash == 906000


def test_s06_timeless_target_keeps_no_time(make_ledger):
    """時刻なし約定を時刻なし訂正した場合は継承する時刻がなく、従来どおり常に時点内（日時付き訂正にも制約なし）。"""
    l = make_ledger()
    l.create_notice(proposal('target'), at=AT)
    assert l.report(trade('target', eid='f', qty=40, at=None)).applied
    assert _fix(l, 'f', 't', None).applied and l.cash() == 964000
    assert l.replay(H1).cash == 964000 and l.replay() == l.view()
    assert _fix(l, 't', 'dated', H1, 950.).applied and l.replay(H1).cash == 962000


def test_s07_history_before_v039_with_early_recorrection_stops_with_position(tmp_path):
    """互換: 旧版で受理された『時刻なし訂正 → それより前の日時付き再訂正』は再起動で MigrationError(seq, TRADE)。check の violation に位置。"""
    path = tmp_path / 'old.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    l.create_notice(proposal('buy1'), at=AT)
    assert l.report(trade('buy1', eid='f1', at=H2)).applied
    assert _fix(l, 'f1', 't1', None, 900., qty=100, pid='buy1').applied
    l.close()
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    ['early', 'TRADE', H1.isoformat(),
                     json.dumps(dict(event_id='early', proposal_id='buy1', kind='CORRECTION', qty=100, price=950., fee=0.,
                                     at=H1.isoformat(), source='line', broker_order_id='order1', replaces_event_id='t1')),
                     H1.isoformat()])
        seq = con.execute("SELECT max(seq) FROM ledger_events").fetchone()[0]
    with pytest.raises(MigrationError) as e:
        Ledger(path)
    assert e.value.kind == 'TRADE' and e.value.seq == seq
    assert check(path)['violation'] == dict(seq=seq, kind='TRADE', reason=e.value.reason)


# ---- 維持: SELL 取得原価・部分合算・取消後・cutover・Q09/Q10 -----------------------------------------------

def test_s08_sell_price_only_timeless_correction_keeps_acquisition_cost(make_ledger):
    l = make_ledger(positions=[PositionIn(code='6857', qty=100, avg_price=600)])
    l.create_notice(proposal('target', side='SELL'), at=AT)
    assert l.report(trade('target', eid='sell', qty=40, price=1000., at=H2)).applied
    assert _fix(l, 'sell', 't', None, 900.).applied and _fix(l, 't', 'd', H3, 950.).applied
    assert l.cash() == 1038000 and l.positions()['6857'].qty == 60 and l.positions()['6857'].avg_price == 600
    assert l.replay(H2 - US).cash == 1000000 and l.replay(H2).cash == 1036000 and l.replay(H3).cash == 1038000


def test_s09_partial_sum_then_timeless_correction_and_cancel(make_ledger):
    """40＋20 株の合算後に 1 件目を時刻なし訂正（有効 10:00 を継承）、その後の取消で残 40 株の予約が消える。各時点の再生が成立。"""
    l = make_ledger()
    _late(l)
    assert l.import_csv_fills([csv('unknown', eid='c2', qty=20, at=H1 + M, broker_order_id='broker-c2')]).pending == ['c2']
    l.resolve_pending('c2', 'target', H3, 'APPLY')
    assert _fix(l, 'c1', 't', None).applied and l.cash() == 944000
    assert l.replay(H2 - US).cash == 1000000 and l.replay(H2).cash == 964000 and l.replay(H3).cash == 944000
    assert l.replay(H6).reserved == 40080
    assert l.report(trade('target', eid='cancel', kind='CANCELLED', qty=40, at=H4)).applied
    assert l.replay(H6).reserved == 0 and l.replay(H6) == l.view()
    assert _fix(l, 't', 'after-cancel', H5, 950.).applied and l.cash() == 942000   # 取消後も約定済み分の訂正は可
    assert l.replay(H5 - US).cash == 944000 and l.replay(H5).cash == 942000


@pytest.mark.parametrize('at', [None, AT - US])
def test_s10_cutover_blocked_trade_has_no_correction_path(make_ledger, at):
    from test_v034_contracts import provenance
    l = make_ledger(initialize=False)
    l.init_snapshot(1000000, [], [], AT, provenance=provenance())
    l.create_notice(proposal('target'), at=H6)
    assert l.report(trade('target', eid='blocked', qty=40, at=at)).pending
    before, seq = l.view(), l.seq()
    with pytest.raises(LedgerError, match='cutover'):
        l.resolve_pending('blocked', 'target', H2, 'APPLY')
    dated = _fix(l, 'blocked', 'fix2', H3)
    assert not dated.applied and not dated.pending and l.seq() == seq           # 日時付き訂正: 対象が未適用なので拒否
    timeless = _fix(l, 'blocked', 'fix', None)
    assert not timeless.applied and timeless.pending                             # 時刻なし訂正: cutover 境界を照合できず保留（従来どおり）
    assert l.view() == before and set(l.pending_rows()) == {'blocked', 'fix'}


def test_s11_q09_q10_preserved(make_ledger):
    l = make_ledger()
    _late(l)
    assert not _fix(l, 'c1', 'early', H2 - 30 * M).applied and l.cash() == 960000      # Q09
    l.set_notice_state('target', 'APPROVED', H1 + 30 * M)                                 # Q10
    assert l.replay(H1 + 45 * M).cash == 1000000 and l.replay(H1 + 45 * M).reserved == 0
    assert l.replay(H6) == l.view()
