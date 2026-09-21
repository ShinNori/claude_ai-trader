"""Persisted run time survives missing or unwritable observation reports."""
from pathlib import Path
from datetime import timedelta

import pytest

from test_mock_delivery import setup, ready, deliver, box, DAY, NOW, RunError, offline
from test_mock_reconciliation import reconcile


def report(home, filename):
    return home/'runs'/str(DAY)/'run1'/filename


def test_deadline_observation_survives_report_deletion(setup):
    home, key = ready(setup)
    deliver(home, now=NOW.replace(minute=15))
    report(home, 'mock_delivery.json').unlink()
    with pytest.raises(RunError):
        deliver(home, now=NOW.replace(minute=14))
    with box(home) as notifier:
        assert notifier.get_entry(key)['attempts'] == []
        assert notifier.get_entry(key)['month'] is None


def test_failed_report_still_prevents_clock_reversal(setup, monkeypatch):
    home, key = ready(setup)
    observed = NOW+timedelta(minutes=2)
    (home/'STOP').touch()
    with monkeypatch.context() as patch:
        import aitrader.runner as runner
        original = runner.os.replace

        def fail(source, target):
            if Path(target).name == 'mock_delivery.json':
                raise OSError('report unavailable')
            return original(source, target)
        patch.setattr(runner.os, 'replace', fail)
        with pytest.raises(OSError):
            deliver(home, now=observed)
    (home/'STOP').unlink()
    with pytest.raises(RunError):
        deliver(home, now=NOW+timedelta(minutes=1))
    assert deliver(home, now=observed)['simulated_sent_by_this_call'] is True
    with box(home) as notifier:
        assert len(notifier.get_entry(key)['attempts']) == 1


def test_stop_observation_survives_report_deletion(setup):
    home, key = ready(setup)
    (home/'STOP').touch()
    deliver(home, now=NOW+timedelta(minutes=2))
    report(home, 'mock_delivery.json').unlink()
    (home/'STOP').unlink()
    with pytest.raises(RunError):
        deliver(home, now=NOW+timedelta(minutes=1))
    with box(home) as notifier:
        assert notifier.get_entry(key)['attempts'] == []


def test_noop_reconciliation_clock_survives_report_deletion(setup):
    home, key = ready(setup)
    reconcile(home, now=NOW+timedelta(minutes=2))
    report(home, 'mock_reconciliation.json').unlink()
    with pytest.raises(RunError):
        deliver(home, now=NOW+timedelta(minutes=1))
    with box(home) as notifier:
        assert notifier.get_entry(key)['attempts'] == []


def test_clock_is_scoped_to_run(setup):
    from test_runner import proposal
    from aitrader.notification_plan import prepare_notifications
    from aitrader.notification_queue import enqueue_prepared_notifications
    from aitrader.mock_delivery import deliver_prepared_mock
    from test_mock_delivery import CONFIG, CONTEXTS
    home, execute = setup
    ready(setup)
    deliver(home, now=NOW.replace(minute=15))
    second = proposal('buy2', code='7203')
    execute([second], run_id='run2')
    contexts = {'buy2': dict(CONTEXTS['buy1'])}
    prepare_notifications(home, 'run2', DAY, contexts=contexts, settings=CONFIG)
    enqueue_prepared_notifications(home, 'run2', DAY, now=NOW, contexts=contexts, settings=CONFIG)
    result = deliver_prepared_mock(home, 'run2', DAY, now=NOW, contexts=contexts,
                                   settings=CONFIG, stub_results=['success'])
    assert result['simulated_sent_by_this_call'] is True
