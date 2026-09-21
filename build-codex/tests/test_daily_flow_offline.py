"""Virtual daily flow across real generation, recorded review and queueing."""
from contextlib import closing

import pytest
from test_synthetic_pipeline_edges import market
from test_notification_plan import offline
from aitrader.db import connect
from aitrader.packet_cli import generate
from aitrader.mock_demo import NOW, DAY, _record
from aitrader.review_runner import run_reviewed_mock
from aitrader.notification_plan import prepare_notifications
from aitrader.notification_queue import enqueue_prepared_notifications
from aitrader.runner import RunError,Valuation
from aitrader_ops.ledger import Ledger


def generated(tmp_path,count=5):
    home,asof,events=market(tmp_path,count=count)
    with connect(home) as con:
        con.executemany('INSERT INTO prices_daily(code,date,close,turnover) VALUES(?,?,?,?)',
                        [(str(1000+n),asof,1000.,200_000_000.) for n in range(count)])
    return home,generate(home,'margin_bucket_long',asof,events_path=events)


def responses(proposals,received):
    result={}
    for p in proposals:
        answer=dict(proposal_id=p['proposal_id'],packet_hash=p['packet_hash'],
                    decision='APPROVE',risks=[],reason='synthetic',confidence=None)
        result[p['proposal_id']]={j:dict(record=_record(j,answer),received_at=received)
                                  for j in ('claude','codex')}
    return result


def finish(home,proposals,now):
    settings={'line':{'allowed_user_id':'synthetic-daily','monthly_budget':10}}
    contexts={p['proposal_id']:{'name':'synthetic'} for p in proposals}
    plan=prepare_notifications(home,'daily',DAY,contexts=contexts,settings=settings,include_status=True)
    receipt=enqueue_prepared_notifications(home,'daily',DAY,now=now,
                    contexts=contexts,settings=settings,include_status=True)
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        assert ledger.reserved()==0
    assert receipt['delivery']=='NOT_SENT'
    assert len(plan['plans'])==1 and plan['plans'][0]['kind']=='RECONCILE'
    assert all(e['state']=='PENDING' for e in receipt['entries'])


def test_real_candidates_cannot_start_review_at_0650(tmp_path):
    home,ps=generated(tmp_path)
    assert len(ps)==1
    early=NOW.replace(hour=6,minute=50)
    with pytest.raises(RunError):
        run_reviewed_mock(home,'daily',DAY,ps,responses(ps,early),early,
                          Valuation(1_000_000,1_000_000),started_at=early,market_context={})
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        assert ledger.reserved()==0
    assert not (home/'notification.sqlite').exists()
    assert not (home/'orchestration.sqlite').exists()


def test_before_cutoff_votes_processed_at_cutoff_only_status_notice(tmp_path):
    home,ps=generated(tmp_path)
    received=NOW.replace(minute=14,second=59)
    now=NOW.replace(minute=15)
    result=run_reviewed_mock(home,'daily',DAY,ps,responses(ps,received),now,
                            Valuation(1_000_000,1_000_000),
                            started_at=NOW.replace(minute=14,second=58),market_context={})
    assert result['status']=='REVIEW_INCOMPLETE'
    finish(home,ps,now)


def test_empty_real_ranking_results_in_no_signal_status_queue(tmp_path):
    home,ps=generated(tmp_path,count=4)
    assert ps==[]
    result=run_reviewed_mock(home,'daily',DAY,ps,{},NOW,Valuation(1_000_000,1_000_000),
                            started_at=NOW.replace(minute=9),market_context={})
    assert result['status']=='NO_SIGNAL'
    finish(home,ps,NOW)
