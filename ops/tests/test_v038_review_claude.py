"""2026-09-09 第12回：ops v0.3.8（Q09: 訂正の業務時刻は訂正対象の約定の有効時刻以後、Q10: 通知状態・期限切れ参照の識別補完）の独立試験
（Claude, Cowork セッション）。skip/xfail/条件緩和はしない。L02/L03/O01、複数保留合算・残数量超過拒否、cutover 遮断、訂正後の予約非復活は維持。"""
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

H1, H2, H3, H4, H5, H6 = [AT + timedelta(hours=h) for h in range(1, 7)]
US = timedelta(microseconds=1)
M = timedelta(minutes=1)


def _audit(path):
    with closing(sqlite3.connect(path)) as con, con:
        return con.execute('SELECT outcome, reason_code FROM ingest_attempts ORDER BY rowid DESC LIMIT 1').fetchone()


def _late(l, qty=40):
    """09:00 保留 40 株 → 14:00 作成の通知 target → 10:00 に APPLY（Codex Q 系列と同じ形）"""
    assert l.import_csv_fills([csv('unknown', eid='c1', qty=qty, at=H1, broker_order_id='broker-c1')]).pending == ['c1']
    l.create_notice(proposal('target'), at=H6)
    l.resolve_pending('c1', 'target', H2, 'APPLY')
    assert l.cash() == 960000 and l.positions()['6857'].qty == 40


# ---- Q09: 訂正の業務時刻は対象の約定が残高に反映された時刻以後 ------------------------------------------

def test_r01_correction_before_pending_apply_time_is_rejected_and_audited(make_ledger):
    """保留 APPLY（有効時刻 10:00）の訂正を 09:30 で報告すると拒否。残高・履歴・seq は不変、監査行は残る。"""
    l = make_ledger()
    _late(l)
    before, seq = l.view(), l.seq()
    r = l.report(trade('target', eid='fix', kind='CORRECTION', qty=40, price=900., replaces_event_id='c1', at=H2 - 30 * M))
    assert not r.applied and r.error and '訂正の業務時刻' in r.error
    assert l.view() == before and l.seq() == seq and l.cash() == 960000
    assert _audit(l.path) == ('REJECTED', 'VALIDATION')
    assert l.replay(H2 - 30 * M).cash == 1000000 and l.replay(H2).cash == 960000   # 再生も従来どおり


@pytest.mark.parametrize('offset', [timezone(timedelta(hours=9)), timezone.utc, timezone(timedelta(hours=-5))])
def test_r02_correction_at_apply_time_or_later_is_accepted_in_any_offset(make_ledger, offset):
    """有効時刻ちょうど（別オフセット表記でも同じ瞬間）と以後の訂正は受理。各時点の再生が成立し、予約は復活しない。"""
    l = make_ledger()
    _late(l)
    r = l.report(trade('target', eid='fix', kind='CORRECTION', qty=40, price=900., replaces_event_id='c1',
                       at=H2.astimezone(offset)))
    assert r.applied and l.cash() == 964000
    assert l.replay(H2 - US).cash == 1000000 and l.replay(H2 - US).reserved == 0
    at = l.replay(H2)
    assert at.cash == 964000 and at.positions['6857'].qty == 40 and at.reserved == 0
    assert l.replay(H6) == l.view() and l.replay(H6).reserved == 60120              # 作成時刻以後は未約定 60 株の予約が通常どおり現れる（Q04 と同じ）
    r2 = l.report(trade('target', eid='fix2', kind='CORRECTION', qty=40, price=950., replaces_event_id='fix', at=H3))
    assert r2.applied and l.cash() == 962000
    r3 = l.report(trade('target', eid='fix3', kind='CORRECTION', qty=40, price=940., replaces_event_id='fix2', at=H3 - US))
    assert not r3.applied and '訂正の業務時刻' in r3.error                         # 訂正の訂正も、その訂正の時刻が有効時刻
    assert l.replay(H3 - US).cash == 964000 and l.replay(H3).cash == 962000


def test_r03_direct_fill_backdated_correction_is_rejected_too(make_ledger):
    """通常の約定（有効時刻＝約定時刻 10:00）でも 09:30 の訂正は拒否。10:00 ちょうどは受理。"""
    l = make_ledger()
    l.create_notice(proposal('buy1'), at=AT)
    assert l.report(trade('buy1', eid='f1', at=H2)).applied
    r = l.report(trade('buy1', eid='fix', kind='CORRECTION', price=900., replaces_event_id='f1', at=H2 - 30 * M))
    assert not r.applied and '訂正の業務時刻' in r.error and l.cash() == 900000
    assert l.report(trade('buy1', eid='fix', kind='CORRECTION', price=900., replaces_event_id='f1', at=H2)).applied
    assert l.cash() == 910000 and l.replay(H2 - US).cash == 1000000 and l.replay(H2).cash == 910000


def test_r04_timeless_correction_has_no_constraint(make_ledger):
    """時刻なし訂正には制約なし（時刻なし保留行の L03 と同じ扱い）。現在残高には反映され、時刻付き再生では最後に効く。"""
    l = make_ledger()
    _late(l)
    r = l.report(trade('target', eid='fix', kind='CORRECTION', qty=40, price=900., replaces_event_id='c1', at=None))
    assert r.applied and l.cash() == 964000
    assert l.replay() == l.view()


def test_r05_history_written_before_v038_with_backdated_correction_stops_with_position(tmp_path):
    """互換: v0.3.8 より前に受理された前倒し訂正を含む履歴は再起動で MigrationError(seq, TRADE)。migrate --check に位置が出る。"""
    path = tmp_path / 'old.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    l.create_notice(proposal('buy1'), at=AT)
    assert l.report(trade('buy1', eid='f1', at=H2)).applied
    l.close()
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    ['fix', 'TRADE', (H2 - 30 * M).isoformat(),
                     json.dumps(dict(event_id='fix', proposal_id='buy1', kind='CORRECTION', qty=100, price=900., fee=0.,
                                     at=(H2 - 30 * M).isoformat(), source='line', broker_order_id='order1', replaces_event_id='f1')),
                     (H2 - 30 * M).isoformat()])
    with pytest.raises(MigrationError) as e:
        Ledger(path)
    assert e.value.kind == 'TRADE' and e.value.seq == 4
    from aitrader_ops.migrate import check
    assert check(path)['violation'] == dict(seq=4, kind='TRADE', reason=e.value.reason)


# ---- Q10: 通知状態・期限切れの参照は識別だけ補完 ------------------------------------------------------------

def test_r06_notice_state_before_creation_replays_without_reservation(make_ledger):
    """遅着通知（14:00 作成）への 09:30 APPROVED / 09:40 SENT / 09:50 EXPIRE は、各中間時点で例外なく再生され、予約 0・残高非先取り。"""
    l = make_ledger()
    _late(l)
    l.set_notice_state('target', 'APPROVED', H1 + 30 * M)
    l.set_notice_state('target', 'SENT', H1 + 40 * M)
    assert l.expire_notices(H1 + 50 * M) == ['target']
    for at in (H1 + 30 * M - US, H1 + 30 * M, H1 + 45 * M, H1 + 50 * M, H2 - US):
        v = l.replay(at)
        assert v.cash == 1000000 and v.positions == {} and v.reserved == 0 and v.reserved_positions == {}, at
    for at in (H2, H3, H6 - US):
        v = l.replay(at)
        assert v.cash == 960000 and v.positions['6857'].qty == 40 and v.reserved == 0, at
    assert l.replay(H6) == l.view() and l.replay(H6).reserved == 60120   # 作成時刻以後は通知が時点内。EXPIRED は予約を解放しないので残 60 株の予約 60120 が残る（Q04 と同じ）
    with closing(Ledger(l.path)) as reopened:
        assert reopened.replay(H1 + 45 * M).cash == 1000000 and reopened.replay(H6) == l.view()
        assert reopened.replay_known(reopened.seq()) == l.view()


def test_r07_expire_with_multiple_ids_replays_without_reservation(make_ledger):
    """EXPIRE が複数 ID（target・other の両方が期限切れ集合に入る）を持つとき、解決前の時点では両方とも識別だけが補完され、
    予約も残高も持ち込まない。列挙外 ID の非補完は Codex T05 が直接観測している（本試験の other は列挙内）。"""
    l = make_ledger()
    _late(l)
    l.create_notice(proposal('other', code='7203'), at=H6 + M)     # target と同様に期限切れ集合へ入る遅着通知
    for pid in ('target', 'other'):
        l.set_notice_state(pid, 'APPROVED', H1 + 30 * M)
        l.set_notice_state(pid, 'SENT', H1 + 31 * M)
    expired = l.expire_notices(H1 + 50 * M)
    assert set(expired) == {'target', 'other'}
    v = l.replay(H1 + 50 * M)
    assert v.cash == 1000000 and v.reserved == 0 and v.reserved_positions == {}
    # 再生は読取専用で、現在残高・seq は変わらない
    before, seq = l.view(), l.seq()
    l.replay(H1 + 45 * M); l.replay(H6 + M)
    assert l.view() == before and l.seq() == seq


def test_r08_missing_notice_for_state_reference_is_still_rejected(make_ledger):
    """故障注入で通知作成を消すと、状態参照の補完は「不明参照を無視」にならず LedgerError / 再起動は MigrationError。"""
    l = make_ledger()
    _late(l)
    l.set_notice_state('target', 'APPROVED', H1 + 30 * M)
    with closing(sqlite3.connect(l.path)) as con, con:
        con.execute("DELETE FROM ledger_events WHERE kind='NOTICE_CREATED'")
    with pytest.raises(LedgerError):
        l.replay(H1 + 45 * M)
    with pytest.raises(MigrationError):
        Ledger(l.path)


def test_r09_apply_after_state_changes_keeps_q04_behaviour(make_ledger):
    """Q04 の形（APPLY 後の状態変更）は従来どおり: 各時点で現金 960000・予約 0、作成時刻以後は現在残高と一致。"""
    l = make_ledger()
    _late(l)
    l.set_notice_state('target', 'APPROVED', H3)
    l.set_notice_state('target', 'SENT', H3 + M)
    assert l.expire_notices(H4) == ['target']
    for at in (H2, H3, H4, H5):
        v = l.replay(at)
        assert v.cash == 960000 and v.reserved == 0
    assert l.replay(H6) == l.view()


def test_r10_l03_o01_and_overflow_preserved(make_ledger):
    """L03（早い解決の拒否）、O01（解決時刻の再生）、複数保留の合算と残数量超過拒否は維持。"""
    l = make_ledger()
    assert l.import_csv_fills([csv('unknown', eid='c1', qty=40, at=H1, broker_order_id='b1'),
                               csv('unknown', eid='c2', qty=70, at=H1 + M, broker_order_id='b2')]).pending == ['c1', 'c2']
    l.create_notice(proposal('target'), at=H6)
    with pytest.raises(LedgerError):
        l.resolve_pending('c1', 'target', H1 - US, 'APPLY')
    l.resolve_pending('c1', 'target', H2, 'APPLY')
    with pytest.raises(LedgerError):
        l.resolve_pending('c2', 'target', H2 + M, 'APPLY')            # 40 + 70 > 100
    assert set(l.pending_rows()) == {'c2'} and l.cash() == 960000
    assert l.replay(H2 - US).cash == 1000000 and l.replay(H2).cash == 960000 and l.replay(H2).reserved == 0
