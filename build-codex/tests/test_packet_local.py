"""Additional packet boundary and command-line integration checks."""
import json
import sys
from dataclasses import replace
from datetime import date, datetime

import pytest
from aitrader.packet import build_proposals, packet_hash, render_packet
from aitrader.db import init, connect


def args():
    return dict(candidates=[{'code':'6857','side':'BUY','strategy':'margin_bucket_long','strategy_version':'v1','reason':'試験'}],
                as_of=date(2026,9,4),prev_close={'6857':1000},lot_sizes={'6857':100},events={},
                snapshot_id='snapshot-1',policy_version='policy-1',budget_per_name=250000)


@pytest.mark.parametrize('budget',[-1,1.5,True,float('nan')])
def test_invalid_budget(budget):
    """負数や整数ではない予算を拒否する。"""
    kw=args(); kw['budget_per_name']=budget
    with pytest.raises(ValueError): build_proposals(**kw)


def test_supplied_calendar_skips_holiday():
    """月曜が休場なら明示カレンダーにある火曜を執行日とする。"""
    p=build_proposals(**args(),business_days=[date(2026,9,8)])[0]
    assert p.expires_at.date()==date(2026,9,8)


def test_empty_calendar_fails_closed():
    """カレンダーの範囲不足を平日推定で補わない。"""
    with pytest.raises(ValueError,match='カレンダー'): build_proposals(**args(),business_days=[])


def test_duplicate_candidate_fails():
    """重複候補へ別IDをつけて二重承認を作らない。"""
    kw=args(); kw['candidates']*=2
    with pytest.raises(ValueError,match='重複'): build_proposals(**kw)


def test_candidate_order_does_not_change_ids():
    """候補の入力順序が変わってもIDとハッシュが変わらない。"""
    kw=args(); kw['candidates'].append({**kw['candidates'][0],'code':'1300'})
    kw['prev_close']['1300']=1000; kw['lot_sizes']['1300']=100
    baseline=build_proposals(**kw)
    kw['candidates'].reverse()
    assert build_proposals(**kw)==baseline


@pytest.mark.parametrize('field',['prev_close','lot_sizes'])
def test_missing_required_instrument_data(field):
    """価格や単元の欠損を推測して補わない。"""
    kw=args(); kw[field]={}
    with pytest.raises(ValueError): build_proposals(**kw)


def test_sell_not_sized_from_buy_budget():
    """保有数が不明なSELLを買付予算から生成しない。"""
    kw=args(); kw['candidates'][0]['side']='SELL'
    with pytest.raises(ValueError,match='BUY以外'): build_proposals(**kw)


def test_naive_expiry_cannot_be_hashed():
    """タイムゾーンがない期限を暗黙のローカル時刻と解釈しない。"""
    p=build_proposals(**args())[0]
    with pytest.raises(ValueError): packet_hash(replace(p,expires_at=datetime(2026,9,7,8,59)))


def test_modified_proposal_cannot_render_with_old_hash():
    """数量を変更した候補を古いハッシュのままAIへ送らない。"""
    p=build_proposals(**args())[0]
    with pytest.raises(ValueError,match='hash'): render_packet(replace(p,qty=300),{})


@pytest.mark.parametrize('context',[{'claude_verdict':'APPROVE'},{'data':{'verdicts':[]}}])
def test_other_judgment_cannot_leak(context):
    """相手AIの結論を参照資料に紛れ込ませない。"""
    with pytest.raises(ValueError,match='他AI'): render_packet(build_proposals(**args())[0],context)


def test_nonfinite_reference_not_serialized():
    """JSON互換でない無限大を資料に通さない。"""
    with pytest.raises(ValueError): render_packet(build_proposals(**args())[0],{'price':float('inf')})


def test_snapshot_isolated_from_input_mutation():
    """呼び出し元がイベント辞書を後から編集してもパケット内容を変えない。"""
    kw=args(); kw['events']={'6857':{'next_earnings_date':None,'margin_regulated':False}}
    p=build_proposals(**kw)[0]; kw['events']['6857']['margin_regulated']=True
    assert p.events['margin_regulated'] is False


def test_packets_cli(tmp_path,monkeypatch,capsys):
    """CLIが実データを送信せずUNKNOWN付きProposalをJSONで出力する。"""
    from aitrader.__main__ import main
    init(tmp_path)
    with connect(tmp_path) as con:
        con.execute("INSERT INTO prices_daily VALUES ('6857','2026-09-04',1000,1000,1000,1000,100,100000,1)")
        con.execute("INSERT INTO calendar VALUES ('2026-09-08',true)")
        con.execute("INSERT INTO provenance VALUES ('data_mode','synthetic')")
    monkeypatch.setenv('AI_TRADER_HOME',str(tmp_path))
    monkeypatch.setattr('aitrader.packet_cli.run_signals',lambda *a:args()['candidates'])
    monkeypatch.setattr(sys,'argv',['aitrader','packets','--strategy','margin_bucket_long','--as-of','2026-09-04'])
    main(); output=capsys.readouterr(); rows=json.loads(output.out)
    assert len(rows)==1 and rows[0]['expires_at']=='2026-09-08T08:59:00+09:00'
    assert rows[0]['qty']==200 and rows[0]['events']['margin_regulated']=='UNKNOWN'
    assert len(rows[0]['snapshot_id'])==64
    assert '研究用' in output.err
