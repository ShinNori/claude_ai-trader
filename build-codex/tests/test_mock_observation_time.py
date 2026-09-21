"""No-op observations are chronological evidence even without queue mutation."""
from datetime import timedelta

import pytest
from test_notification_queue import setup, prepared, queue, CONFIG, NOW, DAY, CONTEXTS, offline
from test_mock_delivery import deliver, box
from aitrader.runner import RunError


def ready(setup):
    home,plan=prepared(setup)
    queue(home)
    return home,plan['plans'][0]['key']


def reconcile(home,now):
    from aitrader.mock_delivery import reconcile_prepared_mock
    return reconcile_prepared_mock(home,'run1',DAY,now=now,contexts=CONTEXTS,settings=CONFIG)


def test_deadline_observation_prevents_backdated_delivery(setup):
    home,key=ready(setup)
    first=deliver(home,now=NOW.replace(minute=16))
    assert first['entries'][0]['observation']=='SKIPPED_DEADLINE'
    with pytest.raises(RunError):
        deliver(home,now=NOW.replace(minute=11))
    with box(home) as notifier:
        assert notifier.get_entry(key)['state']=='PENDING'
        assert notifier.get_entry(key)['attempts']==[]


def test_noop_reconcile_observation_cannot_move_backwards(setup):
    home,_=ready(setup)
    reconcile(home,NOW+timedelta(minutes=2))
    with pytest.raises(RunError):
        reconcile(home,NOW+timedelta(minutes=1))


def test_stop_observation_prevents_backdated_delivery_after_stop_removed(setup):
    home,key=ready(setup)
    stop=home/'STOP'
    stop.write_text('synthetic stop',encoding='utf-8')
    first=deliver(home,now=NOW+timedelta(minutes=2))
    assert first['entries'][0]['observation']=='STOPPED'
    stop.unlink()
    with pytest.raises(RunError):
        deliver(home,now=NOW+timedelta(minutes=1))
    with box(home) as notifier:
        assert notifier.get_entry(key)['attempts']==[]


@pytest.mark.parametrize('source',['delivery','reconcile'])
def test_enqueue_cannot_precede_other_saved_observation(setup,source):
    home,_=ready(setup)
    later=NOW+timedelta(minutes=2)
    if source=='delivery':
        (home/'STOP').write_text('synthetic',encoding='utf-8')
        deliver(home,now=later)
    else:
        reconcile(home,later)
    with pytest.raises(RunError):
        queue(home,now=NOW+timedelta(minutes=1))


def test_same_observation_time_allows_noop_retries(setup):
    home,key=ready(setup)
    (home/'STOP').write_text('synthetic',encoding='utf-8')
    same=NOW+timedelta(minutes=2)
    deliver(home,now=same)
    reconcile(home,same)
    queue(home,now=same)
    again=deliver(home,now=same)
    assert again['attempted_by_this_call'] is False
    with box(home) as notifier:
        assert notifier.get_entry(key)['state']=='PENDING'
