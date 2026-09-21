"""2026-09-09 第11回：ops v0.3.7（O01: 遅着通知を参照する保留 APPLY の時点再生）の独立試験 P01〜P07（Claude, Cowork セッション）。

注: 並行して動いていた 2 つの Claude セッションが同名ファイルを書いたため、ops/Claude対応結果.md 第11回・ops/README.md の
記述（P01〜P07、9 ケース）に合わせて本ファイルを再構成した。skip/xfail/条件緩和はしない。
"""
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
from aitrader_ops.ledger import Ledger, LedgerError  # noqa: E402
from aitrader_ops.models import PositionIn  # noqa: E402

H1, H2, H3, H4 = (AT + timedelta(hours=h) for h in (1, 2, 3, 4))   # 保留 / 解決 / 通知作成 / その後
D1 = AT + timedelta(days=1)
US = timedelta(microseconds=1)


def _rows(path, sql):
    with closing(sqlite3.connect(path)) as con, con:
        return con.execute(sql).fetchall()


def _same(a, b):
    return asdict(a) == asdict(b)


def _late_apply(l, notice_at=H3, resolved_at=H2):
    """09:00 保留（H1）→ 通知 target 作成（notice_at）→ APPLY（resolved_at）。"""
    assert l.import_csv_fills([csv('unknown', eid='c1', at=H1)]).pending == ['c1']
    l.create_notice(proposal('target'), at=notice_at)
    l.resolve_pending('c1', 'target', resolved_at, 'APPLY', actor='norimitsu', reason='遅着通知へ確定')
    assert l.cash() == 900000 and l.positions()['6857'].qty == 100 and l.pending_rows() == {}


def test_p01_replay_before_at_and_after_resolution(make_ledger):
    """解決前（保留時刻の前後・解決 1µs 前）は 1000000・予約 0、解決時刻ちょうど・以後は 900000・100 株・予約 0、
    通知作成時刻以後は現在残高と一致（遅着通知の予約は解決で消化済みなので現れない）。"""
    l = make_ledger()
    _late_apply(l)
    for at in (AT, H1 - US, H1, H2 - US):
        v = l.replay(at)
        assert v.cash == 1000000 and v.positions == {} and v.reserved == 0 and v.reserved_positions == {}, at
    for at in (H2, H2 + US, H3 - US, H3, H4):
        v = l.replay(at)
        assert v.cash == 900000 and v.positions['6857'].qty == 100 and v.reserved == 0, at
    assert _same(l.replay(H3), l.view()) and _same(l.replay(), l.view())


@pytest.mark.parametrize('tz', [timezone.utc, timezone(timedelta(hours=-5)), timezone(timedelta(hours=9))])
def test_p02_same_instant_in_another_offset(make_ledger, tz):
    """通知作成時刻・解決時刻・再生時刻を別オフセットで指定しても同じ瞬間として扱う。"""
    l = make_ledger()
    _late_apply(l, notice_at=H3.astimezone(tz), resolved_at=H2.astimezone(tz))
    assert l.replay((H2 - US).astimezone(tz)).cash == 1000000
    assert l.replay(H2.astimezone(tz)).cash == 900000 and l.replay(H2.astimezone(tz)).reserved == 0
    assert _same(l.replay(H3.astimezone(tz)), l.view())


def test_p03_notice_created_at_the_resolution_instant(make_ledger):
    l = make_ledger()
    _late_apply(l, notice_at=H2, resolved_at=H2)
    assert l.replay(H2 - US).cash == 1000000 and l.replay(H2 - US).reserved == 0
    v = l.replay(H2)
    assert v.cash == 900000 and v.positions['6857'].qty == 100 and v.reserved == 0 and _same(v, l.view())


def test_p04_discard_does_not_bring_the_notice_in(make_ledger):
    """DISCARD は通知を参照しないので識別を持ち込まない。通知作成時刻以後はその通知の予約（100,200 円）が通常どおり現れる。"""
    l = make_ledger()
    assert l.import_csv_fills([csv('unknown', eid='c1', at=H1)]).pending == ['c1']
    l.create_notice(proposal('target'), at=H3)
    l.resolve_pending('c1', None, H2, 'DISCARD', actor='norimitsu', reason='旧台帳に含まれる')
    for at in (H1, H2, H3 - US):
        v = l.replay(at)
        assert v.cash == 1000000 and v.reserved == 0 and v.reserved_positions == {}, at
    v = l.replay(H3)
    assert v.cash == 1000000 and v.reserved == 100200 and v.reserved_positions == {'6857': 100200}
    assert _same(v, l.view()) and l.pending_rows() == {}


def test_p05_restart_and_replay_known_per_seq(tmp_path):
    """再起動後の一致と記録順再生: CSV（保留）→ 通知 → 解決 の各 seq で残高・予約が期待どおり。"""
    path = tmp_path / 'late.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    assert l.import_csv_fills([csv('unknown', eid='c1', at=H1)]).pending == ['c1']
    s_csv = l.seq()
    l.create_notice(proposal('target'), at=H3)
    s_notice = l.seq()
    l.resolve_pending('c1', 'target', H2, 'APPLY', actor='norimitsu', reason='遅着通知へ確定')
    s_apply = l.seq()
    v = l.view()
    l.close()
    l2 = Ledger(path)
    try:
        assert _same(l2.view(), v) and _same(l2.replay(), v) and _same(l2.replay_known(s_apply), v)
        assert l2.replay_known(s_csv).cash == 1000000 and l2.replay_known(s_csv).reserved == 0
        assert l2.replay_known(s_notice).cash == 1000000 and l2.replay_known(s_notice).reserved == 100200
        assert l2.replay_known(s_apply).cash == 900000 and l2.replay_known(s_apply).reserved == 0
        assert l2.replay(H2).cash == 900000 and l2.replay(H2 - US).cash == 1000000
    finally:
        l2.close()


def test_p06_replay_is_read_only(make_ledger):
    """時点再生は履歴・現在残高・seq を変えない（context_only の補完が永続化されない）。"""
    l = make_ledger()
    _late_apply(l)
    events = _rows(l.path, 'SELECT seq, event_id, kind, payload FROM ledger_events ORDER BY seq')
    audits = _rows(l.path, 'SELECT * FROM ingest_attempts ORDER BY rowid')
    seq, v = l.seq(), l.view()
    for at in (H1, H2 - US, H2, H3, None):
        l.replay(at)
        l.replay_known(seq)
    assert _rows(l.path, 'SELECT seq, event_id, kind, payload FROM ledger_events ORDER BY seq') == events
    assert _rows(l.path, 'SELECT * FROM ingest_attempts ORDER BY rowid') == audits
    assert l.seq() == seq and _same(l.view(), v)
    assert _rows(l.path, "SELECT COUNT(*) FROM ledger_events WHERE kind='NOTICE_CREATED'")[0][0] == 1


def test_p07_l02_and_l03_are_preserved(tmp_path):
    """L02: 開始残高前は空（後着保留があっても）。L03: 早い解決は拒否され seq・残高・保留は不変、監査行だけ残る。"""
    path = tmp_path / 'keep.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [], [], AT)
    try:
        assert l.import_csv_fills([csv('unknown', eid='c1', at=H2)]).pending == ['c1']
        l.create_notice(proposal('target'), at=H3)
        assert l.replay(AT - US).cash == 0 and l.replay(AT - US).positions == {} and l.replay(AT - US).reserved == 0
        seq, cash = l.seq(), l.cash()
        with pytest.raises(LedgerError):
            l.resolve_pending('c1', 'target', H1, 'APPLY', actor='norimitsu', reason='早すぎる')
        assert l.seq() == seq and l.cash() == cash and 'c1' in l.pending_rows()
        assert _rows(path, "SELECT outcome, reason_code FROM ingest_attempts WHERE source_event_id='c1' ORDER BY rowid")[-1] == ('REJECTED', 'VALIDATION')
        l.resolve_pending('c1', 'target', H2, 'APPLY', actor='norimitsu', reason='同時刻')
        assert l.replay(H2).cash == 900000 and l.replay(H2 - US).cash == 1000000 and l.replay(AT - US).cash == 0
    finally:
        l.close()
