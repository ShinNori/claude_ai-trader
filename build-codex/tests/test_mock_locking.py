"""Two-connection lock probes; standalone Notifier is outside runner locking."""
import sqlite3
from contextlib import closing

import pytest

from test_mock_delivery import setup, offline, ready, deliver, box, NOW, DAY, CONTEXTS, CONFIG
from test_notification_queue import queue
from aitrader.mock_delivery import reconcile_prepared_mock
from aitrader_ops.notify import Notifier


def assert_runner_locked(home):
    with closing(sqlite3.connect(home/'runner-lock.sqlite', isolation_level=None, timeout=0)) as contender:
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            contender.execute('BEGIN IMMEDIATE')


def assert_runner_available(home):
    with closing(sqlite3.connect(home/'runner-lock.sqlite', isolation_level=None, timeout=0)) as contender:
        contender.execute('BEGIN IMMEDIATE')
        contender.execute('ROLLBACK')


@pytest.mark.parametrize('operation,method', [('queue', 'enqueue'), ('deliver', 'flush'),
                                             ('reconcile', 'reconcile_sent')])
def test_main_operations_hold_lock_through_notifier_operation(setup, monkeypatch, operation, method):
    home, key = ready(setup)
    original = getattr(Notifier, method)
    calls = []

    def checked(notifier, *args, **kwargs):
        assert_runner_locked(home)
        calls.append(True)
        return original(notifier, *args, **kwargs)

    monkeypatch.setattr(Notifier, method, checked)
    if operation == 'queue':
        queue(home)
    elif operation == 'deliver':
        deliver(home)
    else:
        reconcile_prepared_mock(home, 'run1', DAY, now=NOW, contexts=CONTEXTS, settings=CONFIG)
    assert calls
    assert_runner_available(home)


def test_operation_exception_releases_runner_lock(setup, monkeypatch):
    home, key = ready(setup)

    def failure(notifier, **kwargs):
        assert_runner_locked(home)
        raise OSError('injected locked operation failure')

    monkeypatch.setattr(Notifier, 'flush', failure)
    with pytest.raises(OSError, match='injected locked operation'):
        deliver(home)
    assert_runner_available(home)
    with box(home) as notifier:
        assert notifier.get_entry(key)['attempts'] == []


def test_standalone_notifier_does_not_participate_in_runner_lock(setup):
    """Document unsupported mixing: direct ops writers bypass the main lock."""
    home, key = ready(setup)
    with closing(sqlite3.connect(home/'runner-lock.sqlite', isolation_level=None)) as owner:
        owner.execute('BEGIN IMMEDIATE')
        with box(home) as notifier:
            notifier.stub_results = ['success']
            result = notifier.flush(now=NOW, keys=[key])
            assert result[0]['state'] == 'SENT'
            assert len(notifier.get_entry(key)['attempts']) == 1
        owner.execute('ROLLBACK')
