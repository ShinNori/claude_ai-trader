"""ops v0.3 独立レビュー（Claude, 2026-09-08）。共通受入テスト・主系・Codex の ops 自前テストは変更しない。

各テストの docstring は「何を壊そうとしているか」。C 番号は ops/Claude対応結果.md 第4回の表に対応。
"""
import sqlite3
from contextlib import closing
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'build-codex'), str(ROOT / 'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, csv, AT, balances, gate  # noqa: E402
from aitrader_ops.ledger import Ledger, LedgerError  # noqa: E402
from aitrader_ops.models import PositionIn, OpenOrderIn  # noqa: E402

JST = timezone(timedelta(hours=9))


# ---- 訂正と原価 ----------------------------------------------------------------

def test_c01_sell_correction_chain_keeps_cost_basis(make_ledger):
    """SELL の価格訂正を2回連鎖させても取得原価が動かず、現金は最終訂正値だけを反映するか。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.create_notice(proposal('sell1', side='SELL'), at=AT)
    assert l.report(trade('sell1', kind='PARTIAL', qty=40, price=1500.)).applied
    assert l.report(trade('sell1', 'fix1', 'CORRECTION', 40, 1400., replaces_event_id='fill1')).applied
    assert l.report(trade('sell1', 'fix2', 'CORRECTION', 40, 1450., replaces_event_id='fix1')).applied
    assert l.cash() == 1000000 + 40 * 1450
    assert l.positions()['6857'].qty == 60 and l.positions()['6857'].avg_price == 1000.


def test_c02_sell_correction_after_later_buy_keeps_new_average(make_ledger):
    """売却後に別単価で買い増した後の売却価格訂正が、買い増し後の平均原価を壊さないか。"""
    l = make_ledger(cash=1000000, positions=[PositionIn('6857', 200, 1000.)])
    l.create_notice(proposal('sell1', side='SELL'), at=AT)
    assert l.report(trade('sell1', kind='FILLED', qty=100, price=1500.)).applied          # 原価 100,000 取り崩し
    l.create_notice(proposal('buy2', code='6857', limit_price=2000.), at=AT)
    assert l.report(trade('buy2', 'b2fill', 'FILLED', 100, 2000., broker_order_id='o2')).applied
    assert l.positions()['6857'].avg_price == 1500.                                       # (100k + 200k) / 200
    assert l.report(trade('sell1', 'fix1', 'CORRECTION', 100, 1400., replaces_event_id='fill1')).applied
    assert l.positions()['6857'].qty == 200 and l.positions()['6857'].avg_price == 1500.
    assert l.cash() == 1000000 + 100 * 1400 - 100 * 2000


def test_c03_sell_qty_correction_rejected_with_error_result(make_ledger):
    """SELL 数量の訂正が例外ではなく error 付き ReportResult として呼出側に伝わり、残高不変か。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.create_notice(proposal('sell1', side='SELL'), at=AT)
    assert l.report(trade('sell1', kind='PARTIAL', qty=40, price=1500.)).applied
    before = balances(l)
    r = l.report(trade('sell1', 'fix1', 'CORRECTION', 30, 1500., replaces_event_id='fill1'))
    assert r.error and not r.applied and balances(l) == before


def test_c04_buy_correction_after_shares_sold_is_rejected_atomically(make_ledger):
    """買付を全部売却した後の買付価格訂正が、部分反映せずエラーで止まるか（v0.3 の既知制限の確認）。"""
    l = make_ledger()
    l.create_notice(proposal(), at=AT)
    assert l.report(trade()).applied                                                       # 100@1000
    l.create_notice(proposal('sell1', side='SELL'), at=AT)
    assert l.report(trade('sell1', 'sfill', 'FILLED', 100, 1200., broker_order_id='s1')).applied
    before = balances(l)
    r = l.report(trade('buy1', 'fix1', 'CORRECTION', 100, 990., replaces_event_id='fill1'))
    assert r.error and balances(l) == before


# ---- replay と業務時刻 ---------------------------------------------------------------

def test_c05_replay_effective_time_with_csv_without_proposal_id(make_ledger):
    """proposal_id なしの CSV 約定（業務時刻 T+1h）を、通知作成時刻 T+2h より前の時点で replay しても約定が再現されるか。"""
    l = make_ledger()
    l.create_notice(proposal(), at=AT + timedelta(hours=2))          # 通知の作成時刻は約定より後（遅着）
    res = l.import_csv_fills([csv(None, at=AT + timedelta(hours=1))])
    assert res.applied == ['csv1'] and l.cash() == 900000
    view = l.replay(AT + timedelta(hours=1, minutes=30))
    assert view.cash == 900000, '約定は有効時刻内なのに replay から消えている'
    assert view.reserved == 0


def test_c06_replay_context_notice_does_not_count_as_position_slot(make_ledger):
    """context_only で補完された通知が、予約額だけでなく予約銘柄・売却予約株数にも現れないか。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.create_notice(proposal('sell1', side='SELL'), at=AT + timedelta(hours=2))
    assert l.report(trade('sell1', kind='PARTIAL', qty=40, price=1500., at=AT + timedelta(hours=1))).applied
    view = l.replay(AT + timedelta(hours=1, minutes=30))
    assert view.reserved_shares == {} and view.reserved_positions == {}
    assert view.positions['6857'].qty == 60


def test_c07_replay_known_is_monotonic_in_seq(make_ledger):
    """replay_known(seq) が各 seq で例外を出さず、最終 seq で現在残高と一致するか。"""
    l = make_ledger()
    l.create_notice(proposal(), at=AT)
    l.report(trade(kind='PARTIAL', qty=40))
    l.report(trade(eid='cancel', kind='CANCELLED', qty=60, price=0))
    l.report(trade(eid='fix', kind='CORRECTION', qty=40, price=990., replaces_event_id='fill1'))
    last = l.seq()
    for s in range(1, last + 1):
        l.replay_known(s)
    v = l.replay_known(last)
    assert (v.cash, v.reserved) == (l.cash(), l.reserved())


# ---- 監査・原子性 ------------------------------------------------------------------

def test_c08_rejected_report_is_audited_and_not_persisted(make_ledger):
    """検証エラーで拒否した報告が ledger_events に残らず、ingest_attempts には REJECTED が残るか。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    seq = l.seq()
    r = l.report(trade(qty=101))
    assert r.error
    with closing(sqlite3.connect(l.path)) as con, con:
        assert con.execute('SELECT COUNT(*) FROM ledger_events').fetchone()[0] == seq
        row = con.execute("SELECT outcome, reason_code FROM ingest_attempts ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row == ('REJECTED', 'VALIDATION')


def test_c09_pending_apply_failure_keeps_pending_row(make_ledger):
    """保留行の APPLY が数量超過で失敗したとき、保留行が消えず残高も変わらないか。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    assert l.report(trade(kind='PARTIAL', qty=80)).applied
    res = l.import_csv_fills([csv('buy1', eid='c1', code='7203')])       # 銘柄不一致 → pending
    assert res.pending == ['c1']
    l.create_notice(proposal('buy7203', code='7203', qty=100), at=AT)
    before = balances(l)
    with pytest.raises(LedgerError):
        l.resolve_pending('c1', 'buy1', AT + timedelta(hours=3))          # 銘柄不一致で拒否
    assert 'c1' in l.pending_rows() and balances(l)[:3] == before[:3]


def test_c10_audit_rows_have_business_time_and_proposal(make_ledger):
    """監査行に業務時刻と proposal_id が入っているか（後から照合できるか）。"""
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    l.report(trade())
    with closing(sqlite3.connect(l.path)) as con, con:
        row = con.execute("SELECT business_at, proposal_id, ledger_seq FROM ingest_attempts WHERE outcome='APPLIED'").fetchone()
    assert row[0] and row[1] == 'buy1' and row[2] == l.seq()


# ---- 分割・外部注文 ------------------------------------------------------------------

def test_c11_split_allowed_when_only_other_code_has_open_order(make_ledger):
    """別銘柄の注文中や、同銘柄の完了済み注文だけがある場合に SPLIT を拒否しないか。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.create_notice(proposal('buy7203', code='7203'), at=AT)
    l.adjust('SPLIT', None, '6857', 2, AT, '分割')
    assert l.positions()['6857'].qty == 200 and l.positions()['6857'].avg_price == 500.
    l.create_notice(proposal('buy6857', code='6857'), at=AT)
    assert l.report(trade('buy6857', 'f', 'FILLED', 100, 1000., broker_order_id='o9')).applied
    l.adjust('SPLIT', None, '6857', 2, AT + timedelta(hours=5), '完了後の分割')
    assert l.positions()['6857'].qty == 600


def test_c12_reverse_split_with_fractional_result_is_rejected(make_ledger):
    """端株が出る併合（101株×0.5）を黙って切り捨てず拒否するか。"""
    l = make_ledger(positions=[PositionIn('6857', 101, 1000.)])
    with pytest.raises(LedgerError):
        l.adjust('SPLIT', None, '6857', 0.5, AT, '併合')
    assert l.positions()['6857'].qty == 101


def test_c13_external_buy_fill_releases_reserve_and_leaves_unconfirmed(make_ledger):
    """初期の発注済み買注文が約定報告で保有に変わり、予約解放・未確認一覧から消えるか。"""
    l = make_ledger(orders=[OpenOrderIn('old', '6857', 'BUY', 100, 1000., 'ob1')])
    assert 'old' in l.unconfirmed(AT)
    r = l.report(trade('old', 'ext-fill', 'FILLED', 100, 999., broker_order_id='ob1'))
    assert r.applied and l.reserved() == 0 and l.positions()['6857'].qty == 100
    assert 'old' not in l.unconfirmed(AT + timedelta(days=1))


# ---- 移行（v0.2 履歴を v0.3 で開く） --------------------------------------------------

def test_c14_legacy_history_violating_new_rule_fails_at_open_with_no_seq_hint(tmp_path):
    """v0.2 で受理された『注文中の分割』履歴を v0.3 で開くと起動時に落ちる（移行課題の再現）。"""
    path = tmp_path / 'legacy.sqlite'
    l = Ledger(path); l.init_snapshot(1000000, [PositionIn('6857', 100, 1000.)], [], AT - timedelta(days=1))
    l.create_notice(proposal('buy1', code='6857'), at=AT); l.close()
    # v0.2 なら通っていた ADJUST/SPLIT を、v0.3 の検証を通さず直接追記して旧履歴を再現
    import json
    with closing(sqlite3.connect(path)) as con, con:
        con.execute("INSERT INTO ledger_events(event_id, kind, at, payload, recorded_at) VALUES (?,?,?,?,?)",
                    ('sys:legacy-split', 'ADJUST', AT.isoformat(),
                     json.dumps({'kind': 'SPLIT', 'amount': None, 'code': '6857', 'ratio': 2, 'at': AT.isoformat(), 'note': 'v0.2'}),
                     AT.isoformat()))
    with pytest.raises(LedgerError) as ei:
        Ledger(path)
    # 現状: どの seq で止まったかが例外に含まれない → 移行案で MigrationError(seq) を提案
    assert 'seq' not in str(ei.value)


# ---- ゲート --------------------------------------------------------------------------

def test_c15_gate_reason_codes_align_with_reasons(make_ledger):
    """reason_codes が reasons と同じ数・同じ順で返り、INVALID が REVIEW_INVALID になるか。"""
    from dataclasses import replace
    from aitrader_ops.models import Verdict
    p = proposal(); l = make_ledger()
    votes = [Verdict(judge=j, proposal_id=p.proposal_id, packet_hash=p.packet_hash, decision=d, risks=(), reason='',
                     confidence=None, received_at=AT, model='m', cli_version='c', run_id=j)
             for j, d in (('claude', 'INVALID'), ('codex', 'APPROVE'))]
    r = gate(l, p, verdicts=votes, stop_flag=True)
    assert len(r.reason_codes) == len(r.reasons) >= 2
    assert 'REVIEW_INVALID' in r.reason_codes and 'STOP_NEW' in r.reason_codes


def test_c16_gate_survives_none_verdicts(make_ledger):
    """verdicts=None で例外を出さず不許可を返すか。"""
    r = gate(make_ledger(), proposal(), verdicts=None)
    assert not r.allowed and 'REVIEW_INCOMPLETE' in r.reason_codes
