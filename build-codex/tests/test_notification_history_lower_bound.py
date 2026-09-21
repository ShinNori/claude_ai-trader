"""Main flow rejects future queue history before clocks or ledger effects."""
import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from aitrader.mock_delivery import deliver_prepared_mock, reconcile_prepared_mock
from aitrader.runner import RunError
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier
from test_notification_persisted_integrity import (
    _files, _ledger, _prepare_legacy, _prepare_managed,
    CONFIG, CONTEXTS, DAY, NOW, setup,
)


@pytest.mark.parametrize('mode', ['legacy', 'managed'])
@pytest.mark.parametrize('field', ['created_at', 'updated_at', 'attempt_at'])
@pytest.mark.parametrize('action', ['deliver', 'reconcile'])
def test_future_queue_history_is_rejected_before_main_effects(
        tmp_path, setup, monkeypatch, mode, field, action):
    home = _prepare_legacy(setup) if mode == 'legacy' else _prepare_managed(tmp_path)
    if action == 'deliver':
        deliver_prepared_mock(home, 'run1', DAY, now=NOW,
                              contexts=CONTEXTS, settings=CONFIG,
                              stub_results=['timeout'])
    else:
        with closing(Ledger(home/'ledger.sqlite')) as ledger:
            with closing(Notifier(ledger=ledger, state_path=home/'notification.sqlite',
                                  settings=CONFIG, transport='stub',
                                  stub_results=['success'])) as box:
                def fail_after_sent(*args, **kwargs):
                    raise RuntimeError('injected ledger write failure')
                monkeypatch.setattr(ledger, 'set_notice_state', fail_after_sent)
                with pytest.raises(RuntimeError, match='injected ledger'):
                    box.flush(now=NOW)
    future = (NOW + timedelta(minutes=2)).isoformat()
    with closing(sqlite3.connect(home/'notification.sqlite')) as db:
        if field == 'attempt_at':
            key, raw = db.execute('SELECT key,attempts FROM outbox').fetchone()
            attempts = json.loads(raw)
            attempts[0]['at'] = future
            db.execute('UPDATE outbox SET attempts=? WHERE key=?', [json.dumps(attempts), key])
        else:
            db.execute(f'UPDATE outbox SET {field}=?', [future])
        db.commit()
    before_files, before_ledger = _files(home), _ledger(home)
    kwargs = dict(now=NOW+timedelta(minutes=1), contexts=CONTEXTS, settings=CONFIG)
    with pytest.raises(RunError, match='通知履歴'):
        if action == 'deliver':
            deliver_prepared_mock(home, 'run1', DAY, stub_results=['success'], **kwargs)
        else:
            reconcile_prepared_mock(home, 'run1', DAY, **kwargs)
    assert _files(home) == before_files
    assert _ledger(home) == before_ledger
