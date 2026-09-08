"""2026-09-08 第6回：ops v0.3.2（全種別の内部属性除去・マーカー契約・一時ファイル方式の移行・端株精算の保有限定）の
独立レビュー（Claude, Cowork セッション）。

Codex の test_v032_contracts.py（F01〜F04）と第5回までの反例を壊さず、今回の契約を外側から壊しにいく。
失敗したケースは ops/Claude対応結果.md 第6回で A（契約の穴）/ B（未定義契約）に分類する。skip/xfail/条件緩和はしない。
"""
import hashlib
import json
import os
import sqlite3
from contextlib import closing
import sys
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'build-codex'), str(ROOT / 'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT  # noqa: E402,F401
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError, _payload_hash  # noqa: E402
from aitrader_ops.models import PositionIn, OpenOrderIn  # noqa: E402
from aitrader_ops.migrate import check, apply  # noqa: E402
import aitrader_ops.migrate as migrate_module  # noqa: E402

H1 = AT + timedelta(hours=1)
H2 = AT + timedelta(hours=2)
D1 = AT + timedelta(days=1)


def _insert(path, event_id, kind, payload, at=None):
    at = (at or AT).isoformat()
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    [event_id, kind, at, json.dumps(payload), at])


def _payloads(path):
    with closing(sqlite3.connect(path)) as con, con:
        return [(k, json.loads(p)) for k, p in con.execute('SELECT kind,payload FROM ledger_events ORDER BY seq')]


def _underscore_keys(value, prefix=''):
    found = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k.startswith('_'):
                found.append(prefix + k)
            found += _underscore_keys(v, prefix + k + '.')
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found += _underscore_keys(v, f'{prefix}[{i}].')
    return found


def _same_view(a, b):
    return asdict(a) == asdict(b)


def _names(folder, source):
    """原本自身の SQLite サイドカー（-wal/-shm/-journal。テスト側の接続が残す）を除いたファイル名。"""
    return sorted(p.name for p in folder.iterdir() if not p.name.startswith(source.name + '-'))


def _legacy_db(tmp_path, name='legacy.sqlite'):
    """v0.2 でだけ通る履歴（第5回 D04 と同じ形）。"""
    path = tmp_path / name
    l = Ledger(path)
    l.init_snapshot(1000000, [PositionIn('6857', 100, 1000.)], [], AT - timedelta(days=1))
    l.create_notice(proposal(), at=AT)
    l.close()
    _insert(path, 'legacy-split', 'ADJUST', dict(kind='SPLIT', code='6857', ratio=2, at=AT.isoformat()))
    _insert(path, 'csv:old1', 'CSV_FILL', dict(event_id='old1', proposal_id='buy1', code='6857', side='BUY', qty=100,
                                             price=1000., fee=0., at=None, broker_order_id='ob1'), at=None)
    return path


# ---- 重点1: 全種別の外部入力から内部属性が消えること（公開 API 経由・入れ子・非 dict 入力） ----------

def test_g01_every_public_entry_point_strips_internal_keys_including_nested_and_namespace_inputs(tmp_path):
    """SNAPSHOT / NOTICE_CREATED / NOTICE_STATE / STOP_ORDER / TRADE / CSV_FILL / PENDING_RESOLVED / ADJUST の
    どれも、公開 API に渡した入れ子の `_` キーを永続化しない。CSV_FILL だけは台帳が付けた紐付け先のみ持つ。"""
    path = tmp_path / 'strip.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000,
                    [SimpleNamespace(code='7203', qty=100, avg_price=2000., stop_order=False, stop_price=None, _rule_version=1)],
                    [SimpleNamespace(proposal_id=None, code='7203', side='SELL', qty=100, limit_price=2100.,
                                     broker_order_id='ext1', _applied_proposal_id='ghost')],
                    AT - timedelta(days=1))
    p = proposal(events={'next_earnings_date': None, 'margin_regulated': False, '_context_only': True,
                         'nested': [{'_hidden': 1, 'keep': '_literal'}]})
    l.create_notice(p, at=AT)
    l.set_notice_state('buy1', 'APPROVED', AT)
    l.set_stop_order('7203', True, 1900., AT)
    ev = dict(vars(trade()), _applied_proposal_id='ghost', meta={'_x': 1, 'y': {'_z': 2}})
    assert l.report(ev).applied
    row = dict(vars(csv(None, eid='c1', broker_order_id='o9', at=H2)), _applied_proposal_id='ghost', _reason='x')
    assert l.import_csv_fills([row]).skipped == [] and l.notice('buy1')['filled_qty'] == 100
    pend = dict(vars(csv('nope', eid='c2', broker_order_id='o10', at=H2)), _applied_proposal_id='buy1')
    assert l.import_csv_fills([pend]).pending == ['c2']
    l.resolve_pending('c2', None, H2, 'DISCARD', actor='norimitsu', reason='_internal looking reason')
    l.adjust('DEPOSIT', 1, None, None, H2, '_note')
    for kind, pl in _payloads(path):
        leaked = [k for k in _underscore_keys(pl) if not (kind == 'CSV_FILL' and k == '_applied_proposal_id')]
        assert not leaked, f'{kind} に内部風キーが保存されている: {leaked}'
    # 値としての `_` 文字列は保持される（キーだけを除く）
    notice = next(pl for kind, pl in _payloads(path) if kind == 'NOTICE_CREATED')
    assert notice['proposal']['events']['nested'] == [{'keep': '_literal'}]
    assert next(pl for kind, pl in _payloads(path) if kind == 'ADJUST')['note'] == '_note'
    # 再起動・replay・replay_known がすべて一致
    v = l.view()
    seq = l.seq()
    l.close()
    l2 = Ledger(path)
    try:
        assert _same_view(l2.view(), v) and _same_view(l2.replay(), v) and _same_view(l2.replay_known(seq), v)
    finally:
        l2.close()


def test_g02_audit_hash_is_over_external_input_only_and_separates_conflict_from_junk(make_ledger):
    """監査 hash は「外部入力（`_` キー除去後）」だけで計算され、台帳が付けた _applied_proposal_id や
    偽の `_` キーの有無で変わらない。公開フィールドの改変だけが ID_PAYLOAD_CONFLICT になる。"""
    l = make_ledger()
    l.create_notice(proposal(), at=AT)
    base = vars(csv(None))
    assert l.import_csv_fills([dict(base, _applied_proposal_id='ghost')]).applied == ['csv1']
    with closing(sqlite3.connect(l.path)) as con, con:
        stored = json.loads(con.execute("SELECT payload FROM ledger_events WHERE kind='CSV_FILL'").fetchone()[0])
        audited = con.execute("SELECT payload_hash FROM ingest_attempts WHERE source_event_id='csv1'").fetchone()[0]
    assert stored['_applied_proposal_id'] == 'buy1'
    from aitrader_ops.ledger import _to_plain
    assert audited == _payload_hash(_to_plain(base)), '監査 hash が除去後の外部入力と一致しない'
    assert audited != _payload_hash(stored), '監査 hash に台帳内部値が混ざっている'
    # 偽 `_` キー付きの再送 → DUPLICATE（衝突ではない）
    assert l.import_csv_fills([dict(base, _applied_proposal_id='other', _junk=[1])]).skipped == ['csv1']
    # 公開フィールドの改変を伴う再送 → ID_PAYLOAD_CONFLICT
    assert l.import_csv_fills([dict(base, price=1001.)]).skipped == ['csv1']
    with closing(sqlite3.connect(l.path)) as con, con:
        codes = [c for (c,) in con.execute("SELECT reason_code FROM ingest_attempts WHERE source_event_id='csv1' ORDER BY rowid")]
    assert codes == [None, 'DUPLICATE', 'ID_PAYLOAD_CONFLICT']


# ---- 重点2: replay と再起動の一致（保留→解決、訂正、匿名 CSV、マーカーを含む履歴） -----------------

def test_g03_replay_variants_agree_with_restart_on_a_mixed_history(tmp_path):
    path = tmp_path / 'mixed.sqlite'
    l = Ledger(path)
    l.init_snapshot(2000000, [PositionIn('7203', 200, 1500.)], [], AT - timedelta(days=1))
    l.create_notice(proposal(), at=AT)
    l.create_notice(proposal('sell1', code='7203', side='SELL', qty=200, limit_price=1600.), at=AT)
    assert l.report(trade(kind='PARTIAL', qty=50)).applied
    assert l.report(trade(eid='fix1', kind='CORRECTION', qty=60, price=990., replaces_event_id='fill1')).applied
    assert l.import_csv_fills([csv(None, eid='rest', qty=40, broker_order_id='o2', at=H2)]).applied == ['rest']
    assert l.import_csv_fills([csv('sell1', eid='s1', code='7203', side='SELL', qty=200, price=1600., broker_order_id='o3', at=H2)]).applied == ['s1']
    assert l.import_csv_fills([csv('unknown', eid='p1', broker_order_id='o4', at=H2)]).pending == ['p1']
    l.create_notice(proposal('buy2', code='6857'), at=H2)
    l.resolve_pending('p1', 'buy2', H2 + timedelta(minutes=5), 'APPLY', actor='norimitsu', reason='照合済み')
    l.adjust('FRACTIONAL_CASHOUT', 123, '6857', None, D1, '端株')
    seq = l.seq()
    v = l.view()
    assert v.positions['6857'].qty == 200 and '7203' not in v.positions
    l.close()
    l2 = Ledger(path)
    try:
        assert _same_view(l2.view(), v) and _same_view(l2.replay(), v) and _same_view(l2.replay_known(seq), v)
        assert _same_view(l2.replay(D1 + timedelta(days=30)), v)
        mid = l2.replay(H2 + timedelta(minutes=1))       # 保留解決前
        assert mid.positions["6857"].qty == 100 and mid.cash == v.cash - 123 + 100000   # 端株精算前・buy2 約定前
    finally:
        l2.close()


def test_g04_marker_is_a_sequence_boundary_even_when_its_wall_time_is_in_the_future(tmp_path):
    """マーカーの at が未来（例: 時計ずれ）でも、replay(at) は記録順の境界として扱い、マーカー後の履歴は新規則で再生する。"""
    src = _legacy_db(tmp_path)
    out = Path(apply(src)['output'])
    with closing(sqlite3.connect(out)) as con, con:
        con.execute("UPDATE ledger_events SET at=?, payload=json_set(payload,'$.at',?) WHERE kind='POLICY_UPGRADE'",
                    ['2099-01-01T00:00:00+09:00', '2099-01-01T00:00:00+09:00'])
    l = Ledger(out)
    try:
        assert l.import_csv_fills([csv(None, eid='new1', at=None, broker_order_id='ob9')]).pending == ['new1']
        l.create_notice(proposal('buy2', code='6857'), at=D1)
        with pytest.raises(LedgerError):
            l.adjust('SPLIT', None, '6857', 2, D1, '注文中')
        v = l.view()
        assert _same_view(l.replay(D1), v) and _same_view(l.replay(), v)
        assert l.replay(AT + timedelta(hours=12)).positions['6857'].qty == 300   # マーカー前の旧規則区間はそのまま
    finally:
        l.close()


# ---- 重点3: 複数・不正境界マーカー（型・形・別ハンドル経由） ------------------------------------

@pytest.mark.parametrize('payload', [
    ['not', 'a', 'dict'],
    dict(schema_version=3, at=AT.isoformat(), legacy_last_seq=True),
    dict(schema_version=3, at=AT.isoformat(), legacy_last_seq=4.0),
    dict(schema_version=3, at=AT.isoformat(), legacy_last_seq='4'),
    dict(schema_version=3, at=AT.isoformat()),
    dict(at=AT.isoformat(), legacy_last_seq=4),
    dict(schema_version='3', at=AT.isoformat(), legacy_last_seq=4),
])
def test_g05_malformed_markers_are_rejected_with_position(tmp_path, payload):
    src = _legacy_db(tmp_path)
    _insert(src, 'pu', 'POLICY_UPGRADE', payload)
    with pytest.raises(MigrationError) as e:
        Ledger(src)
    assert e.value.seq == 5 and e.value.kind == 'POLICY_UPGRADE'
    with pytest.raises(MigrationError):
        check(src)


def test_g06_bad_marker_appended_by_another_handle_stops_the_open_handle_before_it_writes(tmp_path):
    """別ハンドル（外部ツール）が不正マーカーを追記した後、開いているハンドルは次の書き込みで MigrationError になり、
    新しい履歴を追記しない（壊れた台帳の上に積まない）。"""
    path = tmp_path / 'open.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    l.create_notice(proposal(), at=AT)
    _insert(path, 'pu', 'POLICY_UPGRADE', dict(schema_version=3, at=AT.isoformat(), legacy_last_seq=99))
    try:                                   # report() は LedgerError 系を結果に畳む契約なので、どちらの形でも「適用されない」こと
        res = l.report(trade())
        assert not res.applied and 'POLICY_UPGRADE' in (res.error or '')
    except MigrationError:
        pass
    with pytest.raises(MigrationError):
        l.cash()
    with closing(sqlite3.connect(path)) as con, con:
        assert con.execute('SELECT COUNT(*) FROM ledger_events').fetchone()[0] == 3
        assert con.execute('SELECT COUNT(*) FROM ingest_attempts').fetchone()[0] == 0
    l.close()


def test_g07_second_marker_cannot_be_committed_through_the_ledger_itself(tmp_path):
    """移行済み台帳に POLICY_UPGRADE を（内部 API 経由でも）追記しようとすると、書き込み時点で拒否され、
    次回起動時に初めて壊れる『地雷』にならない。"""
    src = _legacy_db(tmp_path)
    out = Path(apply(src)['output'])
    l = Ledger(out)
    try:
        last = l.seq()
        with pytest.raises((LedgerError, MigrationError)):
            l._commit('POLICY_UPGRADE', dict(schema_version=3, at=D1, legacy_last_seq=last), D1, event_id='pu2')
        assert l.seq() == last
    finally:
        l.close()
    Ledger(out).close()     # 再起動できる


# ---- 重点4: 移行中の失敗・出力衝突・再試行・環境依存パス ---------------------------------------

def test_g08_output_collision_is_detected_before_any_temp_file_and_stale_temp_is_left_alone(tmp_path):
    src = _legacy_db(tmp_path)
    stale = tmp_path / 'legacy.stale.upgrading.sqlite'
    stale.write_bytes(b'crashed earlier run')
    (tmp_path / 'legacy.upgraded.sqlite').write_bytes(b'someone else output')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if not p.name.startswith('legacy.sqlite-')}
    with pytest.raises(FileExistsError):
        apply(src)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if not p.name.startswith('legacy.sqlite-')} == before, '衝突時に他のファイルへ触れている'
    (tmp_path / 'legacy.upgraded.sqlite').unlink()
    out = apply(src)
    assert out['verified'] and stale.read_bytes() == b'crashed earlier run', '他の実行の一時ファイルを消している'
    assert _names(tmp_path, src) == ['legacy.sqlite', 'legacy.stale.upgrading.sqlite', 'legacy.upgraded.sqlite']


def test_g09_failure_during_copy_or_check_leaves_nothing_but_the_source(tmp_path, monkeypatch):
    src = _legacy_db(tmp_path)
    before = hashlib.sha256(src.read_bytes()).hexdigest()

    def boom(path):
        raise RuntimeError('check failed')
    monkeypatch.setattr(migrate_module, 'check', boom)
    with pytest.raises(RuntimeError):
        apply(src)
    assert _names(tmp_path, src) == ['legacy.sqlite']
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    monkeypatch.undo()
    assert apply(src)['verified']


def test_g10_apply_works_with_relative_and_non_ascii_paths_and_an_open_reader_on_the_source(tmp_path, monkeypatch):
    """則光さんの実フォルダ名（! ※ 日本語 空白）に近いパスと相対パス指定、原本を開いたままの Ledger があっても移行できる。"""
    folder = tmp_path / '!!!※則光用 999_投資関係'
    folder.mkdir()
    src = _legacy_db(folder, '台帳 v0.2.sqlite')
    monkeypatch.chdir(folder)
    r = check('台帳 v0.2.sqlite')
    assert r['violation']['seq'] == 3 and r['eligible']
    with pytest.raises(MigrationError):
        Ledger(src)                                     # 原本は新規則では開けない（移行前）
    with closing(sqlite3.connect(src)) as reader, reader:                # 別プロセスの読み取りを模す
        reader.execute('SELECT COUNT(*) FROM ledger_events').fetchone()
        out = apply(Path('台帳 v0.2.sqlite'))
    assert Path(out['output']) == folder / '台帳 v0.2.upgraded.sqlite'
    assert not list(folder.glob('*.upgrading.sqlite*'))
    l = Ledger(out['output'])
    try:
        assert l.positions()['6857'].qty == 300 and l.cash() == 900000
    finally:
        l.close()


def test_g11_source_appended_after_apply_does_not_leak_into_output_and_check_reports_it(tmp_path):
    """移行後に原本へ追記があっても出力は変わらず（コピー方式の限界）、check(原本) の last_seq で検知できる。"""
    src = _legacy_db(tmp_path)
    out = apply(src)
    _insert(src, 'late', 'ADJUST', dict(kind='DEPOSIT', amount=5, code=None, ratio=None, at=D1.isoformat(), note='late'), at=D1)
    assert check(src)['last_seq'] == out['old_last_seq'] + 1
    l = Ledger(out['output'])
    try:
        assert l.seq() == out['old_last_seq'] + 1 and l.cash() == 900000     # +1 はマーカー。late は含まれない
    finally:
        l.close()


# ---- 重点5: 端株精算の保有銘柄限定（売却済み・外部注文のみ・表記ゆれ・分割後） ----------------------

def test_g12_cashout_rejected_for_sold_out_external_only_or_misspelled_codes(make_ledger):
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)],
                    orders=[OpenOrderIn(None, '7203', 'BUY', 100, 2000., 'ext1')])
    l.create_notice(proposal('sell1', code='6857', side='SELL', limit_price=1000.), at=AT)
    assert l.report(trade('sell1', eid='s1')).applied and '6857' not in l.positions()
    for code in ('6857', '7203', '6857 ', ' 6857', '06857', None, ''):
        with pytest.raises(LedgerError):
            l.adjust('FRACTIONAL_CASHOUT', 500, code, None, H1, f'{code!r}')
    assert l.cash() == 1100000
    with closing(sqlite3.connect(l.path)) as con, con:
        assert con.execute("SELECT COUNT(*) FROM ledger_events WHERE kind='ADJUST'").fetchone()[0] == 0


def test_g13_cashout_accepted_only_while_held_and_survives_restart(tmp_path):
    path = tmp_path / 'cashout.sqlite'
    l = Ledger(path)
    l.init_snapshot(1000000, [PositionIn('6857', 100, 1000.)], [], AT - timedelta(days=1))
    l.adjust('SPLIT', None, '6857', 3, AT, '3分割')
    l.adjust('FRACTIONAL_CASHOUT', 700, '6857', None, H1, '分割端株')
    assert l.cash() == 1000700 and l.positions()['6857'].qty == 300
    l.create_notice(proposal('sell1', code='6857', side='SELL', qty=300, limit_price=1000.), at=H1)
    assert l.report(trade('sell1', eid='s1', qty=300, price=400.)).applied
    with pytest.raises(LedgerError):
        l.adjust('FRACTIONAL_CASHOUT', 1, '6857', None, H2, '売却後')
    seq = l.seq()
    v = l.view()
    l.close()
    l2 = Ledger(path)
    try:
        assert _same_view(l2.view(), v) and _same_view(l2.replay_known(seq), v) and v.cash == 1120700
    finally:
        l2.close()
