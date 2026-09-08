"""2026-09-08 第5回：ops v0.3.1（C05 確定紐付け・POLICY_UPGRADE 移行・端株）の独立レビュー（Claude）。

Codex の 235 件を壊さず、今回の修正（内部属性の確定保存・旧規則区間・移行 CLI・端株現金化・監査連鎖）を
外側から壊しにいく反例。失敗するケースは現契約の穴（A）または未定義契約（B）として
ops/Claude対応結果.md 第5回に分類してある。合格させるための skip/xfail/条件緩和はしない。
"""
import hashlib
import json
import sqlite3
from contextlib import closing
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'build-codex'), str(ROOT / 'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT  # noqa: E402,F401
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError  # noqa: E402
from aitrader_ops.models import PositionIn  # noqa: E402
from aitrader_ops.migrate import check, apply  # noqa: E402
import aitrader_ops.migrate as migrate_module  # noqa: E402

H1 = AT + timedelta(hours=1)


def _insert(path, event_id, kind, payload, at=None):
    at = (at or AT).isoformat()
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    [event_id, kind, at, json.dumps(payload), at])


# ---- 重点1: 内部属性 _applied_proposal_id の偽装・確定先の安定・監査 hash ----------------

def test_d01_trade_report_cannot_smuggle_applied_proposal_id_into_replay(make_ledger):
    """LINE 報告（dict 入力）に内部属性 _applied_proposal_id を混ぜても、永続化されず replay(at) を壊さないこと。
    現状: TRADE では属性が取り除かれずに保存され、replay の『必要な通知』集合が偽 ID に置き換わるため、
    有効な約定の replay が『通知がありません』で失敗する。"""
    l = make_ledger()
    l.create_notice(proposal(), at=AT + timedelta(hours=2))          # 遅着通知（C05 と同じ形）
    ev = dict(event_id='f1', proposal_id='buy1', kind='FILLED', qty=100, price=1000., fee=0., at=H1,
              source='line', broker_order_id='o1', _applied_proposal_id='ghost')
    assert l.report(ev).applied and l.cash() == 900000
    with closing(sqlite3.connect(l.path)) as con, con:
        stored = json.loads(con.execute("SELECT payload FROM ledger_events WHERE kind='TRADE'").fetchone()[0])
    assert '_applied_proposal_id' not in stored, '外部入力の内部属性が TRADE payload に保存されている'
    assert l.replay(AT + timedelta(hours=1, minutes=30)).cash == 900000


def test_d02_csv_spoofed_binding_is_replaced_and_persisted_binding_wins(make_ledger):
    """CSV 行の偽 _applied_proposal_id（別銘柄の通知）は無視され、実際の紐付け先が保存される。"""
    l = make_ledger()
    l.create_notice(proposal(), at=AT)
    l.create_notice(proposal('other', code='7203'), at=AT)
    row = dict(event_id='c1', proposal_id=None, code='6857', side='BUY', qty=100, price=1000., fee=0., at=H1,
               broker_order_id='o1', _applied_proposal_id='other')
    assert l.import_csv_fills([row]).applied == ['c1']
    with closing(sqlite3.connect(l.path)) as con, con:
        stored = json.loads(con.execute("SELECT payload FROM ledger_events WHERE kind='CSV_FILL'").fetchone()[0])
    assert stored['_applied_proposal_id'] == 'buy1'
    assert l.notice('buy1')['filled_qty'] == 100 and l.notice('other')['filled_qty'] == 0


def test_d03_anonymous_csv_binding_survives_reopen_and_replay_even_after_second_candidate(make_ledger, tmp_path):
    """匿名 CSV の確定先は、後から同銘柄・同方向の通知が増えても再起動・replay(at)・replay_known で変わらない。"""
    path = tmp_path / 'bind.sqlite'
    l = make_ledger(path=path)
    l.create_notice(proposal(), at=AT)
    assert l.import_csv_fills([csv(None)]).applied == ['csv1']
    l.create_notice(proposal('buy2', code='6857'), at=AT + timedelta(hours=2))  # 2 件目の候補
    seq = l.seq()
    l.close()
    l2 = Ledger(path)
    try:
        assert l2.notice('buy1')['filled_qty'] == 100 and l2.notice('buy2')['filled_qty'] == 0
        assert l2.replay(AT + timedelta(hours=1, minutes=30)).cash == 900000
        assert l2.replay_known(seq).cash == 900000
        # 同じ行の再送は、偽属性を付けても DUPLICATE のまま（監査 hash は入力原文で比較）
        again = dict(vars(csv(None)), _applied_proposal_id='buy2')   # 同じ原文＋偽属性
        assert l2.import_csv_fills([again]).skipped == ['csv1']
        with closing(sqlite3.connect(path)) as con, con:
            assert con.execute('SELECT reason_code FROM ingest_attempts ORDER BY rowid DESC LIMIT 1').fetchone()[0] == 'DUPLICATE'
    finally:
        l2.close()


# ---- 重点2: POLICY_UPGRADE の境界・旧規則の再現・不正マーカー ---------------------------

def _legacy_db(tmp_path, name='legacy.sqlite'):
    """v0.2 でだけ通る履歴: 注文中 SPLIT（ratio 2）と時刻なし CSV の即時適用を直接追記して再現する。"""
    path = tmp_path / name
    l = Ledger(path)
    l.init_snapshot(1000000, [PositionIn('6857', 100, 1000.)], [], AT - timedelta(days=1))
    l.create_notice(proposal(), at=AT)
    l.close()
    _insert(path, 'legacy-split', 'ADJUST', dict(kind='SPLIT', code='6857', ratio=2, at=AT.isoformat()))
    _insert(path, 'csv:old1', 'CSV_FILL', dict(event_id='old1', proposal_id='buy1', code='6857', side='BUY', qty=100,
                                             price=1000., fee=0., at=None, broker_order_id='ob1'), at=None)
    return path


def test_d04_legacy_region_replays_old_rules_and_new_region_enforces_new_rules(tmp_path):
    """マーカー前は旧規則（注文中 SPLIT・時刻なし CSV の適用）で再現され、マーカー後は新規則が効く。"""
    src = _legacy_db(tmp_path)
    result = apply(src)
    l = Ledger(result['output'])
    try:
        old = l.replay_known(result['old_last_seq'])
        assert old.positions['6857'].qty == 300 and old.cash == 900000           # 200（分割）＋100（旧 CSV 即時適用）
        assert l.view().cash == 900000 and l.replay(AT + timedelta(days=1)).cash == 900000
        assert l.import_csv_fills([csv(None, eid='new1', at=None, broker_order_id='ob9')]).pending == ['new1']
        l.create_notice(proposal('buy2', code='6857'), at=AT + timedelta(days=1))   # マーカー後の注文中
        with pytest.raises(LedgerError):
            l.adjust('SPLIT', None, '6857', 2, AT + timedelta(days=1), 'post marker: 注文中は拒否')
        assert l.positions()['6857'].qty == 300
    finally:
        l.close()


def test_d05_marker_before_snapshot_or_unknown_version_is_rejected(tmp_path):
    """初期化前のマーカー・未知の規則版は MigrationError（位置付き）で止まる。"""
    path = tmp_path / 'bad.sqlite'
    Ledger(path).close()
    _insert(path, 'pu', 'POLICY_UPGRADE', dict(schema_version=3, at=AT.isoformat(), legacy_last_seq=0))
    with pytest.raises(MigrationError) as e1:
        Ledger(path)
    assert e1.value.seq == 1 and e1.value.kind == 'POLICY_UPGRADE'
    path2 = _legacy_db(tmp_path, 'bad2.sqlite')
    _insert(path2, 'pu', 'POLICY_UPGRADE', dict(schema_version=4, at=AT.isoformat(), legacy_last_seq=4))
    with pytest.raises(MigrationError) as e2:
        Ledger(path2)
    assert e2.value.kind == 'POLICY_UPGRADE' and e2.value.seq == 5


def test_d06_duplicate_or_inconsistent_marker_is_not_accepted_silently(tmp_path):
    """マーカーは 1 個で、legacy_last_seq が自分の直前 seq と一致すること。
    現状: 2 個目のマーカーも、legacy_last_seq=99 のような矛盾値も、そのまま読み込まれる（B: マーカー契約が未定義）。"""
    src = _legacy_db(tmp_path)
    out = Path(apply(src)['output'])
    _insert(out, 'pu2', 'POLICY_UPGRADE', dict(schema_version=3, at=AT.isoformat(), legacy_last_seq=99))
    with pytest.raises(MigrationError):
        Ledger(out)


# ---- 重点3: migrate --check / --apply の原本保全・失敗時の挙動 ------------------------------

def test_d07_check_and_apply_leave_source_bytes_and_events_untouched(tmp_path):
    src = _legacy_db(tmp_path)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    with closing(sqlite3.connect(src)) as con, con:
        rows_before = con.execute('SELECT * FROM ledger_events ORDER BY seq').fetchall()
    r = check(src)
    assert r['violation']['seq'] == 3 and r['eligible'] and not r['already_upgraded']
    out = apply(src)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    with closing(sqlite3.connect(out['output'])) as con, con:
        assert con.execute('SELECT * FROM ledger_events WHERE seq<=? ORDER BY seq', [out['old_last_seq']]).fetchall() == rows_before
        assert con.execute("SELECT COUNT(*) FROM ledger_events WHERE kind='POLICY_UPGRADE'").fetchone()[0] == 1


def test_d08_balance_mismatch_between_rules_refuses_migration(tmp_path):
    """旧規則と新規則の両方で再生できるが残高が違う履歴（別銘柄の同キー約定を v0.2 は重複扱い）は自動移行しない。"""
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
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    r = check(path)
    assert r['violation'] is None and r['legacy_balance']['cash'] == 900000 and r['current_balance']['cash'] == 800000
    assert not r['eligible']
    with pytest.raises(ValueError):
        apply(path)
    # v0.3.2 で採用した失敗時契約（第6回で期待値を更新）: 検証は一時ファイル上で行い、成功時だけ公開、失敗時は一時ファイルを削除する。
    # 旧期待値「失敗コピーは診断用に残る」は廃止。原本は不変で、出力も一時ファイルも残らない。
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before, '原本が変更された'
    assert not (tmp_path / 'diff.upgraded.sqlite').exists(), '拒否された移行の出力が残っている'
    assert not list(tmp_path.glob('*.upgrading.sqlite*')), '一時ファイルが残っている'
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith('diff.sqlite-')) == ['diff.sqlite']   # 原本のサイドカーは除く


def test_d09_failed_apply_must_not_leave_a_copy_that_looks_migrated(tmp_path, monkeypatch):
    """マーカー追記後の検証で失敗した場合の契約（v0.3.2 採用、第6回で期待値を更新）:
    原本は不変、.upgraded.sqlite も一時ファイル（*.upgrading.sqlite とその -wal/-shm/-journal）も残らない、
    障害を取り除いた再実行は FileExistsError で止まらずに成功し、その出力だけが移行済みになる。"""
    src = _legacy_db(tmp_path)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    original = migrate_module.Ledger.replay_known

    def boom(self, seq):
        raise RuntimeError('simulated failure after marker insert')
    monkeypatch.setattr(migrate_module.Ledger, 'replay_known', boom)
    with pytest.raises(RuntimeError):
        apply(src)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before, '原本が変更された'
    assert not (tmp_path / 'legacy.upgraded.sqlite').exists(), '失敗した検証の出力が公開されている'
    assert not list(tmp_path.glob('*.upgrading.sqlite*')), '一時ファイルが残っている'
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith('legacy.sqlite-')) == ['legacy.sqlite']   # 原本のサイドカーは除く
    assert not check(src)['already_upgraded']
    # 障害解除後の再実行
    monkeypatch.setattr(migrate_module.Ledger, 'replay_known', original)
    out = apply(src)
    assert out['verified'] and Path(out['output']) == tmp_path / 'legacy.upgraded.sqlite'
    assert check(out['output'])['already_upgraded'] and not check(src)['already_upgraded']
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob('*.upgrading.sqlite*'))
    with pytest.raises(FileExistsError):      # 既に出力があるときだけ衝突として止まる
        apply(src)


# ---- 重点4: 端株 SPLIT 拒否と FRACTIONAL_CASHOUT の範囲 ------------------------------------

def test_d10_fractional_split_rejected_even_via_string_ratio_and_positions_unchanged(make_ledger):
    l = make_ledger(positions=[PositionIn('6857', 101, 1000.)])
    for ratio in (0.5, '0.5', 1.1):
        with pytest.raises(LedgerError):
            l.adjust('SPLIT', None, '6857', ratio, AT, '端株')
    assert l.positions()['6857'].qty == 101 and l.positions()['6857'].avg_price == 1000.


def test_d11_cashout_only_moves_cash_and_rejects_negative_or_fractional_amount(make_ledger):
    l = make_ledger(positions=[PositionIn('6857', 101, 1000.)])
    for bad in (-1, 1.5, '500', True):
        with pytest.raises(LedgerError):
            l.adjust('FRACTIONAL_CASHOUT', bad, '6857', None, AT, 'bad')
    l.adjust('FRACTIONAL_CASHOUT', 500, '6857', None, AT, 'ok')
    v = l.view()
    assert v.cash == 1000500 and v.positions['6857'].qty == 101 and v.positions['6857'].avg_price == 1000.


def test_d12_cashout_requires_a_held_code(make_ledger):
    """端株精算は保有銘柄に紐付く現金イベントであるべき。
    現状: 未保有銘柄や code=None でも受理される（B: 精算と銘柄の紐付け契約が未定義）。"""
    l = make_ledger()
    with pytest.raises(LedgerError):
        l.adjust('FRACTIONAL_CASHOUT', 500, '9999', None, AT, 'unheld')
    with pytest.raises(LedgerError):
        l.adjust('FRACTIONAL_CASHOUT', 500, None, None, AT, 'no code')
    assert l.cash() == 1000000


# ---- 重点5: 監査失敗の連鎖・ロールバック、時刻なし CSV の保留 -------------------------------

def test_d13_audit_failure_on_success_path_rolls_back_event_and_state(make_ledger):
    """適用成功側でも監査 INSERT が失敗すれば残高イベントごと ROLLBACK され、再起動後も残高不変。"""
    l = make_ledger()
    l.create_notice(proposal(), at=AT)
    with closing(sqlite3.connect(l.path)) as con, con:
        con.execute("CREATE TRIGGER deny BEFORE INSERT ON ingest_attempts BEGIN SELECT RAISE(FAIL,'audit down'); END")
    with pytest.raises(sqlite3.DatabaseError):
        l.report(trade())
    assert l.cash() == 1000000 and l.notice('buy1')['filled_qty'] == 0
    with closing(sqlite3.connect(l.path)) as con, con:
        con.execute('DROP TRIGGER deny')
        assert con.execute("SELECT COUNT(*) FROM ledger_events WHERE kind='TRADE'").fetchone()[0] == 0
    assert l.report(trade()).applied and l.cash() == 900000


def test_d14_untimed_csv_stays_pending_across_restart_and_replay(make_ledger, tmp_path):
    path = tmp_path / 'untimed.sqlite'
    l = make_ledger(path=path)
    l.create_notice(proposal(), at=AT)
    assert l.import_csv_fills([csv(at=None)]).pending == ['csv1']
    assert l.cash() == 1000000 and l.replay(AT + timedelta(days=1)).cash == 1000000
    l.close()
    l2 = Ledger(path)
    try:
        assert 'csv1' in l2.pending_rows() and l2.cash() == 1000000
        l2.resolve_pending('csv1', 'buy1', AT + timedelta(hours=2), 'APPLY', actor='human', reason='証券画面で確認')
        assert l2.cash() == 900000
        # 解決後に届いた時刻付き LINE 報告は自動では重複確定されず、照合エラーとして返る（S07 契約の帰結）
        r = l2.report(trade())
        assert not r.applied and r.error and l2.cash() == 900000
    finally:
        l2.close()
