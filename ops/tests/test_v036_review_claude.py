"""2026-09-09 第10回：ops v0.3.6（L02: 開始残高前の時点再生は空、L03: 解決時刻は保留行の業務時刻以後）の反例
（Claude, Cowork セッション）。skip/xfail/条件緩和はしない。"""
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict
from datetime import timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'build-codex'), str(ROOT / 'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT  # noqa: E402,F401
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError  # noqa: E402
from aitrader_ops.models import PositionIn  # noqa: E402

H1 = AT + timedelta(hours=1)
H2 = AT + timedelta(hours=2)
D1 = AT + timedelta(days=1)


def _rows(path, sql):
    with closing(sqlite3.connect(path)) as con, con:
        return con.execute(sql).fetchall()


def _prov(**c):
    p = dict(method='RECONCILED_OPENING', source_path='old.sqlite', source_sha256='a' * 64, source_last_seq=3,
             reconciliation_id='rec-1', reconciliation_record_path='rec-1.md', reconciliation_record_sha256='b' * 64,
             actor='norimitsu', reconciled_at=D1, reason='照合済み開始残高', cutover_at=D1, previous_policy_version='p1')
    p.update(c)
    return p


def _empty(v):
    return v.cash == 0 and v.positions == {} and v.reserved == 0 and v.available == 0 and v.reserved_positions == {}


# ---- L02: 開始残高前の時点再生は空 -------------------------------------------------------------------

def test_m01_replay_before_snapshot_is_empty_with_late_pending_notices_and_fills(tmp_path):
    """provenance 付き新台帳: SNAPSHOT 前の時刻に、後着した cutover 前保留（TRADE/CSV/時刻なし）・遅着通知・訂正があっても空。
    SNAPSHOT ちょうど・以後は従来どおり。"""
    path = tmp_path / 'n.sqlite'
    l = Ledger(path)
    l.init_snapshot(900000, [PositionIn('6857', 100, 1000.)], [], D1, provenance=_prov())
    l.create_notice(proposal('buy2', code='6857'), at=D1 + timedelta(hours=1))
    assert l.report(trade(pid='buy2', eid='t-old', at=H1)).pending
    assert l.import_csv_fills([csv('buy2', eid='c-old', at=H2), csv(None, eid='c-none', at=None, broker_order_id='ob3')]).pending == ['c-old', 'c-none']
    assert l.report(trade(pid='buy2', eid='f1', at=D1 + timedelta(hours=2), broker_order_id='o2')).applied
    for at in (AT - timedelta(days=30), H1, H2, D1 - timedelta(microseconds=1)):
        assert _empty(l.replay(at)), f'SNAPSHOT 前 {at} が空でない'
    edge = l.replay(D1)
    assert edge.cash == 900000 and edge.positions['6857'].qty == 100 and edge.reserved == 0
    assert l.replay(D1 + timedelta(hours=1)).reserved == 100200 and l.replay(D1 + timedelta(hours=3)).cash == 800000
    v, seq = l.view(), l.seq()
    l.close()
    l2 = Ledger(path)
    try:
        assert _empty(l2.replay(H1)) and asdict(l2.view()) == asdict(v) and asdict(l2.replay_known(seq)) == asdict(v)
    finally:
        l2.close()


def test_m02_plain_ledger_replay_before_snapshot_is_empty_too(make_ledger):
    """provenance なしの通常台帳でも、SNAPSHOT（AT−1日）より前の時点は空。"""
    l = make_ledger()
    l.create_notice(proposal(), at=AT)
    assert l.report(trade()).applied
    assert _empty(l.replay(AT - timedelta(days=2))) and _empty(l.replay(AT - timedelta(days=1, microseconds=1)))
    assert l.replay(AT - timedelta(days=1)).cash == 1000000 and l.replay(H1).cash == 900000


def test_m03_replay_before_snapshot_in_other_offset_and_migrated_ledger(tmp_path):
    """SNAPSHOT 時刻の別オフセット表記でも境界判定は同じ瞬間で行われ、移行済み台帳でもマーカー検証は先に効く。"""
    path = tmp_path / 'p.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [], [], AT)
    try:
        utc = AT.astimezone(timezone.utc)
        assert l.replay(utc).cash == 1000000 and _empty(l.replay(utc - timedelta(microseconds=1)))
    finally:
        l.close()
    with closing(sqlite3.connect(path)) as con, con:                      # 不正マーカーは空判定より先に止まる
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    ['pu', 'POLICY_UPGRADE', AT.isoformat(), json.dumps(dict(schema_version=3, at=AT.isoformat(), legacy_last_seq=99)), AT.isoformat()])
    with pytest.raises(MigrationError):
        Ledger(path)


# ---- L03: 解決の業務時刻は保留行の業務時刻以後 ---------------------------------------------------------

def test_m04_resolution_earlier_than_pending_business_time_is_rejected_for_apply_and_discard(make_ledger):
    l = make_ledger()
    l.create_notice(proposal(), at=AT)
    assert l.import_csv_fills([csv('unknown', eid='c1', at=H2)]).pending == ['c1']
    events = len(_rows(l.path, 'SELECT seq FROM ledger_events'))
    for action, pid in (('DISCARD', None), ('APPLY', 'buy1')):
        with pytest.raises(LedgerError):
            l.resolve_pending('c1', pid, H2 - timedelta(seconds=1), action, actor='norimitsu', reason='早すぎる')
    assert 'c1' in l.pending_rows() and l.cash() == 1000000
    assert len(_rows(l.path, 'SELECT seq FROM ledger_events')) == events          # 拒否は履歴に残らない
    l.resolve_pending('c1', 'buy1', H2, 'APPLY', actor='norimitsu', reason='同時刻は可')    # ちょうどは受理
    assert l.cash() == 900000 and l.pending_rows() == {}
    assert l.replay(H2 - timedelta(minutes=1)).cash == 1000000 and l.replay(H2).cash == 900000


def test_m05_resolution_time_constraint_uses_the_instant_not_the_text_and_skips_timeless_rows(make_ledger):
    l = make_ledger()
    l.create_notice(proposal(), at=AT)
    assert l.import_csv_fills([csv('unknown', eid='c1', at=H2), csv('unknown', eid='c2', at=None, broker_order_id='ob2')]).pending == ['c1', 'c2']
    l.resolve_pending('c1', None, H2.astimezone(timezone.utc), 'DISCARD', actor='n', reason='別オフセットの同時刻')
    l.resolve_pending('c2', None, AT - timedelta(days=1), 'DISCARD', actor='n', reason='時刻なし行は制約なし')
    assert l.pending_rows() == {}
    assert l.replay(H1).cash == 1000000 and l.replay(D1).cash == 1000000


def test_m06_history_with_reversed_resolution_written_before_v036_stops_with_position(tmp_path):
    """互換の記録: v0.3.6 より前に受理された『保留より前の解決』を含む履歴は、再起動時に MigrationError(seq, PENDING_RESOLVED) で止まる。
    migrate --check で位置が分かる。実台帳は未作成のため影響なし。"""
    path = tmp_path / 'old.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    assert l.import_csv_fills([csv('unknown', eid='c1', at=H2)]).pending == ['c1']
    l.close()
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    ['res', 'PENDING_RESOLVED', AT.isoformat(),
                     json.dumps(dict(source_event_id='c1', proposal_id=None, action='DISCARD', at=AT.isoformat(), actor='x', reason='旧版で受理')),
                     AT.isoformat()])
    with pytest.raises(MigrationError) as e:
        Ledger(path)
    assert e.value.kind == 'PENDING_RESOLVED' and e.value.seq == 3
    from aitrader_ops.migrate import check
    assert check(path)['violation'] == dict(seq=3, kind='PENDING_RESOLVED', reason=e.value.reason)


def test_m07_cutover_pending_trade_resolution_keeps_the_same_constraint(tmp_path):
    l = Ledger(tmp_path / 'n.sqlite')
    l.init_snapshot(900000, [PositionIn('6857', 100, 1000.)], [], D1, provenance=_prov())
    try:
        assert l.report(trade(pid='ghost', eid='t1', at=H2)).pending
        with pytest.raises(LedgerError):
            l.resolve_pending('t1', None, H1, 'DISCARD', actor='n', reason='保留より前')
        l.resolve_pending('t1', None, D1 + timedelta(hours=1), 'DISCARD', actor='n', reason='ok')
        assert l.pending_rows() == {} and _empty(l.replay(H2)) and l.replay(D1).cash == 900000
    finally:
        l.close()
