"""Expiry and retry history mismatches stop before main-flow clocks advance."""
import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from aitrader.mock_delivery import deliver_prepared_mock
from aitrader.runner import RunError
from test_notification_persisted_integrity import (
    _files, _ledger, _prepare_legacy, _prepare_managed,
    CONFIG, CONTEXTS, DAY, NOW, setup,
)


@pytest.mark.parametrize('mode', ['legacy', 'managed'])
@pytest.mark.parametrize('damage', ['expires_at', 'retry_key', 'attempt_retry_key'])
def test_delivery_binding_failure_keeps_clocks_and_reservations(tmp_path, setup, mode, damage):
    home = _prepare_legacy(setup) if mode == 'legacy' else _prepare_managed(tmp_path)
    if damage != 'expires_at':
        first = deliver_prepared_mock(home, 'run1', DAY, now=NOW,
                                      contexts=CONTEXTS, settings=CONFIG,
                                      stub_results=['timeout'])
        assert first['entries'][0]['state'] == 'UNKNOWN'
    with closing(sqlite3.connect(home/'notification.sqlite')) as db:
        if damage == 'expires_at':
            db.execute('UPDATE outbox SET expires_at=?',
                       [NOW.replace(hour=8, minute=58).isoformat()])
        elif damage == 'retry_key':
            db.execute("UPDATE outbox SET retry_key='different-retry-key'")
        else:
            key, raw = db.execute('SELECT key,attempts FROM outbox').fetchone()
            attempts = json.loads(raw)
            attempts[0]['retry_key'] = 'different-retry-key'
            db.execute('UPDATE outbox SET attempts=? WHERE key=?',
                       [json.dumps(attempts), key])
        db.commit()
    before_files, before_ledger = _files(home), _ledger(home)
    with pytest.raises(RunError):
        deliver_prepared_mock(home, 'run1', DAY, now=NOW+timedelta(minutes=1),
                              contexts=CONTEXTS, settings=CONFIG,
                              stub_results=['success'])
    assert _ledger(home) == before_ledger
    assert _files(home) == before_files
