"""通知ゲートの契約テスト。未定義の入力型は属性レコードで渡す（ISSUES参照）。"""
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as Record

import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[3]/'ops'),str(Path(__file__).resolve().parents[3]/'build-codex')]
pytest.importorskip('aitrader_ops',reason='Claude担当のopsパッケージはまだ未実装')
from aitrader_ops.models import Proposal, Verdict
from aitrader_ops.limits import Limits
from aitrader_ops.gate import evaluate
from aitrader.packet import packet_hash

JST=timezone(timedelta(hours=9)); NOW=datetime(2026,9,8,8,58,tzinfo=JST)

def proposal(**changes):
    fields=dict(proposal_id='A1-20260908-6857-01',packet_hash='',code='6857',side='BUY',qty=100,
        lot_size=100,limit_price=1000.,exec_condition='OPENING_LIMIT',account_type='CASH',
        strategy='margin_bucket_long',strategy_version='v1',as_of=date(2026,9,7),snapshot_id='s1',
        policy_version='p1',expires_at=NOW.replace(hour=8,minute=59),reason='テスト',
        events={'next_earnings_date':None,'margin_regulated':False})
    fields.update(changes); p=Proposal(**fields)
    return replace(p,packet_hash=packet_hash(p))

def votes(p):
    return [Verdict(judge=j,proposal_id=p.proposal_id,packet_hash=p.packet_hash,decision='APPROVE',
        risks=(),reason='テスト',confidence=None,received_at=NOW-timedelta(seconds=1),
        model='fixture',cli_version='fixture',run_id=j+'-run') for j in ('claude','codex')]

def view(**changes):
    fields=dict(cash=1000000,reserved=0,available=1000000,positions={},reserved_positions={},daily_pnl=0,day_start_equity=1000000)
    fields.update(changes); return Record(**fields)

def check(p=None,**changes):
    p=p or proposal()
    fields=dict(p=p,verdicts=votes(p),now=NOW,ledger_view=view(),limits=Limits(),equity=1000000,
        peak_equity=1000000,new_sent_today=0,stop_flag=False,unresolved_unconfirmed=False)
    fields.update(changes); return evaluate(**fields)

def test_baseline_allows_and_reserves():
    """拒否だけを返す空実装を防ぎ正常条件と予約額を確認する。"""
    r=check(); assert r.allowed and r.category=='NEW' and r.reserve_amount==100200

@pytest.mark.parametrize('decision',['REJECT','ABSTAIN','INVALID'])
@pytest.mark.parametrize('index',[0,1])
def test_non_approve_blocks(decision,index):
    """どちらか一方の拒否・棄権・応答不正を承認扱いしない。"""
    p=proposal(); vs=votes(p); vs[index]=replace(vs[index],decision=decision)
    assert not check(p,verdicts=vs).allowed

@pytest.mark.parametrize('kind',['empty','one','duplicate','third'])
def test_exactly_two_distinct_judges(kind):
    """人数だけ数える実装や重複した同一AIでの二重承認を拒否する。"""
    p=proposal(); a,b=votes(p)
    vs={'empty':[],'one':[a],'duplicate':[a,a],'third':[a,b,a]}[kind]
    assert not check(p,verdicts=vs).allowed

@pytest.mark.parametrize('field,value',[('packet_hash','0'*64),('proposal_id','other')])
def test_response_bound_to_exact_proposal(field,value):
    """旧パケットや別候補の承認を流用させない。"""
    p=proposal(); vs=votes(p); vs[1]=replace(vs[1],**{field:value})
    assert not check(p,verdicts=vs).allowed

@pytest.mark.parametrize('seconds,allowed',[(-1,True),(0,True),(1,False)])
def test_expiry_second_boundary(seconds,allowed):
    """締切ちょうどを許可し一秒超過を拒否する。"""
    p=proposal(); assert check(p,now=p.expires_at+timedelta(seconds=seconds)).allowed is allowed

def test_expiry_same_instant_utc():
    """UTC前日とJST当日を別の締切と誤認しない。"""
    p=proposal(); assert check(p,now=p.expires_at.astimezone(timezone.utc)).allowed

@pytest.mark.parametrize('changes',[{'qty':0},{'qty':-100},{'qty':101},{'lot_size':0},
    {'exec_condition':'MARKET'},{'account_type':'MARGIN'},{'side':'SHORT'}])
def test_invalid_order_contract(changes):
    """現物・単元・執行条件に違反する候補を通知しない。"""
    assert not check(proposal(**changes)).allowed

@pytest.mark.parametrize('events',[{'next_earnings_date':'UNKNOWN','margin_regulated':False},
    {'next_earnings_date':None,'margin_regulated':'UNKNOWN'},
    {'next_earnings_date':None,'margin_regulated':True},{}])
def test_unknown_or_regulated_events(events):
    """決算や規制の欠損を安全とみなさない。"""
    assert not check(proposal(events=events)).allowed

def test_earnings_on_execution_day():
    """執行当日の決算イベントを拒否する。"""
    assert not check(proposal(events={'next_earnings_date':date(2026,9,8),'margin_regulated':False})).allowed

@pytest.mark.parametrize('flag',['stop_flag','unresolved_unconfirmed'])
def test_new_blocked_by_stop_or_unresolved(flag):
    """停止中・照合未解決の新規買付を遮断する。"""
    assert not check(**{flag:True}).allowed

def test_stop_does_not_block_owned_exit():
    """STOPと新規件数制限で現物の手仕舞いまで止めない。"""
    p=proposal(side='SELL'); holdings={'6857':Record(qty=100,avg_price=1000,stop_order=True)}
    r=check(p,ledger_view=view(positions=holdings),stop_flag=True,new_sent_today=2)
    assert r.allowed and r.category=='EXIT'

@pytest.mark.parametrize('qty',[0,99])
def test_sell_cannot_exceed_owned(qty):
    """保有不足のSELLを空売りとして通さない。"""
    assert not check(proposal(side='SELL'),ledger_view=view(positions={'6857':Record(qty=qty,avg_price=1000,stop_order=False)})).allowed

@pytest.mark.parametrize('available,allowed',[(100200,True),(100199,False),(-1,False)])
def test_available_boundary(available,allowed):
    """費用余裕込み予約額の一円境界と負の余力を検証する。"""
    assert check(ledger_view=view(available=available)).allowed is allowed

def test_second_candidate_includes_first_reservation():
    """一件目の予約を無視して二件目へ同じ現金を配分しない。"""
    assert check(ledger_view=view(cash=150000,available=150000)).allowed
    assert not check(ledger_view=view(cash=150000,reserved=100200,available=49800)).allowed

@pytest.mark.parametrize('sent,allowed',[(0,True),(1,True),(2,False),(3,False)])
def test_new_daily_limit(sent,allowed):
    """三件目の候補が日次二件制限を超えない。"""
    assert check(new_sent_today=sent).allowed is allowed

def test_position_count_includes_reserved_names():
    """未約定の銘柄を保有数上限から落とさない。"""
    holdings={str(i):Record(qty=100,avg_price=100,stop_order=False) for i in range(4)}
    assert not check(ledger_view=view(positions=holdings,reserved_positions={'9999':10000})).allowed

def test_existing_holding_counts_toward_concentration():
    """同銘柄の保有額を含めず追加購入を許可する誤りを検出する。"""
    holdings={'6857':Record(qty=200,avg_price=1000,stop_order=True)}
    assert not check(ledger_view=view(positions=holdings)).allowed

def test_weight_exact_boundary():
    """費用余裕を含む集中度ちょうどの許可境界を確認する。"""
    assert check(equity=400800,peak_equity=400800).allowed
    assert not check(equity=400799,peak_equity=400799).allowed

@pytest.mark.parametrize('equity,allowed',[(900001,True),(900000,False),(899999,False)])
def test_drawdown_boundary(equity,allowed):
    """DDが上限に到達した瞬間に新規候補を停止する。"""
    assert check(equity=equity).allowed is allowed

def test_confidence_and_risk_words_are_not_gates():
    """低確信度や否定文中のブロック単語を拒否条件に使わない。"""
    p=proposal(); vs=[replace(v,confidence=0.0,risks=('決算直前ではない','規制銘柄ではない')) for v in votes(p)]
    assert check(p,verdicts=vs).allowed

def test_all_failure_reasons_are_reported():
    """最初の一条件だけで打ち切り他の停止理由を隠さない。"""
    r=check(stop_flag=True,unresolved_unconfirmed=True,new_sent_today=2)
    assert not r.allowed and len(r.reasons)>=3 and all(isinstance(s,str) and s for s in r.reasons)

@pytest.mark.parametrize('pnl,allowed',[(-19999,True),(-20000,False),(-20001,False)])
def test_daily_loss_boundary(pnl,allowed):
    """当日開始資産の2%損失ちょうどから新規候補を止める（入力型提案）。"""
    assert check(ledger_view=view(daily_pnl=pnl)).allowed is allowed

def test_exit_still_requires_double_approval():
    """STOP例外を利用して片方しか承認しない売却を通さない。"""
    p=proposal(side='SELL'); holdings={'6857':Record(qty=100,avg_price=1000,stop_order=True)}
    assert not check(p,ledger_view=view(positions=holdings),stop_flag=True,verdicts=votes(p)[:1]).allowed

def test_sell_no_cash_reservation():
    """現金がなくても保有株を売却でき、売却に買付資金を予約しない。"""
    p=proposal(side='SELL'); holdings={'6857':Record(qty=100,avg_price=1000,stop_order=True)}
    r=check(p,ledger_view=view(cash=0,available=0,positions=holdings))
    assert r.allowed and r.reserve_amount==0
