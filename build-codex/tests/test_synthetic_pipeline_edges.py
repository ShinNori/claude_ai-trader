"""Minimal real SQL strategy input for stale-price pipeline review."""
from datetime import date, timedelta
import json

import pytest
from test_notification_plan import offline
from aitrader.db import init, connect
from aitrader.packet_cli import generate
from aitrader.runner import initialize_mock, mock_verdicts, normalize_proposal, run, Valuation
from aitrader.mock_demo import NOW, DAY


def market(tmp_path, count=5):
    home=tmp_path/'market'
    initialize_mock(home,1_000_000,[],NOW-timedelta(days=1))
    init(home)
    asof=DAY-timedelta(days=1)
    with connect(home) as con:
        con.execute("INSERT INTO provenance VALUES ('data_mode','synthetic')")
        con.executemany('INSERT INTO calendar VALUES (?,?)',[(asof,True),(DAY,True)])
        for n in range(count):
            code=str(1000+n)
            con.execute('INSERT INTO listed VALUES (?,?,?,?,?)',
                        [code,'synthetic','prime','synthetic',date(2020,1,1)])
            for i in range(60):
                con.execute('INSERT INTO prices_daily(code,date,close,turnover) VALUES(?,?,?,?)',
                            [code,asof-timedelta(days=i+1),1000.,200_000_000.])
            for i in range(5):
                d=asof-timedelta(days=i*7)
                con.execute('INSERT INTO margin_weekly VALUES(?,?,?,?,?)',
                            [code,d,d,100.+i*100.,10.])
    # No generator or run_signals mocking: all 5 names meet real SQL ranking.
    events=home/'events.json'
    events.write_text(json.dumps({str(1000+n): {'next_earnings_date':None,'margin_regulated':False}
                                 for n in range(count)}),encoding='utf-8')
    return home,asof,events


def test_stale_price_cannot_be_approved_as_current_asof(tmp_path):
    home,asof,events=market(tmp_path)
    try:
        generated=generate(home,'margin_bucket_long',asof,events_path=events)
    except ValueError:
        return  # Rejecting stale generation is a valid fail-closed boundary.
    assert generated, 'Fixture must exercise a real candidate, not an empty ranking'
    ps=[normalize_proposal(p) for p in generated]
    result=run(home,'stale-review',DAY,ps,mock_verdicts(ps,'stale-review',NOW),NOW,
               Valuation(1_000_000,1_000_000))
    assert all(c['status']!='APPROVED' for c in result['candidates']), \
        'Previous-day price was labelled with current as_of and approved'


def test_fresh_price_real_generator_and_gate_approve(tmp_path):
    home,asof,events=market(tmp_path)
    with connect(home) as con:
        con.executemany('INSERT INTO prices_daily(code,date,close,turnover) VALUES(?,?,?,?)',
                        [(str(1000+n),asof,1000.,200_000_000.) for n in range(5)])
    generated=generate(home,'margin_bucket_long',asof,events_path=events)
    assert len(generated)==1
    ps=[normalize_proposal(p) for p in generated]
    result=run(home,'fresh-review',DAY,ps,mock_verdicts(ps,'fresh-review',NOW),NOW,
               Valuation(1_000_000,1_000_000))
    assert result['candidates'][0]['status']=='APPROVED'


def test_other_names_fresh_stale_name_not_backfilled(tmp_path):
    home,asof,events=market(tmp_path,count=6)
    with connect(home) as con:
        con.executemany('INSERT INTO prices_daily(code,date,close,turnover) VALUES(?,?,?,?)',
                        [(str(1000+n),asof,1000.,200_000_000.) for n in range(1,6)])
    generated=generate(home,'margin_bucket_long',asof,events_path=events)
    # The actual SQL 60-day liquidity window excludes code 1000 (59/60 rows).
    assert len(generated)==1
    assert generated[0]['code']=='1001'


def test_empty_ranking_does_not_hide_stale_market(tmp_path):
    home,asof,events=market(tmp_path,count=4)
    with pytest.raises(ValueError):
        generate(home,'margin_bucket_long',asof,events_path=events)
