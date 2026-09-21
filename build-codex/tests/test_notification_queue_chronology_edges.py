"""Queue re-observation must not move its receipt before known history."""
import json
from datetime import timedelta

import pytest
from test_notification_queue import setup, prepared, queue, NOW, DAY, offline
from test_mock_delivery import deliver, box
from aitrader.runner import RunError


@pytest.mark.parametrize('history', ['created', 'receipt', 'attempt'])
def test_enqueue_reobservation_before_known_history_rejected(setup, history):
    home,plan=prepared(setup)
    key=plan['plans'][0]['key']
    later=NOW+timedelta(minutes=2)
    earlier=NOW+timedelta(minutes=1)
    queue(home,now=later if history=='created' else NOW)
    if history=='receipt':
        queue(home,now=later)
    elif history=='attempt':
        deliver(home,now=later,stub_results=['timeout'])
    receipt_path=home/'runs'/str(DAY)/'run1'/'notification_queue.json'
    receipt_before=receipt_path.read_bytes()
    with box(home) as notifier:
        entry_before=notifier.get_entry(key)
    with pytest.raises(RunError):
        queue(home,now=earlier)
    assert receipt_path.read_bytes()==receipt_before
    with box(home) as notifier:
        assert notifier.get_entry(key)==entry_before


def test_corrupt_early_receipt_cannot_bypass_attempt_time_delivery_guard(setup):
    home,plan=prepared(setup)
    key=plan['plans'][0]['key']
    queue(home,now=NOW)
    later=NOW+timedelta(minutes=2)
    earlier=NOW+timedelta(minutes=1)
    deliver(home,now=later,stub_results=['timeout'])
    path=home/'runs'/str(DAY)/'run1'/'notification_queue.json'
    receipt=json.loads(path.read_text(encoding='utf-8'))
    receipt['observed_at']=earlier.isoformat()
    path.write_text(json.dumps(receipt),encoding='utf-8')
    with pytest.raises(RunError):
        deliver(home,now=earlier,stub_results=['success'])
    with box(home) as notifier:
        entry=notifier.get_entry(key)
        assert entry['state']=='UNKNOWN'
        assert len(entry['attempts'])==1
