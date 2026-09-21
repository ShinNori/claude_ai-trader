"""A STOP appearing after selection must still prevent the actual NEW attempt."""
from datetime import timedelta

import pytest
from test_notification_queue import setup, prepared, queue, NOW, offline
from test_mock_delivery import deliver, box
from aitrader_ops.notify import Notifier


@pytest.mark.parametrize('initial',['PENDING','UNKNOWN'])
def test_stop_created_between_selection_and_flush_blocks_attempt(setup,monkeypatch,initial):
    home,plan=prepared(setup)
    queue(home)
    key=plan['plans'][0]['key']
    if initial=='UNKNOWN':
        deliver(home,stub_results=['timeout'])
    with box(home) as notifier:
        before=notifier.get_entry(key)
        budget=notifier.status(now=NOW)['monthly_used']
        reserved=notifier.ledger.reserved()
    actual_flush=Notifier.flush
    def delayed_stop(self,**kwargs):
        (home/'STOP').write_text('arrived after selection',encoding='utf-8')
        return actual_flush(self,**kwargs)
    monkeypatch.setattr(Notifier,'flush',delayed_stop)
    result=deliver(home,now=NOW+timedelta(seconds=1),stub_results=['success'])
    assert result['attempted_by_this_call'] is False
    with box(home) as notifier:
        after=notifier.get_entry(key)
        assert after['state']==initial
        assert after['attempts']==before['attempts']
        assert after['retry_key']==before['retry_key']
        assert notifier.status(now=NOW)['monthly_used']==budget
        assert notifier.ledger.reserved()==reserved
