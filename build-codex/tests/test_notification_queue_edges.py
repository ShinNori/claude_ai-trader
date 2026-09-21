"""Queue interruption, pre-existing conflicts, and status-only counterexamples."""
import copy
import sqlite3
from contextlib import contextmanager, closing

import pytest

from test_notification_queue import (setup, prepared, queue, CONFIG, CONTEXTS,
                                     DAY, NOW, snapshot, offline)
from test_runner import proposal
from aitrader.notification_plan import prepare_notifications
from aitrader_ops.notify import Notifier
from aitrader_ops.ledger import Ledger
from aitrader.runner import RunError


@pytest.fixture(autouse=True)
def never_flush(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('queue-only integration must never flush')
    monkeypatch.setattr(Notifier, 'flush', forbidden)


@contextmanager
def opened(home, settings=CONFIG):
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        with closing(Notifier(ledger=ledger, state_path=home/'notification.sqlite',
                              settings=settings, transport='stub')) as box:
            yield box


def two_prepared(setup):
    home, execute = setup
    result = execute([proposal(), proposal('buy2', code='7203')])
    assert [c['status'] for c in result['candidates']] == ['APPROVED', 'APPROVED']
    contexts = copy.deepcopy(CONTEXTS)
    contexts['buy2'] = dict(contexts['buy1'], name='second')
    plan = prepare_notifications(home, 'run1', DAY, contexts=contexts, settings=CONFIG)
    assert len(plan['plans']) == 2
    return home, plan, contexts


def test_partial_enqueue_recovers_same_keys_without_duplicate(setup, monkeypatch):
    home, plan, contexts = two_prepared(setup)
    before = snapshot(home)
    original = Notifier.enqueue
    calls = []
    def interrupted(self, **args):
        calls.append(args['key'])
        if len(calls) == 2:
            raise OSError('simulated interruption before second enqueue')
        return original(self, **args)
    with monkeypatch.context() as patch:
        patch.setattr(Notifier, 'enqueue', interrupted)
        with pytest.raises(OSError):
            queue(home, contexts=contexts)
    first_key, second_key = [p['key'] for p in plan['plans']]
    with opened(home) as box:
        first = box.get_entry(first_key)
        with pytest.raises(KeyError):
            box.get_entry(second_key)
    receipt = queue(home, contexts=contexts)
    assert [e['key'] for e in receipt['entries']] == [first_key, second_key]
    queue(home, contexts=contexts)
    with opened(home) as box:
        assert box.get_entry(first_key) == first
        assert box.get_entry(second_key)['state'] == 'PENDING'
        assert box.status(now=NOW)['monthly_used'] == 0
    assert snapshot(home) == before


@pytest.mark.parametrize('conflict', ['recipient', 'message'])
def test_second_key_conflict_preflights_before_first_insert(setup, conflict):
    home, plan, contexts = two_prepared(setup)
    card = plan['plans'][1]
    settings, message = copy.deepcopy(CONFIG), copy.deepcopy(card['message'])
    if conflict == 'recipient':
        settings['line']['allowed_user_id'] = 'different-recipient'
    else:
        message['altText'] = 'existing unrelated content'
    with opened(home, settings) as box:
        box.enqueue(key=card['key'], kind=card['kind'], message=message,
                    proposal_id=card['proposal_id'], now=NOW)
        existing = box.get_entry(card['key'])
    before = snapshot(home)
    with pytest.raises(RunError):
        queue(home, contexts=contexts)
    # Inspect with the original row's recipient: get_entry now validates it.
    with opened(home, settings) as box:
        with pytest.raises(KeyError):
            box.get_entry(plan['plans'][0]['key'])
        assert box.get_entry(card['key']) == existing
    assert snapshot(home) == before


def test_same_proposal_under_another_key_is_rejected(setup):
    home, plan = prepared(setup)
    card = plan['plans'][0]
    with opened(home) as box:
        box.enqueue(key='alternate-key', kind=card['kind'], message=card['message'],
                    proposal_id=card['proposal_id'], now=NOW)
        existing = box.get_entry('alternate-key')
    before = snapshot(home)
    with pytest.raises(RunError):
        queue(home)
    with opened(home) as box:
        assert box.get_entry('alternate-key') == existing
        with pytest.raises(KeyError):
            box.get_entry(card['key'])
    assert snapshot(home) == before


def test_empty_context_status_only_queues_without_candidate_or_budget(setup):
    home, execute = setup
    execute([])
    plan = prepare_notifications(home, 'run1', DAY, contexts={}, settings=CONFIG,
                                 include_status=True)
    assert len(plan['plans']) == 1
    assert plan['plans'][0]['kind'] == 'RECONCILE'
    receipt = queue(home, contexts={}, include_status=True)
    assert receipt['delivery'] == 'NOT_SENT'
    with opened(home) as box:
        entry = box.get_entry(plan['plans'][0]['key'])
        assert entry['proposal_id'] is None
        assert entry['state'] == 'PENDING'
        assert entry['attempts'] == []
        assert box.status(now=NOW)['monthly_used'] == 0


@pytest.mark.parametrize('prior_state', ['UNKNOWN', 'SENT', 'SENDING'])
def test_previous_attempt_cannot_be_reported_as_not_sent(setup, prior_state):
    home, plan = prepared(setup)
    queue(home)
    card = plan['plans'][0]
    # Durable-state fault injection represents an earlier process's attempt;
    # this queue-only test never calls flush or starts a transport.
    with closing(sqlite3.connect(home/'notification.sqlite')) as con:
        con.execute('UPDATE outbox SET state=?, month=? WHERE key=?',
                    [prior_state, '2026-09', card['key']])
        con.commit()
    before = snapshot(home)
    receipt = queue(home)
    assert receipt['action'] == 'ENQUEUE_ONLY'
    assert receipt['sent_by_this_call'] is False
    assert receipt['delivery'] == 'PRIOR_ATTEMPT_EXISTS'
    assert receipt['entries'][0]['state'] == prior_state
    with opened(home) as box:
        assert box.get_entry(card['key'])['state'] == prior_state
        assert box.status(now=NOW)['monthly_used'] == 1
    assert snapshot(home) == before
