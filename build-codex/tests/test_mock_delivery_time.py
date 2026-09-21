"""A mock send must not precede its durable queue or previous send attempt."""
from datetime import timedelta

import pytest

from test_mock_delivery import setup, ready, deliver, box, NOW, RunError, offline


@pytest.mark.parametrize('early', [NOW.replace(hour=6, minute=0), NOW-timedelta(minutes=1)])
def test_delivery_before_queue_observation_is_rejected(setup, early):
    home, key = ready(setup)
    with box(home) as notifier:
        before = notifier.get_entry(key)
    with pytest.raises((RunError, ValueError)):
        deliver(home, now=early)
    with box(home) as notifier:
        assert notifier.get_entry(key) == before
        assert notifier.status(now=NOW)['monthly_used'] == 0
    assert not (home/'runs'/str(NOW.date())/'run1'/'mock_delivery.json').exists()


@pytest.mark.parametrize('first_result', ['timeout', 'failure'])
def test_retry_cannot_precede_previous_attempt(setup, first_result):
    home, key = ready(setup)
    first_at = NOW+timedelta(minutes=2)
    deliver(home, now=first_at, stub_results=[first_result])
    report_path = home/'runs'/str(NOW.date())/'run1'/'mock_delivery.json'
    prior_report = report_path.read_bytes()
    with box(home) as notifier:
        before = notifier.get_entry(key)
    with pytest.raises((RunError, ValueError)):
        deliver(home, now=NOW+timedelta(minutes=1))
    with box(home) as notifier:
        assert notifier.get_entry(key) == before
    assert report_path.read_bytes() == prior_report


def test_retry_at_same_instant_is_not_clock_reversal(setup):
    home, key = ready(setup)
    deliver(home, stub_results=['timeout'])
    deliver(home, stub_results=['success'])
    with box(home) as notifier:
        row = notifier.get_entry(key)
        assert row['state'] == 'SENT'
        assert len(row['attempts']) == 2
