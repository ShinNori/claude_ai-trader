"""v0.4.0 independent review. Synthetic ledgers, stub transport, no network."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta, timezone
import json
from pathlib import Path
import sys
import threading
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'ops'),str(ROOT/'common/tests/phase2')]
import test_notify as n
import test_judges as j
from aitrader_ops import notify, judges
from aitrader_ops.models import compute_packet_hash
from dataclasses import replace
ledger=n.ledger
offline=n.offline
NOW=n.NOW

@pytest.fixture
def make_notifier(tmp_path,ledger):
    opened=[]
    def make(results=(),budget=200):
        settings=dict(n.SETTINGS,line=dict(n.SETTINGS['line'],monthly_budget=budget))
        obj=notify.Notifier(ledger=ledger,state_path=tmp_path/'outbox.sqlite',settings=settings,stub_results=list(results))
        opened.append(obj); return obj
    yield make
    for obj in opened: obj.close()

def risk(obj,key='risk',at=NOW):
    return obj.enqueue(key=key,kind='RISK',message=n.message('RISK'),proposal_id=None,now=at)

@pytest.mark.parametrize('location',['reason','packet','stderr'])
def test_v01_no_secret_in_any_decision_log(tmp_path,monkeypatch,location):
    secret='dummy-private-unique-value'
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN',secret)
    body=j.packet(); answer=j.answer(); response=j.record('codex')
    if location=='reason': response=j.record('codex',j.answer(reason=secret))
    elif location=='packet':
        parsed=json.loads(body); parsed['review_context']=secret; body=json.dumps(parsed,ensure_ascii=False)
    else: response['stderr']=secret
    j.run(tmp_path,'codex',response,packet=body)
    logs=''.join(p.read_text(encoding='utf-8') for p in (tmp_path/'decisions').glob('*.jsonl'))
    assert secret not in logs

def test_v02_sending_crash_is_recoverable(make_notifier,monkeypatch):
    obj=make_notifier(['success']); risk(obj)
    def crash(): raise RuntimeError('simulated crash after claim')
    monkeypatch.setattr(obj,'_send_stub',crash)
    with pytest.raises(RuntimeError): obj.flush(now=NOW)
    recovered=make_notifier(['success'])
    results=recovered.flush(now=NOW+timedelta(seconds=1))
    assert results or recovered.status(now=NOW)['alerts'], 'SENDING row disappeared from both recovery and alerts'
    assert recovered.status(now=NOW)['monthly_used']==1

def test_v03_parallel_budget_reservation_is_atomic(make_notifier,monkeypatch):
    first=make_notifier(['success'],budget=1); second=make_notifier(['success'],budget=1)
    risk(first,'one'); risk(first,'two')
    entered=threading.Event(); release=threading.Event(); sent=[]
    def paused_send():
        sent.append('first')
        entered.set()
        if not release.wait(5): raise RuntimeError('test synchronization timeout')
        return 'success'
    monkeypatch.setattr(first,'_send_stub',paused_send)
    def second_send():
        sent.append('second'); return 'success'
    monkeypatch.setattr(second,'_send_stub',second_send)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(first.flush,now=NOW)
        try:
            assert entered.wait(5)
            second.flush(now=NOW)
        finally: release.set()
        future.result(timeout=5)
    assert len(sent)<=1, f'budget=1 but sends={sent}'
    assert second.status(now=NOW)['monthly_used']==len(sent)

def test_v04_expired_unknown_keeps_budget_and_uncertainty(make_notifier):
    obj=make_notifier(['timeout']); n.enqueue(obj)
    assert obj.flush(now=NOW)[0]['state']=='UNKNOWN'
    result=obj.flush(now=NOW.replace(minute=16))
    assert obj.status(now=NOW)['monthly_used']==1
    assert result[0]['state']=='UNKNOWN'

def test_v05_retry_failure_does_not_erase_prior_unknown(make_notifier):
    obj=make_notifier(['timeout','failure']); risk(obj)
    obj.flush(now=NOW)
    result=obj.flush(now=NOW+timedelta(seconds=1))
    assert obj.status(now=NOW)['monthly_used']==1
    assert result[0]['state']=='UNKNOWN'

def test_v06_old_daily_notification_not_sent_in_new_month(make_notifier):
    obj=make_notifier(['success']); old=NOW.replace(day=30,month=8)
    obj.enqueue(key='daily-old',kind='RECONCILE',message=n.message('RECONCILE'),proposal_id=None,now=old)
    result=obj.flush(now=NOW.replace(day=1))
    assert all(x['state']!='SENT' for x in result)

def test_v07_late_stop_does_not_reverse_newer_resume(make_notifier):
    obj=make_notifier()
    assert n.receive(obj,[n.event('STOP','s',at=NOW.replace(hour=9))],now=NOW.replace(hour=9))['results'][0]['status']=='STOPPED'
    assert n.receive(obj,[n.event('RESUME','r',at=NOW.replace(hour=10))],now=NOW.replace(hour=10),reconciled_at=NOW.replace(hour=10))['results'][0]['status']=='RESUMED'
    n.receive(obj,[n.event('STOP','old-s',at=NOW.replace(hour=8))],now=NOW.replace(hour=11))
    assert not obj.is_stopped()

@pytest.mark.parametrize('field',['message','postback'])
def test_v08_malformed_event_shape_does_not_abort_batch(make_notifier,field):
    obj=make_notifier(); bad=n.event(event_id='bad'); bad[field]=['not-an-object']
    result=n.receive(obj,[bad,n.event(event_id='good')])
    assert result['http_status']==200
    assert result['results'][0]['status']=='INVALID'
    assert result['results'][1]['status']=='APPLIED'

def test_v09_unicode_signature_rejected_without_exception(make_notifier):
    assert n.receive(make_notifier(),[],signature='署名ではない')['http_status']==401

@pytest.mark.parametrize('action',['STOP','RESUME'])
def test_v10_future_control_rejected(make_notifier,action):
    obj=make_notifier()
    result=n.receive(obj,[n.event(action,at=NOW.replace(hour=11))],now=NOW.replace(hour=10),reconciled_at=NOW.replace(hour=10))
    assert result['results'][0]['status']=='INVALID'

@pytest.mark.parametrize('judge',['claude','codex'])
@pytest.mark.parametrize('changes',[{'decision':'approve'},{'confidence':'0.5'},{'risks':[{'qty':100}]},{'reason':{'qty':100}},{'DECISION':'APPROVE'}])
def test_v11_response_schema_rejects_nested_and_case_variants(tmp_path,judge,changes):
    result=j.run(tmp_path,judge,j.record(judge,j.answer(**changes)))
    assert result['failure']=='malformed' and result['verdict'].decision=='INVALID'

@pytest.mark.parametrize('minute',[5,30])
@pytest.mark.parametrize('offset',[0,1])
def test_v12_earlier_expiry_and_utc_deadline(tmp_path,minute,offset):
    p=n.proposal(expires_at=NOW.replace(minute=minute))
    from dataclasses import asdict
    body=json.loads(j.packet()); body['proposal']=asdict(p)
    body=json.dumps(body,default=str)
    deadline=NOW.replace(minute=min(minute,15))+timedelta(microseconds=offset)
    result=j.run(tmp_path,'codex',j.record('codex',j.answer(body=body)),packet=body,received_at=deadline.astimezone(timezone.utc))
    assert result['failure']==('deadline' if offset else None)

@pytest.mark.parametrize('template,valid',[
    ('https://WWW.RAKUTEN-SEC.CO.JP/path/@other?code={code}',True),
    ('https://www.rakuten-sec.co.jp./?code={code}',True),
    ('https://www.rakuten-sec.co.jp:443/?code={code}',True),
    ('https://rakuten-sec.co.jp.evil.invalid/?code={code}',False),
    ('https://ｍember.rakuten-sec.co.jp/?code={code}',False),
])
def test_v13_broker_link_boundaries(template,valid):
    if valid: assert notify.broker_link(template,'6857')
    else:
        with pytest.raises(ValueError): notify.broker_link(template,'6857')

def test_v14_needs_details_then_new_event_and_late_ordered(make_notifier,ledger):
    obj=make_notifier(); before=ledger.cash()
    assert n.receive(obj,[n.event('PARTIAL','need')])['results'][0]['status']=='NEEDS_DETAILS'
    assert ledger.cash()==before
    assert n.receive(obj,[n.event('PARTIAL','fill',qty=40,price=1000,fee=0)])['results'][0]['status']=='APPLIED'
    assert n.receive(obj,[n.event('FILLED','over',qty=100,price=1000,fee=0)])['results'][0]['status']=='INVALID'
    assert n.receive(obj,[n.event('FILLED','rest',qty=60,price=1000,fee=0)])['results'][0]['status']=='APPLIED'
    assert n.receive(obj,[n.event('ORDERED','late')])['results'][0]['status']=='IGNORED'
    assert ledger.cash()==before-100000

def test_v15_replay_modified_hash_rejected_and_file_unchanged(tmp_path):
    record=j.record('codex',j.answer(packet_hash='0'*64))
    path=tmp_path/'record.json'; path.write_text(json.dumps(record),encoding='utf-8'); before=path.read_bytes()
    assert j.run(tmp_path,'codex',replay_path=path)['failure']=='hash_mismatch'
    assert path.read_bytes()==before

@pytest.mark.parametrize('judge',['claude','codex'])
def test_v16_cli_plan_stays_pure(tmp_path,judge):
    plan=judges.build_cli_request(judge=judge,packet=j.packet(),model='fixture',work_dir=tmp_path/'absent',prompt_version='review-v1')
    assert plan['shell'] is False and plan['network_access'] is False
    assert not (tmp_path/'absent').exists()
    with pytest.raises(ValueError): j.run(tmp_path,judge,transport='cli')
    if judge=='claude': assert plan['argv'][plan['argv'].index('--tools')+1]==''
    else: assert '--output-schema' in plan['argv'] and '--output-last-message' in plan['argv']

@pytest.mark.parametrize('delta,expected',[(timedelta(hours=24),'SENT'),(timedelta(hours=24,microseconds=1),'UNKNOWN')])
def test_v17_unknown_retry_window(make_notifier,delta,expected):
    obj=make_notifier(['timeout','success']); risk(obj)
    first=obj.flush(now=NOW)[0]; later=obj.flush(now=NOW+delta)[0]
    assert later['state']==expected and later['retry_key']==first['retry_key']
    if expected=='UNKNOWN': assert 'RETRY_EXPIRED' in obj.status(now=NOW+delta)['alerts']

def test_v18_jst_month_boundary_budget(make_notifier):
    obj=make_notifier(['success','success'],budget=1)
    end=NOW.replace(day=30,hour=23,minute=59,second=59)
    risk(obj,'sept',end); assert obj.flush(now=end)[0]['state']=='SENT'
    start=end+timedelta(seconds=1)
    risk(obj,'oct',start); assert obj.flush(now=start.astimezone(timezone.utc))[0]['state']=='SENT'
    assert obj.status(now=end)['monthly_used']==obj.status(now=start)['monthly_used']==1

@pytest.mark.parametrize('kind',['RISK','RECONCILE'])
def test_v19_stop_does_not_block_non_new_messages(make_notifier,kind):
    obj=make_notifier(['success']); obj.stop(now=NOW)
    obj.enqueue(key='control',kind=kind,message=n.message(kind),proposal_id=None,now=NOW)
    assert obj.flush(now=NOW)[0]['state']=='SENT'

@pytest.mark.parametrize('mode',['newline','bom','uppercase'])
def test_v20_signature_bytes_not_normalized(make_notifier,mode):
    import base64,hashlib,hmac
    obj=make_notifier(); body=b'{"events":[]}'
    signature=base64.b64encode(hmac.new(n.SECRET.encode(),body,hashlib.sha256).digest()).decode()
    if mode=='newline': body+=b'\n'
    elif mode=='bom': body=b'\xef\xbb\xbf'+body
    else: signature=signature.upper()
    assert n.receive(obj,[],signature=signature,body=body)['http_status']==401

@pytest.mark.parametrize('change',[{'stdout':[]},{'exit_code':True},{'elapsed_seconds':float('nan')},{'final_message':{}}])
def test_v21_bad_record_types_are_invalid(tmp_path,change):
    assert j.run(tmp_path,'codex',j.record('codex',**change))['failure']=='malformed'
