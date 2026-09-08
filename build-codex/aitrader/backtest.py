import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from .db import connect
from .strategies import get_strategy
from .report import render

TRADE_COLS = ['code','entry_date','entry_price','exit_date','exit_price','qty_yen','pnl_yen','pnl_pct','holding_days','strategy','reason']

def run(home, strategy, start, end, out_dir):
    if start > end:
        raise ValueError('start must not exceed end')
    st = get_strategy(strategy)
    with connect(home) as con:
        px = con.execute('SELECT code,date,open,close FROM prices_daily WHERE date<=? ORDER BY date,code',[end]).df()
        ix = con.execute("SELECT date,close FROM index_daily WHERE name='TOPIX' AND date<=? ORDER BY date",[end]).df()
        mode_row = con.execute("SELECT value FROM provenance WHERE key='data_mode'").fetchone()
        if mode_row is None:
            raise ValueError('Data provenance missing; load verified data first')
        days = sorted(px.date.unique())
        days = [pd.Timestamp(d) for d in days]
        active = [i for i,d in enumerate(days) if start<=d.date()<=end]
        if not active:
            raise ValueError('No prices in requested period')
        daily = {pd.Timestamp(d): f.set_index('code') for d,f in px.groupby('date')}
        benchmark = ix.set_index('date')['close'].reindex(pd.DatetimeIndex([days[i] for i in active]))
        if benchmark.isna().any() or (benchmark<=0).any():
            raise ValueError('Benchmark coverage incomplete')
        initial = 10_000_000.0
        cash = initial
        positions = {}
        trades, equity = [], []
        skipped, duplicates = 0, 0

        def close_position(code, price, i):
            nonlocal cash
            p = positions.pop(code)
            exit_price = float(price)*.999
            proceeds = p['shares']*exit_price
            cash += proceeds
            pnl = proceeds-p['qty_yen']
            trades.append(dict(code=code,entry_date=days[p['i']].date(),entry_price=p['entry_price'],
                               exit_date=days[i].date(),exit_price=exit_price,qty_yen=p['qty_yen'],
                               pnl_yen=pnl,pnl_pct=pnl/p['qty_yen'],holding_days=i-p['i'],
                               strategy=strategy,reason=p['reason']))

        for i in active:
            day, frame = days[i], daily[days[i]]
            # The first session in each ISO week; holidays use the next session.
            rebalance = i>0 and day.isocalendar()[:2] != days[i-1].isocalendar()[:2]
            if rebalance:
                previous = daily[days[i-1]]
                candidates = st.generate(days[i-1].date(),con)
                budget = cash/len(candidates) if candidates else 0
                for c in candidates:
                    if c.code in positions:
                        duplicates += 1
                        continue
                    if c.code not in frame.index or c.code not in previous.index:
                        continue
                    opening = float(frame.loc[c.code,'open'])
                    reference = float(previous.loc[c.code,'close'])
                    if not np.isfinite(opening) or opening<=0 or not np.isfinite(reference) or reference<=0:
                        raise ValueError('Invalid execution prices')
                    if opening>reference*(1+c.limit_pct):
                        skipped += 1
                        continue
                    if budget<=1e-8:
                        continue
                    price = opening*1.001
                    positions[c.code] = dict(i=i,entry_price=price,qty_yen=budget,shares=budget/price,
                                             due=i+c.holding_days,reason=c.reason,last_close=reference)
                    cash -= budget
            for code in list(positions):
                if code in frame.index:
                    closing = float(frame.loc[code,'close'])
                    if not np.isfinite(closing) or closing<=0:
                        raise ValueError('Invalid closing price')
                    positions[code]['last_close'] = closing
                    if i>=positions[code]['due'] or i==active[-1]:
                        close_position(code,closing,i)
                elif i>=positions[code]['due'] or i==active[-1]:
                    raise ValueError('Exit price missing; cannot invent a fill')
            value = sum(p['shares']*p['last_close'] for p in positions.values())
            equity.append(dict(date=day.date(),equity=cash+value,cash=cash,positions_value=value,
                               benchmark=initial*float(benchmark.loc[day])/float(benchmark.iloc[0])))

    eq, tr = pd.DataFrame(equity), pd.DataFrame(trades,columns=TRADE_COLS)
    returns = eq.equity.pct_change().fillna(eq.equity.iloc[0]/initial-1)
    br = eq.benchmark.pct_change().fillna(0)
    pnl = tr.pnl_yen.astype(float)
    losses = -pnl[pnl<0].sum()
    final = float(eq.equity.iloc[-1])
    dd = eq.equity / eq.equity.cummax().clip(lower=initial)-1
    yearly = {}
    before = initial
    for year,g in eq.groupby(pd.to_datetime(eq.date).dt.year):
        yearly[str(year)] = dict(return_=float(g.equity.iloc[-1]/before-1),
                                  trades=int(sum(pd.Timestamp(d).year==year for d in tr.entry_date)))
        yearly[str(year)]['return'] = yearly[str(year)].pop('return_')
        before = float(g.equity.iloc[-1])
    std = float(returns.std(ddof=1))
    correlation = float(returns.corr(br)) if std>0 and br.std()>0 else 0.0
    summary = dict(strategy=strategy,version=st.version,period={'from':str(start),'to':str(end)},
                   initial_capital=initial,final_equity=final,cagr=(final/initial)**(252/len(eq))-1,
                   sharpe=float(returns.mean()/std*np.sqrt(252)) if std>0 else 0.0,
                   max_drawdown=float(dd.min()),trades=len(tr),skipped_by_limit=skipped,
                   skipped_duplicate=duplicates,win_rate=float((pnl>0).mean()) if len(tr) else 0.0,
                   profit_factor=float(pnl[pnl>0].sum()/losses) if losses>0 else None,
                   avg_holding_days=float(tr.holding_days.mean()) if len(tr) else 0.0,
                   benchmark_cagr=float((eq.benchmark.iloc[-1]/initial)**(252/len(eq))-1),
                   benchmark_max_drawdown=float((eq.benchmark/eq.benchmark.cummax()-1).min()),
                   corr_to_benchmark=correlation,yearly=yearly,data_mode=mode_row[0],
                   generated_at=datetime.now(timezone.utc).isoformat())
    folder = Path(out_dir)/f'{strategy}_{st.version}'
    folder.mkdir(parents=True,exist_ok=True)
    tr.to_csv(folder/'trades.csv',index=False)
    eq.to_csv(folder/'equity.csv',index=False)
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    render(folder)
    return summary
