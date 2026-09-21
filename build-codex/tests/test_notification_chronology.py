"""A notification must never precede its authenticated approval artifacts."""
import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest
from test_notification_queue import setup, prepared, queue, CONFIG, NOW, DAY, CONTEXTS, offline
from aitrader.runner import RunError
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier


def test_initial_enqueue_before_completed_review_is_rejected(setup):
    home, _=prepared(setup)
    with pytest.raises(RunError):
        queue(home, now=NOW-timedelta(minutes=1))
    assert not (home/'notification.sqlite').exists()


def test_initial_enqueue_at_completed_review_is_valid(setup):
    home, _=prepared(setup)
    assert queue(home,now=NOW)['entries'][0]['state']=='PENDING'


def legacy_early_queue(setup, monkeypatch, sent):
    home, plan=prepared(setup)
    queue(home)
    key=plan['plans'][0]['key']
    early=NOW-timedelta(minutes=1)
    if sent:
        with closing(Ledger(home/'ledger.sqlite')) as ledger:
            with closing(Notifier(ledger=ledger,state_path=home/'notification.sqlite',
                                  settings=CONFIG,stub_results=['success'])) as notifier:
                def crash(*a,**k):
                    raise RuntimeError('after durable SENT')
                monkeypatch.setattr(ledger,'set_notice_state',crash)
                with pytest.raises(RuntimeError):
                    notifier.flush(now=NOW,keys=[key])
    # Simulate a queue created by the old API, which accepted an earlier now.
    with closing(sqlite3.connect(home/'notification.sqlite')) as con:
        attempts=json.loads(con.execute('SELECT attempts FROM outbox WHERE key=?',[key]).fetchone()[0])
        for attempt in attempts:
            attempt['at']=early.isoformat()
        con.execute('UPDATE outbox SET created_at=?,updated_at=?,attempts=? WHERE key=?',
                    [early.isoformat(),early.isoformat(),json.dumps(attempts),key])
        con.commit()
    path=home/'runs'/str(DAY)/'run1'/'notification_queue.json'
    receipt=json.loads(path.read_text(encoding='utf-8'))
    receipt['observed_at']=early.isoformat()
    path.write_text(json.dumps(receipt),encoding='utf-8')
    return home,early,key


def test_delivery_of_legacy_queue_before_approval_is_rejected(setup,monkeypatch):
    from aitrader.mock_delivery import deliver_prepared_mock
    home,early,key=legacy_early_queue(setup,monkeypatch,False)
    with pytest.raises(RunError):
        deliver_prepared_mock(home,'run1',DAY,now=early,contexts=CONTEXTS,
                              settings=CONFIG,stub_results=['success'])
    with closing(sqlite3.connect(home/'notification.sqlite')) as con:
        assert con.execute('SELECT state FROM outbox WHERE key=?',[key]).fetchone()[0]=='PENDING'


def test_recovery_of_legacy_success_before_approval_is_rejected(setup,monkeypatch):
    from aitrader.mock_delivery import reconcile_prepared_mock
    home,early,key=legacy_early_queue(setup,monkeypatch,True)
    with pytest.raises(RunError):
        reconcile_prepared_mock(home,'run1',DAY,now=early,contexts=CONTEXTS,settings=CONFIG)
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        assert ledger.notice('buy1')['notice_state']=='APPROVED'
