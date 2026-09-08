from datetime import date
from types import SimpleNamespace
import pandas as pd
from aitrader.db import init,connect
from aitrader.backtest import run
from aitrader.strategies.base import Candidate

def test_closing_proceeds_cannot_fund_same_morning(monkeypatch,tmp_path):
    init(tmp_path)
    days = pd.bdate_range('2026-02-27','2026-03-10')
    px = pd.DataFrame([dict(code=code,date=d,open=100.,high=100.,low=100.,close=100.,
                           volume=100.,turnover=10000.,adj_factor=1.) for d in days for code in ('A','B')])
    ix = pd.DataFrame([dict(name='TOPIX',date=d,close=100.) for d in days])
    with connect(tmp_path) as con:
        for table,frame in [('prices_daily',px),('index_daily',ix)]:
            con.register('incoming',frame); con.execute(f'INSERT INTO {table} SELECT * FROM incoming'); con.unregister('incoming')
        con.execute("INSERT INTO provenance VALUES ('data_mode','synthetic')")
    def generate(as_of,con):
        code = 'A' if as_of<date(2026,3,6) else 'B'
        return [Candidate(code,'BUY',1,1,'test','margin_bucket_long','v1',as_of,5,.005)]
    monkeypatch.setattr('aitrader.backtest.get_strategy',lambda name:SimpleNamespace(version='v1',generate=generate))
    summary = run(tmp_path,'margin_bucket_long',date(2026,3,2),date(2026,3,10),tmp_path/'out')
    trades = pd.read_csv(tmp_path/'out/margin_bucket_long_v1/trades.csv')
    assert summary['trades']==1
    assert trades.code.tolist()==['A']
    assert trades.exit_date.tolist()==['2026-03-09']
