"""2026-09-08 再レビュー。公開APIだけで残高・照合・時点の反例を検証する。"""
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path[:0] = [str(Path(__file__).resolve().parents[3] / 'ops'),
                str(Path(__file__).resolve().parents[3] / 'build-codex')]
from aitrader_ops.ledger import Ledger, LedgerError
from aitrader_ops.models import Proposal, PositionIn, OpenOrderIn, TradeEvent, CsvFill, Verdict, compute_packet_hash
from aitrader_ops.gate import evaluate
from aitrader_ops.limits import Limits

JST = timezone(timedelta(hours=9))
AT = datetime(2026, 9, 8, 8, 0, tzinfo=JST)


def proposal(pid='buy1', **changes):
    fields = dict(proposal_id=pid, packet_hash='', code='6857', side='BUY', qty=100,
                  lot_size=100, limit_price=1000., exec_condition='OPENING_LIMIT',
                  account_type='CASH', strategy='margin_bucket_long', strategy_version='v1',
                  as_of=date(2026, 9, 7), snapshot_id='s1', policy_version='p1',
                  expires_at=AT.replace(minute=59), reason='合成試験',
                  events={'next_earnings_date': None, 'margin_regulated': False})
    fields.update(changes)
    p = Proposal(**fields)
    return replace(p, packet_hash=compute_packet_hash(p))


def trade(pid='buy1', eid='fill1', kind='FILLED', qty=100, price=1000., **changes):
    fields = dict(event_id=eid, proposal_id=pid, kind=kind, qty=qty, price=price,
                  fee=0., at=AT + timedelta(hours=1), source='line', broker_order_id='order1')
    fields.update(changes)
    return TradeEvent(**fields)


def csv(pid=None, eid='csv1', **changes):
    fields = dict(event_id=eid, proposal_id=pid, code='6857', side='BUY', qty=100,
                  price=1000., fee=0., at=AT + timedelta(hours=1), broker_order_id='order1')
    fields.update(changes)
    return CsvFill(**fields)


@pytest.fixture
def make_ledger(tmp_path):
    opened = []
    def make(cash=1000000, positions=(), orders=(), fee_margin=.002, path=None, initialize=True):
        ledger = Ledger(path or tmp_path / f'ledger{len(opened)}.sqlite', fee_margin=fee_margin)
        opened.append(ledger)
        if initialize:
            ledger.init_snapshot(cash, list(positions), list(orders), AT-timedelta(days=1))
        return ledger
    yield make
    for ledger in opened:
        ledger.close()


def balances(ledger):
    return (ledger.cash(), ledger.reserved(), ledger.available(),
            {c: (p.qty, p.avg_price) for c, p in ledger.positions().items()})


def gate(ledger, p, now=AT, **changes):
    votes = [Verdict(judge=j, proposal_id=p.proposal_id, packet_hash=p.packet_hash,
                     decision='APPROVE', risks=(), reason='模擬判定', confidence=None,
                     received_at=now-timedelta(seconds=1), model='mock', cli_version='mock', run_id=j)
             for j in ('claude', 'codex')]
    args = dict(p=p, verdicts=votes, now=now, ledger_view=ledger.view(day_start_equity=1000000),
                limits=Limits(), equity=1000000, peak_equity=1000000, new_sent_today=0,
                stop_flag=False, unresolved_unconfirmed=False)
    args.update(changes)
    return evaluate(**args)


def test_r01_sell_partial_correction_preserves_acquisition_cost(make_ledger):
    """売却価格の訂正が残保有の取得原価を変えてしまわないか。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.create_notice(proposal('sell1', side='SELL'))
    assert l.report(trade('sell1', kind='PARTIAL', qty=40, price=1500.)).applied
    assert l.report(trade('sell1', 'fix1', 'CORRECTION', 40, 1400., replaces_event_id='fill1')).applied
    assert l.cash() == 1056000
    assert l.positions()['6857'].qty == 60
    assert l.positions()['6857'].avg_price == 1000.


def test_r02_correction_after_cancel_does_not_reopen_reservation(make_ledger):
    """残注文取消後の約定価格訂正が取り消した予約を復活させないか。"""
    l = make_ledger(); l.create_notice(proposal())
    assert l.report(trade(kind='PARTIAL', qty=40)).applied
    assert l.report(trade(eid='cancel', kind='CANCELLED', qty=60, price=0)).applied
    assert l.reserved() == 0
    assert l.report(trade(eid='fix', kind='CORRECTION', qty=40, price=990., replaces_event_id='fill1')).applied
    assert l.cash() == 960400
    assert l.reserved() == 0


@pytest.mark.parametrize('excess', [0, 1])
def test_r03_withdraw_reservation_boundary(make_ledger, excess):
    """予約後の出金が一円境界を守り、拒否時には残高を変えないか。"""
    l = make_ledger(); l.create_notice(proposal()); before = balances(l)
    args = ('WITHDRAW', l.available()+excess, None, None, AT, '境界試験')
    if excess:
        with pytest.raises(LedgerError):
            l.adjust(*args)
        assert balances(l) == before
    else:
        l.adjust(*args)
        assert l.available() == 0 and l.reserved() == 100200


def test_r04_notice_reservation_must_not_exceed_cash(make_ledger):
    """通知を続けて作った時に予約だけで余力を負にできないか。"""
    l = make_ledger(cash=150000); l.create_notice(proposal()); before = balances(l)
    with pytest.raises(LedgerError):
        l.create_notice(proposal('buy2', code='7203'))
    assert balances(l) == before


def test_r05_fill_preserves_other_notice_reservation(make_ledger):
    """実費の報告が別注文の予約を食い込み余力を負にしないか。"""
    l = make_ledger(cash=250000)
    l.create_notice(proposal()); l.create_notice(proposal('buy2', code='7203'))
    before = balances(l)
    result = l.report(trade(fee=60000.))
    assert result.error and not result.applied
    assert balances(l) == before


def test_r06_initial_buy_order_reserves_cash(make_ledger):
    """開始時点の発注済み買注文を自由な現金として数えていないか。"""
    l = make_ledger(orders=[OpenOrderIn('old', '6857', 'BUY', 100, 1000.)])
    assert l.reserved() == 100200 and l.available() == 899800


def test_r07_initial_sell_order_reserves_shares(make_ledger):
    """開始時点の売注文と新しい売通知で同じ保有株を二重予約しないか。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)],
                    orders=[OpenOrderIn('old', '6857', 'SELL', 100, 1000.)])
    with pytest.raises(LedgerError):
        l.create_notice(proposal('sell1', side='SELL'))


def test_r08_buy_sell_same_code_partial_and_cancel(make_ledger):
    """同じ銘柄の買いと売りの部分約定が現金・株数・予約を混同しないか。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.create_notice(proposal()); l.create_notice(proposal('sell1', side='SELL'))
    assert l.report(trade(kind='PARTIAL', qty=40)).applied
    assert l.report(trade('sell1', 'sellfill', 'PARTIAL', 50, 1100., broker_order_id='sellorder')).applied
    assert l.cash() == 1015000 and l.positions()['6857'].qty == 90
    assert l.reserved() == 60120
    assert l.report(trade(eid='cancel', kind='CANCELLED', qty=60, price=0)).applied
    assert l.reserved() == 0


def test_r09_csv_no_proposal_unique_open_notice(make_ledger):
    """proposal_id がない行でも一意の未約定候補には紐付けられるか。"""
    l = make_ledger(); l.create_notice(proposal())
    assert l.import_csv_fills([csv()]).applied == ['csv1']
    assert l.cash() == 900000 and l.positions()['6857'].qty == 100


def test_r10_csv_no_proposal_ambiguous_pending(make_ledger):
    """同一銘柄の未約定候補が二つあるCSVを勝手に選ばないか。"""
    l = make_ledger(); l.create_notice(proposal()); l.create_notice(proposal('buy2'))
    before = balances(l)
    assert l.import_csv_fills([csv()]).pending == ['csv1']
    assert balances(l) == before


@pytest.mark.parametrize('changes', [{'code': '7203'}, {'side': 'SELL'}], ids=['code', 'side'])
def test_r11_csv_explicit_proposal_must_match_contract(make_ledger, changes):
    """proposal_id だけを信用して別銘柄や逆売買のCSVを計上しないか。"""
    l = make_ledger(); l.create_notice(proposal()); before = balances(l)
    result = l.import_csv_fills([csv('buy1', **changes)])
    assert not result.applied and (result.pending or result.errors)
    assert balances(l) == before


def test_r12_old_csv_must_not_attach_to_new_notice(make_ledger):
    """全約定済みLINEのCSVが同銘柄の新しい候補へ誤って二重計上されないか。"""
    l = make_ledger(); l.create_notice(proposal())
    assert l.report(trade()).applied
    l.create_notice(proposal('buy2')); before = balances(l)
    result = l.import_csv_fills([csv()])
    assert not result.applied and (result.skipped or result.pending)
    assert balances(l) == before


def test_r13_csv_fee_difference_requires_reconciliation(make_ledger):
    """数量・価格・注文番号が同じでも手数料差を照合完了として隠さないか。"""
    l = make_ledger(); l.create_notice(proposal()); assert l.report(trade()).applied
    before = balances(l)
    result = l.import_csv_fills([csv('buy1', fee=100.)])
    assert result.pending or result.errors
    assert balances(l) == before


def test_r14_replay_before_notice_excludes_future_reservation(make_ledger):
    """通知作成前を指定した再計算に未来の通知予約を混入させないか。"""
    l = make_ledger()
    # 初期スナップショットより後、実際の通知作成より確実に前の時刻。
    cutoff = datetime.now(JST)-timedelta(seconds=1)
    assert cutoff > AT-timedelta(days=1)
    l.create_notice(proposal())
    assert l.replay(cutoff).reserved == 0


def test_r15_two_handles_current_equals_replay(make_ledger, tmp_path):
    """別ハンドルの追記後に更新すると現在残高と全履歴再計算が食い違わないか。"""
    path = tmp_path/'shared.sqlite'
    first = make_ledger(path=path); second = make_ledger(path=path, initialize=False)
    first.adjust('DEPOSIT', 1000, None, None, AT, 'first')
    second.adjust('DEPOSIT', 2000, None, None, AT, 'second')
    assert second.cash() == second.replay().cash == 1003000


def test_r16_reopen_preserves_committed_reserve_policy(make_ledger, tmp_path):
    """再起動の既定値で過去に確保した予約額が書き換わらないか。"""
    path = tmp_path/'policy.sqlite'
    first = make_ledger(path=path, fee_margin=.01); first.create_notice(proposal())
    reopened = make_ledger(path=path, initialize=False)
    assert reopened.reserved() == first.reserved() == 101000


def test_r17_gate_nan_returns_rejection(make_ledger):
    """価格NaNの不正パケットで不許可を返さず審査処理全体が落ちないか。"""
    p = replace(proposal(), limit_price=float('nan'))
    assert not gate(make_ledger(), p).allowed


def test_r18_earnings_across_weekend_blocks(make_ledger):
    """金曜執行・翌月曜決算を暦日三日だからと許可しないか。"""
    now = datetime(2026, 9, 4, 8, 0, tzinfo=JST)
    p = proposal(as_of=date(2026, 9, 3), expires_at=now.replace(minute=59),
                 events={'next_earnings_date': date(2026, 9, 7), 'margin_regulated': False})
    assert not gate(make_ledger(), p, now=now).allowed


def test_r19_sell_gate_accounts_for_existing_share_reservation(make_ledger):
    """既存売通知が全株予約中でも二つ目の売通知をゲートが許可しないか。"""
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.create_notice(proposal('sell1', side='SELL'))
    assert not gate(l, proposal('sell2', side='SELL')).allowed


def test_r20_mock_packet_to_gate_then_reserve(make_ledger):
    """主系パケットを模擬二承認で審査し、予約後の二件目は余力不足で止まるか。"""
    from aitrader.packet import build_proposals
    packets = build_proposals(
        candidates=[{'code': '6857', 'side': 'BUY', 'strategy': 'margin_bucket_long', 'strategy_version': 'v1'}],
        as_of=date(2026, 9, 7), prev_close={'6857': 995.}, lot_sizes={'6857': 100},
        events={'6857': {'next_earnings_date': None, 'margin_regulated': False}},
        snapshot_id='mock-snapshot', policy_version='mock-policy', budget_per_name=100000,
        business_days=[date(2026, 9, 8)])
    assert len(packets) == 1
    p = packets[0]; l = make_ledger(cash=150000)
    result = gate(l, p)
    assert result.allowed
    l.create_notice(p); l.set_notice_state(p.proposal_id, 'APPROVED', AT)
    assert l.reserved() == result.reserve_amount
    assert not gate(l, proposal('buy2', code='7203')).allowed
