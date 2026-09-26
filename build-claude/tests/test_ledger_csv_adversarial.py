"""CSV突合の敵対的境界テスト（独立検証・build-claude担当分）。

common/tests/phase2/test_ledger.py の CSV シナリオ(1-90行)を
aitrader.ledger.Ledger / aitrader.models.Proposal・packet_hash に対して移植し、
さらに import_csv_fills の仕様（担当分離ドキュメント参照）に基づく敵対的ケースを追加する。
"""
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as Record

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aitrader.ledger import Ledger, LedgerNotInitialized
from aitrader.models import Proposal, packet_hash

JST = timezone(timedelta(hours=9))
AT = datetime(2026, 9, 8, 8, 0, tzinfo=JST)


def make_proposal(**overrides):
    base = dict(
        proposal_id="A1-20260908-6857-01", packet_hash="", code="6857", side="BUY", qty=100,
        lot_size=100, limit_price=1000., exec_condition="OPENING_LIMIT", account_type="CASH",
        strategy="margin_bucket_long", strategy_version="v1", as_of=date(2026, 9, 7), snapshot_id="s1",
        policy_version="p1", expires_at=AT.replace(minute=59), reason="テスト",
        events={"next_earnings_date": None, "margin_regulated": False},
    )
    base.update(overrides)
    p = Proposal(**base)
    return replace(p, packet_hash=packet_hash(p))


def proposal():
    return make_proposal()


def event(kind="FILLED", id="e1", qty=100, price=1000, fee=0, source="line", broker="b1",
          at=AT + timedelta(hours=1), proposal_id=None):
    return Record(event_id=id, proposal_id=proposal_id or proposal().proposal_id, kind=kind, qty=qty,
                  price=price, fee=fee, at=at, source=source, broker_order_id=broker)


def csv_fill(id="csv1", broker="b1", proposal_id=None, code="6857", side="BUY", qty=100, price=1000,
             fee=0, at=AT + timedelta(hours=1)):
    return Record(event_id=id, proposal_id=proposal_id, code=code, side=side, qty=qty, price=price,
                  fee=fee, at=at, source="csv", broker_order_id=broker)


@pytest.fixture
def ledger(tmp_path):
    l = Ledger(tmp_path / "ledger.sqlite")
    l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    p = proposal()
    l.create_notice(p, at=AT)
    l.set_notice_state(p.proposal_id, "APPROVED", AT)
    l.set_notice_state(p.proposal_id, "SENT", AT)
    return l


def balance(l):
    return l.cash(), l.reserved(), l.available(), {c: (p.qty, p.avg_price) for c, p in l.positions().items()}


def assert_ok(result):
    assert not result.error


# --------------------------------------------------------------- 移植（common/tests/phase2/test_ledger.py 1-90行）

def test_repeat_csv(ledger):
    """同じCSVの再取込を重複売買にしない。"""
    ledger.import_csv_fills([csv_fill()])
    once = balance(ledger)
    ledger.import_csv_fills([csv_fill()])
    assert balance(ledger) == once
    assert ledger.cash() == 900000 and ledger.positions()["6857"].qty == 100


@pytest.mark.parametrize("csv_first", [False, True])
def test_csv_and_line_reverse_order(ledger, csv_first):
    """CSVとLINEの到着順序が逆でも同じ約定を一度だけ反映する。"""
    if csv_first:
        r1 = ledger.import_csv_fills([csv_fill()])
        r2 = ledger.report(event())
    else:
        r2 = ledger.report(event())
        r1 = ledger.import_csv_fills([csv_fill()])
    assert ledger.cash() == 900000 and ledger.positions()["6857"].qty == 100
    assert ledger.reserved() == 0


def test_ambiguous_csv_does_not_add(ledger):
    """証券IDがなく既存約定に似た行は追加計上せず照合待ちにする。"""
    ledger.report(event(broker=None))
    once = balance(ledger)
    result = ledger.import_csv_fills([csv_fill(broker=None)])
    assert balance(ledger) == once
    assert result.pending


# --------------------------------------------------------------- 追加の敵対的ケース

def test_csv_before_any_notice_is_error_or_pending(ledger):
    """通知が存在しない code/proposal_id へのCSV取込は約定計上してはならない。"""
    row = csv_fill(id="csv-none", proposal_id="NO-SUCH-PROPOSAL", broker="b-none")
    once = balance(ledger)
    result = ledger.import_csv_fills([row])
    assert balance(ledger) == once
    assert row.event_id in result.errors or row.event_id in result.pending


def test_csv_for_rejected_notice_not_applied(ledger, tmp_path):
    """REJECTED状態の通知に対するCSV約定は残高に反映されない。"""
    l = Ledger(tmp_path / "rejected.sqlite")
    l.init_snapshot(1000000, [], [], AT - timedelta(days=1))
    p = make_proposal(proposal_id="A1-20260908-6857-02")
    l.create_notice(p, at=AT)
    l.set_notice_state(p.proposal_id, "REJECTED", AT)
    once = balance(l)
    result = l.import_csv_fills([csv_fill(id="csv-rej", proposal_id=p.proposal_id, broker="b-rej")])
    assert balance(l) == once
    assert result.skipped or result.errors or result.pending
    assert "csv-rej" not in result.applied


def test_csv_sell_with_no_holdings_errors(ledger):
    """保有ゼロの銘柄をSELLするCSV行はエラー扱いで残高に影響しない。"""
    once = balance(ledger)
    row = csv_fill(id="csv-sell-none", code="9999", side="SELL", qty=100, broker="b-sell-none")
    result = ledger.import_csv_fills([row])
    assert balance(ledger) == once
    assert row.event_id in result.errors or row.event_id in result.pending


def test_csv_qty_zero_errors(ledger):
    """qty=0のCSV行はエラー。"""
    once = balance(ledger)
    row = csv_fill(id="csv-zero", qty=0, broker="b-zero")
    result = ledger.import_csv_fills([row])
    assert balance(ledger) == once
    assert row.event_id in result.errors


def test_csv_qty_negative_errors(ledger):
    """qtyが負のCSV行はエラー。"""
    once = balance(ledger)
    row = csv_fill(id="csv-neg", qty=-10, broker="b-neg")
    result = ledger.import_csv_fills([row])
    assert balance(ledger) == once
    assert row.event_id in result.errors


def test_csv_naive_datetime_handled(ledger):
    """タイムゾーンなしのatを持つCSV行を拒否しないか、明示的にエラーとして扱う（クラッシュしない）。"""
    naive_at = datetime(2026, 9, 8, 9, 0)  # tzinfo=None
    row = csv_fill(id="csv-naive", at=naive_at, broker="b-naive")
    result = ledger.import_csv_fills([row])
    assert row.event_id in result.applied or row.event_id in result.errors or row.event_id in result.pending


def test_two_csv_rows_same_event_id_in_one_call(ledger):
    """同一呼び出し内で同じevent_idが2行あっても一度しか計上しない。"""
    row = csv_fill(id="csv-dup-inline", broker="b-dup-inline")
    result = ledger.import_csv_fills([row, row])
    assert ledger.positions()["6857"].qty == 100
    assert ledger.cash() == 900000
    applied_count = result.applied.count("csv-dup-inline")
    assert applied_count <= 1


def test_two_csv_rows_exceed_remaining_qty(ledger):
    """1件目は適用され2件目が残数超過でエラーになる。"""
    r1 = csv_fill(id="csv-part1", qty=80, broker="b-part1")
    r2 = csv_fill(id="csv-part2", qty=80, broker="b-part2")
    result = ledger.import_csv_fills([r1, r2])
    assert "csv-part1" in result.applied
    assert "csv-part2" in result.errors
    assert ledger.positions()["6857"].qty == 80


def test_csv_after_notice_expired_still_applies(ledger):
    """期限切れ(EXPIRED)はキャンセルではないため、期限後のCSV約定は計上されてよい。"""
    ledger.expire_notices(AT + timedelta(days=10))
    row = csv_fill(id="csv-after-expire", broker="b-expire")
    result = ledger.import_csv_fills([row])
    assert "csv-after-expire" in result.applied
    assert ledger.positions()["6857"].qty == 100
    assert ledger.cash() == 900000


def test_csv_partial_then_line_filled_different_broker_sums_to_full(ledger):
    """CSV40株 + LINE FILLED60株（別broker_order_id）で合計100株になる。"""
    r1 = ledger.import_csv_fills([csv_fill(id="csv-p40", qty=40, broker="b-p40")])
    assert "csv-p40" in r1.applied
    assert ledger.positions()["6857"].qty == 40
    r2 = ledger.report(event(id="line-p60", kind="FILLED", qty=60, broker="b-p60"))
    assert_ok(r2)
    assert ledger.positions()["6857"].qty == 100
    assert ledger.cash() == 900000


def test_same_broker_id_reused_across_different_codes_not_confused(ledger, tmp_path):
    """同じbroker_order_idが別銘柄に使い回されても取り違えない。"""
    p2 = make_proposal(proposal_id="A1-20260908-7203-01", code="7203")
    ledger.create_notice(p2, at=AT)
    ledger.set_notice_state(p2.proposal_id, "APPROVED", AT)
    ledger.set_notice_state(p2.proposal_id, "SENT", AT)

    r1 = ledger.import_csv_fills([csv_fill(id="csv-6857-shared", broker="shared-id", code="6857",
                                           proposal_id=None)])
    r2 = ledger.import_csv_fills([csv_fill(id="csv-7203-shared", broker="shared-id", code="7203",
                                           proposal_id=p2.proposal_id, price=1000)])
    assert "csv-6857-shared" in r1.applied
    assert "csv-7203-shared" in r2.applied
    assert ledger.positions()["6857"].qty == 100
    assert ledger.positions()["7203"].qty == 100


def test_pending_then_line_report_then_resolve_apply_no_double_count(ledger):
    """LINE報告で既に約定計上された後、broker_order_idなしで一致するCSV行はpendingになり、
    そのpendingをAPPLYで解決しようとしても二重計上してはならない。"""
    line_result = ledger.report(event(id="line-first", broker=None))
    assert_ok(line_result)
    once = balance(ledger)
    assert once[3].get("6857", (0, 0))[0] == 100

    csv_row = csv_fill(id="csv-ambiguous", broker=None)
    result = ledger.import_csv_fills([csv_row])
    assert "csv-ambiguous" in result.pending
    assert balance(ledger) == once

    pending = ledger.pending_rows()["csv-ambiguous"]
    ledger.resolve_pending("csv-ambiguous", pending.get("proposal_id") or proposal().proposal_id,
                            AT + timedelta(hours=2), action="APPLY")
    after_resolve = balance(ledger)
    assert after_resolve == once, "pendingのAPPLYが二重計上してはならない"


def test_resolve_pending_unknown_event_id(ledger):
    """存在しないevent_idへのresolve_pendingは何も適用されず、Noneを返し状態も変えない。"""
    once = balance(ledger)
    result = ledger.resolve_pending("no-such-event", None, AT, action="APPLY")
    assert result is None
    assert balance(ledger) == once


def test_resolve_pending_discard_never_applies(ledger):
    """pending行をDISCARDした場合は決して約定計上されない。"""
    line_result = ledger.report(event(id="line-second", broker=None))
    assert_ok(line_result)
    once = balance(ledger)

    csv_row = csv_fill(id="csv-to-discard", broker=None)
    result = ledger.import_csv_fills([csv_row])
    assert "csv-to-discard" in result.pending
    pending = ledger.pending_rows()["csv-to-discard"]
    ledger.resolve_pending("csv-to-discard", pending.get("proposal_id"), AT + timedelta(hours=2), action="DISCARD")
    assert balance(ledger) == once
    assert "csv-to-discard" not in ledger.pending_rows()


def test_balance_at_before_snapshot_is_zeroed(ledger):
    """スナップショット時刻より前のbalance_atは初期化前状態として0/空を返す（例外は投げない）。"""
    result = ledger.balance_at(AT - timedelta(days=30))
    assert result["cash"] == 0
    assert result["positions"] == {}


def test_balance_at_replays_history(ledger):
    """balance_atは指定時刻までの履歴を再生した残高を返す。"""
    ledger.import_csv_fills([csv_fill(id="csv-bal", qty=100, broker="b-bal", at=AT + timedelta(hours=1))])
    before = ledger.balance_at(AT + timedelta(minutes=30))
    after = ledger.balance_at(AT + timedelta(hours=2))
    assert before["positions"].get("6857", {}).get("qty", 0) == 0
    assert after["positions"]["6857"]["qty"] == 100
    assert after["cash"] == 900000


def test_csv_fill_that_would_make_available_negative_refused(ledger):
    """CSV約定を適用すると余力が負になる場合は拒否する（残高不変）。"""
    p2 = make_proposal(proposal_id="A1-20260908-9001-01", code="9001", qty=100000, limit_price=1000.)
    ledger.create_notice(p2, at=AT)
    ledger.set_notice_state(p2.proposal_id, "APPROVED", AT)
    ledger.set_notice_state(p2.proposal_id, "SENT", AT)
    once = balance(ledger)
    row = csv_fill(id="csv-overdraw", proposal_id=p2.proposal_id, code="9001", qty=100000,
                    price=1000, broker="b-overdraw")
    result = ledger.import_csv_fills([row])
    assert row.event_id in result.errors
    assert balance(ledger) == once


def test_explicit_proposal_id_code_side_mismatch_is_pending(ledger):
    """明示的なproposal_idがあってもcode/sideが不一致ならpendingにする。"""
    once = balance(ledger)
    row = csv_fill(id="csv-mismatch", proposal_id=proposal().proposal_id, code="7203", side="SELL",
                    qty=100, broker=None)
    result = ledger.import_csv_fills([row])
    assert "csv-mismatch" in result.pending
    assert balance(ledger) == once


def test_missing_event_id_errors(ledger):
    """event_idが無いCSV行はエラーで残高不変。"""
    once = balance(ledger)
    row = Record(event_id=None, proposal_id=None, code="6857", side="BUY", qty=100, price=1000,
                fee=0, at=AT + timedelta(hours=1), source="csv", broker_order_id=None)
    result = ledger.import_csv_fills([row])
    assert balance(ledger) == once
    assert result.errors
