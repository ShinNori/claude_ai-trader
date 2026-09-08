"""2026-09-08 第7回：ops v0.3.3（G07 の書込時拒否）の独立レビューと、D08 の原本 hash 不安定（テスト側の未 close 接続）の解消確認
（Claude, Cowork セッション）。

Codex の test_v033_contracts.py（H01〜H03）と第6回までの反例を壊さない。skip/xfail/条件緩和はしない。
"""
import gc
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'build-codex'), str(ROOT / 'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT  # noqa: E402,F401
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError  # noqa: E402
from aitrader_ops.models import PositionIn  # noqa: E402
from aitrader_ops.migrate import check, apply  # noqa: E402

H1 = AT + timedelta(hours=1)
D1 = AT + timedelta(days=1)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _insert(path, event_id, kind, payload, at=None):
    """書込接続は commit と close の両方を保証する（第7回で統一した書き方）。"""
    at = (at or AT).isoformat()
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    [event_id, kind, at, json.dumps(payload), at])


def _rows(path, sql):
    with closing(sqlite3.connect(path)) as con, con:
        return con.execute(sql).fetchall()


def _legacy_db(tmp_path, name='legacy.sqlite'):
    path = tmp_path / name
    l = Ledger(path)
    l.init_snapshot(1000000, [PositionIn('6857', 100, 1000.)], [], AT - timedelta(days=1))
    l.create_notice(proposal(), at=AT)
    l.close()
    _insert(path, 'legacy-split', 'ADJUST', dict(kind='SPLIT', code='6857', ratio=2, at=AT.isoformat()))
    _insert(path, 'csv:old1', 'CSV_FILL', dict(event_id='old1', proposal_id='buy1', code='6857', side='BUY', qty=100,
                                             price=1000., fee=0., at=None, broker_order_id='ob1'), at=None)
    return path


# ---- 重点1: D08 の原本 hash はテスト側の接続を閉じた後に採れば、gc を挟んでも変わらない -------------------

def test_i01_source_hash_is_stable_across_gc_once_test_connections_are_closed(tmp_path):
    """Codex 指摘の再現条件（check 直前の gc.collect で hash が変わる）は、テストの書込接続が close されていないことが原因。
    close 後の hash を基準にすれば、gc・check・apply の前後で原本の本体は 1 バイトも変わらない。"""
    src = _legacy_db(tmp_path)                       # すべての接続は close 済み（WAL は最後の close で本体へ反映される）
    assert not (tmp_path / 'legacy.sqlite-wal').exists(), '書込接続が閉じられていない（WAL が残っている）'
    base = _sha(src)
    gc.collect()
    assert _sha(src) == base, 'close 後なのに gc で本体が変わった'
    r = check(src)
    gc.collect()
    assert _sha(src) == base and r['eligible']
    out = apply(src)
    gc.collect()
    assert _sha(src) == base and Path(out['output']).exists()
    # 逆に、閉じない書込接続を残すと本体は「未確定」であり、hash は基準にできない（Codex の再現を意図的に起こす）
    other = _legacy_db(tmp_path, 'unclosed.sqlite')
    leak = sqlite3.connect(other)
    with leak:
        leak.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                     ['late', 'ADJUST', D1.isoformat(), json.dumps(dict(kind='DEPOSIT', amount=1, code=None, ratio=None, at=D1.isoformat(), note='x')), D1.isoformat()])
    unstable = _sha(other)
    assert (tmp_path / 'unclosed.sqlite-wal').exists()
    leak.close()                                     # close で WAL が本体へ反映され、hash が変わる
    assert _sha(other) != unstable, 'この環境では close 前後で本体が変わらなかった（WAL 未使用？）'


def test_i02_d08_style_mismatch_refusal_is_stable_with_gc_and_closed_connections(tmp_path):
    """D08 と同じ履歴（旧新残高不一致）を閉じた接続で作り、gc を挟んでも: 原本不変・拒否・出力なし・一時ファイルなし。"""
    path = tmp_path / 'diff.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    l.create_notice(proposal(), at=AT)
    l.create_notice(proposal('p2', code='7203'), at=AT)
    l.close()
    _insert(path, 'trade:f1', 'TRADE', dict(event_id='f1', proposal_id='buy1', kind='FILLED', qty=100, price=1000., fee=0.,
                                            at=H1.isoformat(), source='line', broker_order_id='o1'), at=H1)
    _insert(path, 'csv:c1', 'CSV_FILL', dict(event_id='c1', proposal_id='p2', code='7203', side='BUY', qty=100, price=1000.,
                                             fee=0., at=H1.isoformat(), broker_order_id='o1'), at=H1)
    gc.collect()
    base = _sha(path)
    for _ in range(3):
        gc.collect()
        r = check(path)
        assert not r['eligible'] and r['legacy_balance']['cash'] == 900000 and r['current_balance']['cash'] == 800000
        with pytest.raises(ValueError):
            apply(path)
        gc.collect()
        assert _sha(path) == base
        assert not (tmp_path / 'diff.upgraded.sqlite').exists() and not list(tmp_path.glob('*.upgrading.sqlite*'))


# ---- 重点2: G07（v0.3.3）の書込時拒否 -----------------------------------------------------------

def test_i03_rejected_marker_leaves_events_audit_balance_and_restart_identical(tmp_path):
    """移行済み台帳・新台帳のどちらでも、内部 API 経由のマーカー追記は拒否され、履歴・監査行・残高・再起動が変わらない。"""
    src = _legacy_db(tmp_path)
    out = Path(apply(src)['output'])
    fresh = tmp_path / 'fresh.sqlite'
    f = Ledger(fresh)
    f.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    f.create_notice(proposal(), at=AT)
    assert f.report(trade()).applied
    f.close()
    for path in (out, fresh):
        l = Ledger(path)
        try:
            seq, view = l.seq(), l.view()
            events = _rows(path, 'SELECT seq,event_id,kind,payload FROM ledger_events ORDER BY seq')
            audits = _rows(path, 'SELECT * FROM ingest_attempts ORDER BY rowid')
            for marker in (dict(schema_version=3, at=D1, legacy_last_seq=seq),
                           dict(schema_version=3, at=D1, legacy_last_seq=0),
                           dict(schema_version=4, at=D1, legacy_last_seq=seq)):
                with pytest.raises(LedgerError):
                    l._commit('POLICY_UPGRADE', marker, D1, event_id=f'pu-{marker["schema_version"]}-{marker["legacy_last_seq"]}')
            assert l.seq() == seq and asdict(l.view()) == asdict(view)
            l.adjust('DEPOSIT', 1, None, None, D1, '拒否後も通常の書込は可能')
            assert l.cash() == view.cash + 1
            assert _rows(path, 'SELECT seq,event_id,kind,payload FROM ledger_events ORDER BY seq')[:len(events)] == events
            assert _rows(path, 'SELECT * FROM ingest_attempts ORDER BY rowid')[:len(audits)] == audits
            assert not [r for r in _rows(path, "SELECT event_id FROM ledger_events WHERE kind='POLICY_UPGRADE'") if r[0].startswith('pu-')]
            v2 = l.view()
        finally:
            l.close()
        l2 = Ledger(path)
        try:
            assert asdict(l2.view()) == asdict(v2) and asdict(l2.replay()) == asdict(v2)
        finally:
            l2.close()
    assert check(out)['already_upgraded'] and not check(fresh)['already_upgraded']


def test_i04_legit_first_upgrade_still_replays_and_raw_marker_on_fresh_ledger_is_caught_by_check(tmp_path):
    """正規の初回移行（旧規則区間あり）は v0.3.3 でも通る。
    一方、旧規則区間のない v0.3 台帳に生 SQL でマーカーを入れると、再生時はマーカー前が旧規則で再生されるため
    保留中の時刻なし CSV が『適用済み』に化ける。check はこれを旧新残高の不一致として検出し、apply は拒否する。"""
    src = _legacy_db(tmp_path)
    out = Path(apply(src)['output'])
    l = Ledger(out)
    try:
        assert l.positions()['6857'].qty == 300 and l.cash() == 900000
    finally:
        l.close()
    fresh = tmp_path / 'fresh.sqlite'
    f = Ledger(fresh)
    f.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    f.create_notice(proposal(), at=AT)
    assert f.import_csv_fills([csv(None, eid='nt', at=None, broker_order_id='ob1')]).pending == ['nt']
    last = f.seq()
    f.close()
    r = check(fresh)
    assert r['violation'] is None and r['current_balance']['cash'] == 1000000 and r['legacy_balance']['cash'] == 900000
    assert not r['eligible']
    with pytest.raises(ValueError):
        apply(fresh)
    # 生 SQL で入れてしまった場合の実際の再生結果（契約の限界の記録）: 旧規則区間として再生され、保留が適用済みになる
    _insert(fresh, 'raw-pu', 'POLICY_UPGRADE', dict(schema_version=3, at=D1.isoformat(), legacy_last_seq=last), at=D1)
    l = Ledger(fresh)
    try:
        assert l.cash() == 900000 and l.pending_rows() == {}
    finally:
        l.close()


def test_i05_state_level_rejection_matches_rebuild_level_rejection(tmp_path):
    """書込時（_on_policy_upgrade）と再生時（_validate_markers）の拒否が同じ入力に対して両方効く。"""
    src = _legacy_db(tmp_path)
    out = Path(apply(src)['output'])
    last = _rows(out, 'SELECT max(seq) FROM ledger_events')[0][0]
    _insert(out, 'raw-pu2', 'POLICY_UPGRADE', dict(schema_version=3, at=D1.isoformat(), legacy_last_seq=last), at=D1)
    with pytest.raises(MigrationError) as e:
        Ledger(out)
    assert e.value.seq == last + 1 and e.value.kind == 'POLICY_UPGRADE'
    with pytest.raises(MigrationError):
        check(out)


# ---- 重点3: 契約5点のうち、現 API で検証できる部分 -----------------------------------------------

def test_i06_post_exit_dividend_note_is_validated_and_deduplicated(make_ledger):
    """v0.3.4 で採用された契約（第8回で期待値を更新）: category=POST_EXIT_SETTLEMENT の DIVIDEND note は必須キーを検証し、
    同じ (category, source_event_id) の 2 回目は LedgerError。残高は 1 回分、履歴件数も不変。通常 note の互換は維持。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.create_notice(proposal('sell1', code='6857', side='SELL', limit_price=1000.), at=AT)
    assert l.report(trade('sell1', eid='s1')).applied and '6857' not in l.positions()
    note = json.dumps(dict(category='POST_EXIT_SETTLEMENT', settlement_type='FRACTIONAL_CASHOUT', code='6857',
                           source_event_id='stmt-001', source_document_sha256='0' * 64, reconciliation_id='rec-1',
                           actor='norimitsu', received_at=D1.isoformat(), reason='端株精算'), ensure_ascii=False)
    l.adjust('DIVIDEND', 700, '6857', None, D1, note)
    events = len(_rows(l.path, 'SELECT seq FROM ledger_events'))
    with pytest.raises(LedgerError):
        l.adjust('DIVIDEND', 700, '6857', None, D1, note)                      # 同一明細の再送
    changed = json.loads(note); changed.update(reason='金額訂正')
    with pytest.raises(LedgerError):
        l.adjust('DIVIDEND', 900, '6857', None, D1, json.dumps(changed, ensure_ascii=False))   # 同キー・別内容も拒否
    assert l.cash() == 1100000 + 700 and len(_rows(l.path, 'SELECT seq FROM ledger_events')) == events
    l.adjust('DIVIDEND', 1, '6857', None, D1, 'not json')                       # 通常 note は従来どおり
    assert l.cash() == 1100000 + 701
    seq = l.seq()
    v = l.view()
    l.close()
    l2 = Ledger(l.path)
    try:
        assert asdict(l2.view()) == asdict(v) and asdict(l2.replay_known(seq)) == asdict(v)
        with pytest.raises(LedgerError):                                       # 再起動後も重複キーは復元される
            l2.adjust('DIVIDEND', 700, '6857', None, D1, note)
    finally:
        l2.close()


def _provenance(**changes):
    p = dict(method='RECONCILED_OPENING', source_path='old.sqlite', source_sha256='a' * 64, source_last_seq=3,
             reconciliation_id='rec-1', reconciliation_record_path='rec-1.md', reconciliation_record_sha256='b' * 64,
             actor='norimitsu', reconciled_at=D1, reason='照合済み開始残高', cutover_at=D1, previous_policy_version='p1')
    p.update(changes)
    return p


def test_i07_new_ledger_with_provenance_holds_pre_cutover_reports_as_pending(tmp_path):
    """契約 2（v0.3.4）: provenance＋cutover_at 付きの新台帳は、旧台帳で適用済みの約定 ID を旧時刻で再送されても
    自動適用せず PENDING（残高不変・APPLY 不可・DISCARD 可）。cutover 以後の報告は通常どおり適用される。"""
    old = tmp_path / 'old.sqlite'
    o = Ledger(old)
    o.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    o.create_notice(proposal(), at=AT)
    assert o.report(trade()).applied and o.cash() == 900000
    o.close()
    new = tmp_path / 'new.sqlite'
    n = Ledger(new)
    n.init_snapshot(900000, [PositionIn('6857', 100, 1000.)], [], D1, provenance=_provenance())
    n.create_notice(proposal('buy2', code='6857'), at=D1 + timedelta(hours=1))
    res = n.report(trade(pid='buy2', eid='fill1'))                             # 旧台帳で適用済みの ID・旧時刻（AT+1h < cutover）
    assert not res.applied and res.pending and 'cutover' in (res.ignored_reason or '')
    assert n.cash() == 900000 and 'fill1' in n.pending_rows()
    with pytest.raises(LedgerError):
        n.resolve_pending('fill1', 'buy2', D1 + timedelta(hours=2), 'APPLY', actor='norimitsu', reason='迂回')
    assert n.cash() == 900000
    late = n.report(trade(pid='buy2', eid='fill2', broker_order_id='o2', at=D1 + timedelta(hours=2)))   # cutover 後
    assert late.applied and n.cash() == 800000
    n.resolve_pending('fill1', None, D1 + timedelta(hours=3), 'DISCARD', actor='norimitsu', reason='旧台帳に含まれる')
    assert n.pending_rows() == {}
    v, seq = n.view(), n.seq()
    n.close()
    n2 = Ledger(new)
    try:
        assert asdict(n2.view()) == asdict(v) and asdict(n2.replay()) == asdict(v) and asdict(n2.replay_known(seq)) == asdict(v)
    finally:
        n2.close()


def test_i07b_ledger_without_provenance_keeps_legacy_behaviour(tmp_path):
    """互換: provenance なしの新台帳は旧 event_id を記憶せず、通知が一致すれば適用する（cutover 保護は provenance 付きのみ）。"""
    new = tmp_path / 'plain.sqlite'
    n = Ledger(new)
    n.init_snapshot(900000, [PositionIn('6857', 100, 1000.)], [], D1)
    n.create_notice(proposal('buy2', code='6857'), at=D1 + timedelta(hours=1))
    res = n.report(trade(pid='buy2', eid='fill1'))
    assert res.applied and not res.pending and n.cash() == 800000
    n.close()
