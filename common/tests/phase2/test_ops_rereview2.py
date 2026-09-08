"""ops v0.2の再々レビュー。既存テストは変更せず、公開APIと監査SQLを検証。"""
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest
from test_ops_rereview import AT, proposal, trade, csv, gate, make_ledger, balances
from aitrader_ops.models import PositionIn, OpenOrderIn
from aitrader_ops.ledger import LedgerError


def test_s01_external_cancel_releases(make_ledger):
    """初期注文の取消で外部注文の予約を解放できるか。"""
    l = make_ledger(orders=[OpenOrderIn('old', '6857', 'BUY', 100, 1000.)])
    assert l.report(trade('old', kind='CANCELLED', price=0)).applied
    assert l.reserved() == 0 and l.cash() == 1000000


def test_s02_split_without_orders_preserves_cost(make_ledger):
    """未注文株の分割で総取得原価を保てるか。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.adjust('SPLIT', None, '6857', 2, AT, '分割')
    assert l.positions()['6857'].qty == 200
    assert l.positions()['6857'].avg_price == 500.


def test_s03_split_with_order_requires_reconciliation(make_ledger):
    """売注文を残したまま株数だけ分割して注文との不整合を隠さないか。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.create_notice(proposal('sell', side='SELL'), at=AT)
    before = balances(l)
    with pytest.raises(LedgerError):
        l.adjust('SPLIT', None, '6857', 2, AT, '注文調整契約なし')
    assert balances(l) == before


def test_s04_sell_correction_after_intervening_buy(make_ledger):
    """後続買付のある売却価格訂正で残株の原価を再平均して変えないか。"""
    l = make_ledger(positions=[PositionIn('6857', 200, 1000.)])
    l.create_notice(proposal('sell', side='SELL'), at=AT)
    assert l.report(trade('sell', price=1500.)).applied
    l.create_notice(proposal(), at=AT)
    assert l.report(trade(eid='buyfill', price=2000., broker_order_id='order2')).applied
    before = l.positions()['6857'].avg_price
    assert l.report(trade('sell', 'fix', 'CORRECTION', price=1400., replaces_event_id='fill1')).applied
    assert l.positions()['6857'].avg_price == before == 1500.


def test_s05_cross_code_order_key_collision(make_ledger):
    """横断照合で別銘柄の同キー約定を重複として消さないか。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    assert l.report(trade()).applied
    l.create_notice(proposal('buy2', code='7203'), at=AT)
    result = l.import_csv_fills([csv('buy2', code='7203')])
    assert not result.skipped
    assert result.applied or result.pending or result.errors


def test_s06_distinct_timed_partial_fills(make_ledger):
    """同注文・同価格・同数量でも異時刻の部分約定は別計上するか。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    assert l.report(trade(kind='PARTIAL', qty=50)).applied
    assert l.report(trade(eid='fill2', qty=50, at=AT+timedelta(hours=1, seconds=1))).applied
    assert l.positions()['6857'].qty == 100


def test_s07_timeless_partial_requires_pending(make_ledger):
    """時刻なしの同数量部分約定を確定重複として捨てないか。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    assert l.report(trade(kind='PARTIAL', qty=50, at=None)).applied
    result = l.import_csv_fills([csv('buy1', qty=50, at=None)])
    assert result.pending


def test_s08_replay_known_is_stable(make_ledger):
    """後着の訂正で以前の記録順スナップショットを書き換えないか。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    assert l.report(trade()).applied
    seq = l.seq()
    assert l.report(trade(eid='fix', kind='CORRECTION', price=990.)).applied
    assert l.replay_known(seq).cash == 900000
    assert l.cash() == 901000


def test_s09_backdated_fill_replay_dependency(make_ledger):
    """遅れて作った外部報告用通知を時刻で除外し、約定だけ再生して落ちないか。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT+timedelta(hours=2))
    assert l.report(trade(at=AT+timedelta(hours=1))).applied
    assert l.replay(AT+timedelta(hours=1, minutes=1)).cash == 900000


def test_s10_audit_applied_duplicate_ignored(make_ledger):
    """重複と遅着無視を残高へ再適用せず監査には残せるか。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    l.report(trade()); l.report(trade()); l.report(trade(eid='late', kind='ORDERED'))
    with sqlite3.connect(l.path) as con:
        rows = con.execute('SELECT outcome,payload_hash FROM ingest_attempts ORDER BY rowid').fetchall()
    assert [r[0] for r in rows] == ['APPLIED', 'DUPLICATE', 'IGNORED']
    assert all(len(r[1]) == 64 for r in rows)
    assert l.cash() == 900000


def test_s11_audit_empty_id_rejected(make_ledger):
    """空IDの拒否報告も全受信試行の監査から落とさないか。"""
    l = make_ledger(); assert l.report(trade(eid='')).error
    with sqlite3.connect(l.path) as con:
        assert con.execute("SELECT count(*) FROM ingest_attempts WHERE outcome='REJECTED'").fetchone()[0] == 1


def test_s12_pending_resolution_revalidates_code(make_ledger):
    """人間の紐付け操作でも別銘柄へ約定を適用できないか。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    assert l.import_csv_fills([csv('buy1', code='7203')]).pending
    before = balances(l)
    with pytest.raises(LedgerError):
        l.resolve_pending('csv1', 'buy1', AT)
    assert balances(l) == before


def test_s13_invalid_resolution_action(make_ledger):
    """不明な照合操作名をAPPLYとみなして約定させないか。"""
    l = make_ledger(); assert l.import_csv_fills([csv()]).pending
    l.create_notice(proposal(), at=AT)
    with pytest.raises(LedgerError):
        l.resolve_pending('csv1', 'buy1', AT, action='TYPO')


def test_s14_invalid_verdict_reason_code(make_ledger):
    """形式不正の判定を正常な不承認と同じ理由コードにしないか。"""
    from aitrader_ops.models import Verdict
    p = proposal()
    votes = [Verdict(j, p.proposal_id, p.packet_hash, 'INVALID', (), '', None, AT, 'mock', 'mock', j)
             for j in ('claude', 'codex')]
    result = gate(make_ledger(), p, verdicts=votes)
    assert not result.allowed and 'REVIEW_INVALID' in result.reason_codes


def test_s15_external_orders_require_reconciliation(make_ledger):
    """発注済み外部注文を翌朝の未確認一覧から消してしまわないか。"""
    l = make_ledger(orders=[OpenOrderIn('old', '6857', 'BUY', 100, 1000.)])
    assert 'old' in l.unconfirmed(AT+timedelta(days=1))


def test_s16_audit_failure_is_atomic(make_ledger):
    """監査書込失敗時に約定だけ確定して監査のない残高を作らないか。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    before = balances(l)
    with sqlite3.connect(l.path) as con:
        con.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON ingest_attempts BEGIN SELECT RAISE(FAIL, 'audit unavailable'); END")
    with pytest.raises(sqlite3.DatabaseError):
        l.report(trade())
    assert balances(l) == before
