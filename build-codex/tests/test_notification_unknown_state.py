"""Unknown persistent state must not be presented as a harmless no-op."""
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from aitrader.mock_delivery import deliver_prepared_mock, reconcile_prepared_mock
from aitrader.runner import RunError
from test_notification_persisted_integrity import (
    _files, _ledger, _enqueue, _prepare_legacy, _prepare_managed,
    CONFIG, CONTEXTS, DAY, NOW, setup,
)


@pytest.mark.parametrize('mode', ['legacy', 'managed'])
@pytest.mark.parametrize('action', ['enqueue', 'deliver', 'reconcile'])
def test_unknown_state_does_not_advance_clock_or_create_report(tmp_path, setup, mode, action):
    home = _prepare_legacy(setup) if mode == 'legacy' else _prepare_managed(tmp_path)
    with closing(sqlite3.connect(home/'notification.sqlite')) as db:
        db.execute("UPDATE outbox SET state='BOGUS'")
        db.commit()
    before_files, before_ledger = _files(home), _ledger(home)
    now = NOW + timedelta(minutes=1)
    with pytest.raises(RunError):
        if action == 'enqueue':
            _enqueue(home, now)
        elif action == 'deliver':
            deliver_prepared_mock(home, 'run1', DAY, now=now, contexts=CONTEXTS,
                                  settings=CONFIG, stub_results=['success'])
        else:
            reconcile_prepared_mock(home, 'run1', DAY, now=now,
                                    contexts=CONTEXTS, settings=CONFIG)
    assert _ledger(home) == before_ledger
    assert _files(home) == before_files
