"""Offline queue-only integration; no flush or new ledger reservations."""
import copy
import json
from contextlib import closing
from datetime import timedelta
import pytest
from test_notification_plan import setup, DAY, NOW, CONTEXTS, SETTINGS, offline, snapshot
from aitrader.notification_plan import prepare_notifications
from aitrader_ops.notify import Notifier
from aitrader_ops.ledger import Ledger
from aitrader.runner import RunError

CONFIG = dict(SETTINGS, line={'allowed_user_id':'fixture-recipient','monthly_budget':10})

def queue(home, **changes):
    from aitrader.notification_queue import enqueue_prepared_notifications
    args=dict(now=NOW,contexts=copy.deepcopy(CONTEXTS),settings=copy.deepcopy(CONFIG))
    args.update(changes)
    return enqueue_prepared_notifications(home,'run1',DAY,**args)

def prepared(setup):
    home,execute=setup
    execute()
    plan=prepare_notifications(home,'run1',DAY,contexts=CONTEXTS,settings=CONFIG)
    return home,plan

def test_queue_does_not_send_or_reserve(setup,monkeypatch):
    home,plan=prepared(setup)
    before=snapshot(home)
    monkeypatch.setattr(Notifier,'flush',lambda *a,**k: pytest.fail('flush forbidden'))
    receipt=queue(home)
    assert receipt['mode']=='mock' and receipt['delivery']=='NOT_SENT'
    assert receipt['entries'][0]['state']=='PENDING'
    assert snapshot(home)==before
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        with closing(Notifier(ledger=ledger,state_path=home/'notification.sqlite',settings=CONFIG)) as box:
            row=box.get_entry(plan['plans'][0]['key'])
            assert row['message']==plan['plans'][0]['message']
            assert row['attempts']==[] and row['month'] is None
            assert box.status(now=NOW)['monthly_used']==0

def test_retry_keeps_key_and_reservations(setup):
    home,plan=prepared(setup)
    first=queue(home)
    before=snapshot(home)
    again=queue(home)
    assert first['entries'][0]['key']==again['entries'][0]['key']
    assert snapshot(home)==before

@pytest.mark.parametrize('change',['recipient','context','tomorrow'])
def test_changed_inputs_rejected(setup,change):
    home,_=prepared(setup)
    args={}
    if change=='recipient': args['settings']=dict(CONFIG,line={'allowed_user_id':'other'})
    elif change=='context': args['contexts']={'buy1':{'name':'changed'}}
    else: args['now']=NOW+timedelta(days=1)
    with pytest.raises((RunError,ValueError)): queue(home,**args)
    assert not (home/'notification.sqlite').exists()

def test_unprepared_run_rejected(setup):
    home,execute=setup
    execute()
    with pytest.raises((RunError,ValueError)): queue(home)
    assert not (home/'notification.sqlite').exists()

def test_queue_receipt_loss_can_recover(setup):
    home,_=prepared(setup)
    first=queue(home)
    path=home/'runs'/str(DAY)/'run1'/'notification_queue.json'
    path.unlink()
    second=queue(home)
    assert path.is_file()
    assert second['entries'][0]['key']==first['entries'][0]['key']
