"""2026-09-09 第8回：ops v0.3.4（provenance 付き新台帳・cutover 遮断・精算 note 検証と重複拒否）の独立レビュー
（Claude, Cowork セッション）。

Codex の test_v034_contracts.py（J01〜J07）と第7回までの反例を壊さず、新方式を外側から壊しにいく。
失敗したケースは ops/Claude対応結果.md 第8回で A（契約の穴）/ B（未定義契約）に分類する。skip/xfail/条件緩和はしない。
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

H1 = AT + timedelta(hours=1)
D1 = AT + timedelta(days=1)
CUT = D1


def _rows(path, sql):
    with closing(sqlite3.connect(path)) as con, con:
        return con.execute(sql).fetchall()


def _prov(**changes):
    p = dict(method='RECONCILED_OPENING', source_path='old.sqlite', source_sha256='a' * 64, source_last_seq=3,
             reconciliation_id='rec-1', reconciliation_record_path='rec-1.md', reconciliation_record_sha256='b' * 64,
             actor='norimitsu', reconciled_at=CUT, reason='照合済み開始残高', cutover_at=CUT, previous_policy_version='p1')
    p.update(changes)
    return p


def _new_ledger(path, provenance=None, cash=900000, at=CUT):
    l = Ledger(path)
    l.init_snapshot(cash, [PositionIn('6857', 100, 1000.)], [], at, provenance=provenance)
    l.create_notice(proposal('buy2', code='6857'), at=CUT + timedelta(hours=1))
    return l


def _note(**changes):
    n = dict(category='POST_EXIT_SETTLEMENT', settlement_type='FRACTIONAL_CASHOUT', code='6857',
             source_event_id='stmt-001', source_document_sha256='0' * 64, reconciliation_id='rec-1',
             actor='norimitsu', received_at=D1.isoformat(), reason='端株精算')
    n.update(changes)
    return json.dumps(n, ensure_ascii=False)


# ---- 重点1: provenance の型・空・日時・hash・未知 method、失敗時に SNAPSHOT が残らないこと ----------------

@pytest.mark.parametrize('bad', [
    'RECONCILED_OPENING',                                   # dict でない
    [('method', 'RECONCILED_OPENING')],
    _prov(method='reconciled_opening'),                     # 大小文字違い
    _prov(method='MANUAL'),
    _prov(source_sha256='A' * 63),                          # 桁不足
    _prov(source_sha256='g' * 64),                          # 16 進でない
    _prov(reconciliation_record_sha256=None),
    _prov(source_last_seq='3'),
    _prov(source_last_seq=True),
    _prov(source_last_seq=0),
    _prov(actor='   '),
    _prov(reason=''),
    _prov(cutover_at=CUT.replace(tzinfo=None)),             # naive
    _prov(cutover_at='2026-09-09'),                         # 日付だけ
    _prov(reconciled_at=None),
    _prov(previous_policy_version=1),                       # 文字列でない
])
def test_k01_invalid_provenance_leaves_no_snapshot_and_ledger_stays_uninitialised(tmp_path, bad):
    path = tmp_path / 'p.sqlite'
    l = Ledger(path)
    try:
        with pytest.raises(LedgerError):
            l.init_snapshot(900000, [], [], CUT, provenance=bad)
        assert _rows(path, 'SELECT COUNT(*) FROM ledger_events')[0][0] == 0
        l.init_snapshot(900000, [], [], CUT, provenance=_prov())        # 正しい provenance ならその後初期化できる
        assert l.cash() == 900000
    finally:
        l.close()


def test_k02_provenance_is_persisted_verbatim_and_cutover_survives_restart_and_replay(tmp_path):
    """出所は SNAPSHOT payload に公開属性として保存され（`_` キーは除去）、cutover は再起動・replay(at)・replay_known で同じに効く。"""
    path = tmp_path / 'p.sqlite'
    prov = _prov(_secret='x', extra='keep')
    l = _new_ledger(path, prov)
    stored = json.loads(_rows(path, "SELECT payload FROM ledger_events WHERE kind='SNAPSHOT'")[0][0])['provenance']
    assert '_secret' not in stored and stored['extra'] == 'keep' and stored['cutover_at'] == CUT.isoformat()
    assert l.report(trade(pid='buy2', eid='old1', at=CUT - timedelta(seconds=1))).pending
    assert l.report(trade(pid='buy2', eid='edge', at=CUT, broker_order_id='o2')).applied        # 境界ちょうどは受理
    v, seq = l.view(), l.seq()
    l.close()
    l2 = Ledger(path)
    try:
        assert 'old1' in l2.pending_rows() and asdict(l2.view()) == asdict(v)
        assert asdict(l2.replay()) == asdict(v) and asdict(l2.replay_known(seq)) == asdict(v)
        assert l2.replay(CUT + timedelta(minutes=30)).cash == 800000          # 境界約定は含まれ、保留は含まれない
    finally:
        l2.close()


def test_k03_cutover_after_snapshot_time_is_rejected(tmp_path):
    """v0.3.5 で採用（第9回で期待値を更新）: provenance.cutover_at は開始残高の at 以前でなければならない。
    cutover_at > at だと開始残高以後・cutover 前の正当な約定まで保留になるため、SNAPSHOT 登録時に拒否し、何も保存しない。"""
    path = tmp_path / 'p.sqlite'
    l = Ledger(path)
    try:
        with pytest.raises(LedgerError):
            l.init_snapshot(900000, [PositionIn('6857', 100, 1000.)], [], CUT - timedelta(days=2),
                            provenance=_prov(cutover_at=CUT))
        assert _rows(path, 'SELECT COUNT(*) FROM ledger_events')[0][0] == 0
        l.init_snapshot(900000, [PositionIn('6857', 100, 1000.)], [], CUT, provenance=_prov(cutover_at=CUT - timedelta(hours=1)))
        l.create_notice(proposal('buy2', code='6857'), at=CUT + timedelta(hours=1))
        assert l.report(trade(pid='buy2', eid='between', at=CUT - timedelta(minutes=30))).applied   # cutover 以後・at 以前は適用
    finally:
        l.close()


# ---- 重点2: cutover 遮断の周辺（未知通知・再送・改変・APPLY 迂回・CSV/TRADE 交差・時刻なし） ---------------

def test_k04_pre_cutover_reports_are_held_even_for_unknown_notices_and_never_applied_by_resend(tmp_path):
    l = _new_ledger(tmp_path / 'p.sqlite', _prov())
    try:
        res = l.report(trade(pid='ghost', eid='g1', at=H1))                  # 通知が新台帳にない
        assert res.pending and not res.error and 'g1' in l.pending_rows()
        again = l.report(trade(pid='ghost', eid='g1', at=H1))
        assert again.duplicate and not again.applied and not again.pending
        moved = l.report(trade(pid='buy2', eid='g1', at=CUT + timedelta(hours=2), broker_order_id='o9'))   # 同 ID で時刻を後ろへ改変
        assert moved.duplicate and not moved.applied and l.cash() == 900000
        codes = [r[0] for r in _rows(l.path, "SELECT reason_code FROM ingest_attempts WHERE source_event_id='g1' ORDER BY rowid")]
        assert codes == ['PENDING', 'DUPLICATE', 'ID_PAYLOAD_CONFLICT']
        assert len(_rows(l.path, "SELECT seq FROM ledger_events WHERE kind='TRADE'")) == 1
    finally:
        l.close()


def test_k05_csv_and_trade_for_the_same_pre_cutover_fill_are_both_held_and_apply_is_refused_for_each(tmp_path):
    l = _new_ledger(tmp_path / 'p.sqlite', _prov())
    try:
        assert l.report(trade(pid='buy2', eid='t1', at=H1)).pending
        assert l.import_csv_fills([csv('buy2', eid='c1', at=H1)]).pending == ['c1']
        assert l.import_csv_fills([csv(None, eid='c2', at=None, broker_order_id='ob2')]).pending == ['c2']   # 時刻なし
        for eid in ('t1', 'c1', 'c2'):
            with pytest.raises(LedgerError):
                l.resolve_pending(eid, 'buy2', CUT + timedelta(hours=2), 'APPLY', actor='norimitsu', reason='迂回')
        assert l.cash() == 900000 and set(l.pending_rows()) == {'t1', 'c1', 'c2'}
        assert l.notice('buy2')['filled_qty'] == 0
    finally:
        l.close()


def test_k06_discard_then_resend_of_a_pre_cutover_row_is_held_or_deduplicated_not_crashing(tmp_path):
    """OPS_V034_REPORT の契約「DISCARD した行を再送すれば再び保留され得る」を確認する。
    どちらの結果（再保留 / 重複扱い）でも、生の DB 例外で取込が落ちてはならず、残高は不変で台帳は継続利用できること。"""
    l = _new_ledger(tmp_path / 'p.sqlite', _prov())
    try:
        assert l.import_csv_fills([csv('buy2', eid='c1', at=H1)]).pending == ['c1']
        l.resolve_pending('c1', None, CUT + timedelta(hours=2), 'DISCARD', actor='norimitsu', reason='旧台帳に含まれる')
        assert l.pending_rows() == {}
        res = l.import_csv_fills([csv('buy2', eid='c1', at=H1), csv(None, eid='c9', broker_order_id='o9', at=CUT + timedelta(hours=2))])
        assert res.applied == ['c9'], '同じ取込バッチの他の行まで巻き添えになっている'
        assert res.skipped == ['c1'] and l.cash() == 800000 and l.pending_rows() == {}       # v0.3.5: 再送は DUPLICATE/DISCARDED
        codes = [r[0] for r in _rows(l.path, "SELECT reason_code FROM ingest_attempts WHERE source_event_id='c1' ORDER BY rowid")]
        assert codes == ['PENDING', None, 'DISCARDED']       # 保留 → DISCARD 操作の監査行（reason_code なし）→ 再送は DISCARDED
        assert len(_rows(l.path, "SELECT seq FROM ledger_events WHERE kind='CSV_FILL' AND event_id='csv:c1'")) == 1
        # TRADE 側も同じ: cutover 保留→DISCARD→再送
        assert l.report(trade(pid='buy2', eid='t1', at=H1)).pending
        l.resolve_pending('t1', None, CUT + timedelta(hours=3), 'DISCARD', actor='norimitsu', reason='旧台帳に含まれる')
        again = l.report(trade(pid='buy2', eid='t1', at=H1))
        assert again.duplicate and not again.applied and not again.pending and not again.error
        assert l.cash() == 800000
        v, seq = l.view(), l.seq()
        l.close()
        l2 = Ledger(l.path)                                                     # DISCARD 済み ID は再起動後も記憶される
        try:
            assert l2.report(trade(pid='buy2', eid='t1', at=H1)).duplicate and asdict(l2.view()) == asdict(v)
            assert asdict(l2.replay_known(seq)) == asdict(v)
        finally:
            l2.close()
        return
    finally:
        l.close()


def test_k07_post_cutover_paths_are_unchanged_including_anonymous_csv_and_corrections(tmp_path):
    l = _new_ledger(tmp_path / 'p.sqlite', _prov())
    try:
        t = CUT + timedelta(hours=2)
        assert l.report(trade(pid='buy2', eid='f1', kind='PARTIAL', qty=50, at=t)).applied
        assert l.report(trade(pid='buy2', eid='fix', kind='CORRECTION', qty=60, price=990., at=t + timedelta(minutes=1),
                              replaces_event_id='f1')).applied
        assert l.import_csv_fills([csv(None, eid='rest', qty=40, broker_order_id='o2', at=t + timedelta(minutes=2))]).applied == ['rest']
        assert l.notice('buy2')['filled_qty'] == 100 and l.positions()['6857'].qty == 200
        # 訂正だけを cutover 前の時刻で送ると保留になる（改変検知ではなく業務時刻による遮断）
        assert l.report(trade(pid='buy2', eid='fix2', kind='CORRECTION', qty=60, price=980., at=H1, replaces_event_id='f1')).pending
        assert l.positions()['6857'].qty == 200
    finally:
        l.close()


# ---- 重点3: 精算 note の欠損・偽装・同キー別種別・同時書込・障害時ロールバック --------------------------

@pytest.mark.parametrize('note, amount', [
    (_note(settlement_type='CASHOUT'), 700),
    (_note(code='7203'), 700),                               # 引数 code と不一致
    (_note(received_at=(D1 + timedelta(seconds=1)).isoformat()), 700),
    (_note(received_at=D1.replace(tzinfo=None).isoformat()), 700),
    (_note(source_document_sha256='x' * 64), 700),
    (_note(source_event_id=''), 700),
    (_note(actor=None), 700),
    (_note(), 0),
    (_note(), -1),
    (_note(), True),
    (_note(), 700.0),
])
def test_k08_invalid_settlement_notes_are_rejected_without_events(make_ledger, note, amount):
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    with pytest.raises(LedgerError):
        l.adjust('DIVIDEND', amount, '6857', None, D1, note)
    assert l.cash() == 1000000 and _rows(l.path, "SELECT COUNT(*) FROM ledger_events WHERE kind='ADJUST'")[0][0] == 0


def test_k09_settlement_accepts_equivalent_instant_in_another_offset_and_dedups_across_offsets(make_ledger):
    l = make_ledger(positions=[PositionIn('7203', 100, 1000.)])       # 6857 は未保有（売却後）
    utc = D1.astimezone(timezone.utc)
    l.adjust('DIVIDEND', 700, '6857', None, D1, _note(received_at=utc.isoformat()))
    with pytest.raises(LedgerError):
        l.adjust('DIVIDEND', 700, '6857', None, utc, _note())              # 同キー、別オフセット表記
    assert l.cash() == 1000700


def test_k10_settlement_scope_covers_deposit_whitespace_and_held_codes(make_ledger):
    """v0.3.5 で採用（第9回で期待値を更新）:
    (a) 同じ精算 note を DEPOSIT で送ると拒否（DEPOSIT は外部入金専用）、
    (b) source_event_id の前後空白は同一キー、
    (c) 保有中の銘柄への POST_EXIT_SETTLEMENT は拒否（FRACTIONAL_CASHOUT へ誘導）。"""
    l = make_ledger(positions=[PositionIn('7203', 100, 1000.)])
    l.adjust('DIVIDEND', 700, '6857', None, D1, _note())
    with pytest.raises(LedgerError):
        l.adjust('DEPOSIT', 700, '6857', None, D1, _note(source_event_id='stmt-002'))          # (a)
    with pytest.raises(LedgerError):
        l.adjust('DIVIDEND', 700, '6857', None, D1, _note(source_event_id='stmt-001 '))        # (b)
    with pytest.raises(LedgerError):
        l.adjust('DIVIDEND', 700, '7203', None, D1, _note(code='7203', source_event_id='stmt-003'))   # (c)
    l.adjust('FRACTIONAL_CASHOUT', 5, '7203', None, D1, '保有中の端株精算は従来どおり')
    l.adjust('DEPOSIT', 1, None, None, D1, '通常の入金 note は従来どおり')
    assert l.cash() == 1000000 + 700 + 5 + 1 and l.positions()['7203'].qty == 100
    assert _rows(l.path, "SELECT COUNT(*) FROM ledger_events WHERE kind='ADJUST'")[0][0] == 3


def test_k11_two_handles_cannot_both_record_the_same_settlement(tmp_path):
    path = tmp_path / 's.sqlite'
    a = Ledger(path)
    a.init_snapshot(1000000, [PositionIn('7203', 100, 1000.)], [], AT - timedelta(days=1))
    b = Ledger(path)
    try:
        assert b.cash() == 1000000                                             # b は a の初期化後に同期済み
        a.adjust('DIVIDEND', 700, '6857', None, D1, _note())
        with pytest.raises(LedgerError):
            b.adjust('DIVIDEND', 700, '6857', None, D1, _note())               # 別ハンドルは書込前の同期で重複を検知
        assert a.cash() == b.cash() == 1000700
        assert _rows(path, "SELECT COUNT(*) FROM ledger_events WHERE kind='ADJUST'")[0][0] == 1
    finally:
        a.close(); b.close()


def test_k12_failed_commit_after_settlement_validation_leaves_no_key_behind(tmp_path, monkeypatch):
    """検証は通るが永続化で失敗した精算は、キーを消費せず再試行できる（trial 状態が捨てられ、トランザクションは戻る）。"""
    import aitrader_ops.ledger as ledger_module
    path = tmp_path / 's.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [PositionIn('7203', 100, 1000.)], [], AT - timedelta(days=1))
    real_dumps = ledger_module.json.dumps
    calls = []
    def failing(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('simulated serialisation failure before INSERT')
    monkeypatch.setattr(ledger_module.json, 'dumps', failing)
    with pytest.raises(RuntimeError):
        l.adjust('DIVIDEND', 700, '6857', None, D1, _note())
    monkeypatch.setattr(ledger_module.json, 'dumps', real_dumps)
    assert calls and l.cash() == 1000000 and not l._con.in_transaction
    assert _rows(path, "SELECT COUNT(*) FROM ledger_events WHERE kind='ADJUST'")[0][0] == 0
    l.adjust('DIVIDEND', 700, '6857', None, D1, _note())                     # 再試行が通る（キーは消費されていない）
    assert l.cash() == 1000700
    with pytest.raises(LedgerError):
        l.adjust('DIVIDEND', 700, '6857', None, D1, _note())
    v, seq = l.view(), l.seq()
    l.close()
    l2 = Ledger(path)
    try:
        assert asdict(l2.view()) == asdict(v) and asdict(l2.replay_known(seq)) == asdict(v)
    finally:
        l2.close()


# ---- 重点4: 新台帳の時点再生（SNAPSHOT より前）と旧履歴 ----------------------------------------------

def test_k13_replay_before_new_snapshot_is_empty_not_old_ledger(tmp_path):
    """新台帳の replay(at < SNAPSHOT.at) は空（未初期化相当）であり、旧台帳の残高を再現しない（B: 契約として明記が必要）。"""
    l = _new_ledger(tmp_path / 'p.sqlite', _prov())
    try:
        v = l.replay(CUT - timedelta(days=1))
        assert v.cash == 0 and v.positions == {} and v.reserved == 0
    finally:
        l.close()
