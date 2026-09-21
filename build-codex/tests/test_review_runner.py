"""Recorded responses -> judges -> runner -> actual mock ledger, offline."""
import json
from datetime import timedelta
import pytest
from test_runner import setup, proposal, NOW, DAY
from test_judges import offline, record
from aitrader.review_runner import run_reviewed_mock
from aitrader.runner import Valuation, RunError
from aitrader_ops.ledger import Ledger

offline = offline

def inputs(decision='APPROVE'):
    p = proposal()
    answer = dict(proposal_id=p.proposal_id, packet_hash=p.packet_hash,
                  decision=decision, risks=[], reason='fixture', confidence=None)
    return p, {p.proposal_id: {j: dict(record=record(j, answer), received_at=NOW)
                              for j in ('claude','codex')}}

def call(home, p, responses, now=NOW, **kwargs):
    return run_reviewed_mock(home, 'review1', DAY, [p], responses, now,
        Valuation(1000000,1000000), started_at=NOW-timedelta(seconds=1),
        market_context={}, **kwargs)

@pytest.mark.parametrize('decision,expected', [('APPROVE','APPROVED'),('REJECT','REJECTED'),('ABSTAIN','REJECTED')])
def test_records_reach_gate(setup, decision, expected):
    home, _ = setup
    p, responses = inputs(decision)
    result = call(home,p,responses)
    assert result['candidates'][0]['status'] == expected
    logs = list((home/'runs'/str(DAY)/'review1'/'reviews').glob('*.jsonl'))
    rows = [json.loads(line) for f in logs for line in f.read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 2 and rows[0]['packet'] == rows[1]['packet']
    ledger = Ledger(home/'ledger.sqlite')
    try:
        assert (ledger.reserved() > 0) == (decision == 'APPROVE')
    finally:
        ledger.close()

def test_missing_judge_is_incomplete(setup):
    home,_ = setup
    p,responses=inputs()
    responses[p.proposal_id].pop('claude')
    assert call(home,p,responses)['status'] == 'REVIEW_INCOMPLETE'

@pytest.mark.parametrize('change', ['malformed','timeout','hash'])
def test_bad_record_never_reserves(setup, change):
    home,_=setup
    p,responses=inputs()
    r=responses[p.proposal_id]['codex']['record']
    if change == 'malformed': r['final_message']='not json'
    elif change == 'timeout': r['elapsed_seconds']=121
    else:
        a=json.loads(r['final_message']);a['packet_hash']='0'*64;r['final_message']=json.dumps(a)
    assert call(home,p,responses)['candidates'][0]['status'] != 'APPROVED'
    ledger=Ledger(home/'ledger.sqlite')
    try: assert ledger.reserved()==0
    finally: ledger.close()

@pytest.mark.parametrize('change', ['unknown','future'])
def test_envelope_rejected_before_logging(setup, change):
    home,_=setup
    p,responses=inputs()
    if change=='unknown': responses[p.proposal_id]['other']={}
    else: responses[p.proposal_id]['codex']['received_at']=NOW+timedelta(seconds=1)
    with pytest.raises(RunError): call(home,p,responses)
    assert not (home/'runs').exists()

def test_cutoff_uses_stricter_runner_deadline(setup):
    home,_=setup
    p,responses=inputs()
    assert call(home,p,responses,now=NOW.replace(minute=15))['status']=='REVIEW_INCOMPLETE'

def test_repeated_run_does_not_duplicate_reservation(setup):
    home,_=setup
    p,responses=inputs()
    first=call(home,p,responses)
    ledger=Ledger(home/'ledger.sqlite')
    try: seq=ledger.seq()
    finally: ledger.close()
    assert call(home,p,responses)==first
    ledger=Ledger(home/'ledger.sqlite')
    try: assert ledger.seq()==seq
    finally: ledger.close()
