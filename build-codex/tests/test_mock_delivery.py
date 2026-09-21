"""Independent offline checks of run-scoped simulated delivery."""
import copy
from contextlib import closing, contextmanager
from datetime import timedelta

import pytest

from test_notification_queue import setup, prepared, queue, CONFIG, NOW, DAY, CONTEXTS, offline, snapshot
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier
from aitrader.runner import RunError


def deliver(home, **changes):
    from aitrader.mock_delivery import deliver_prepared_mock
    args = dict(now=NOW, contexts=copy.deepcopy(CONTEXTS),
                settings=copy.deepcopy(CONFIG), stub_results=['success'])
    args.update(changes)
    return deliver_prepared_mock(home, 'run1', DAY, **args)


@contextmanager
def box(home):
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        with closing(Notifier(ledger=ledger, state_path=home/'notification.sqlite',
                              settings=CONFIG, transport='stub')) as notifier:
            yield notifier


def ready(setup):
    home, plan = prepared(setup)
    queue(home)
    return home, plan['plans'][0]['key']


def test_success_is_only_simulated_and_does_not_reserve_again(setup):
    home, key = ready(setup)
    before = snapshot(home)
    result = deliver(home)
    assert result['mode'] == 'mock' and result['transport'] == 'stub'
    assert result['simulated_sent_by_this_call'] is True
    assert result['real_sent_by_this_call'] is False
    with box(home) as notifier:
        assert notifier.get_entry(key)['state'] == 'SENT'
        assert len(notifier.get_entry(key)['attempts']) == 1
    after = snapshot(home)
    assert after[1] == before[1]
    assert after[2]['notice_state'] == 'SENT'


def test_repeated_success_does_not_send_twice(setup):
    home, key = ready(setup)
    deliver(home)
    result = deliver(home)
    assert result['attempted_by_this_call'] is False
    with box(home) as notifier:
        assert len(notifier.get_entry(key)['attempts']) == 1


@pytest.mark.parametrize('seconds,attempted', [(-1, True), (0, False), (1, False)])
def test_exclusive_0715_deadline(setup, seconds, attempted):
    home, key = ready(setup)
    result = deliver(home, now=NOW.replace(minute=15)+timedelta(seconds=seconds))
    assert result['attempted_by_this_call'] is attempted
    with box(home) as notifier:
        assert len(notifier.get_entry(key)['attempts']) == int(attempted)


@pytest.mark.parametrize('stop_kind', ['file', 'persistent'])
def test_stop_blocks_new_without_consuming_budget(setup, stop_kind):
    home, key = ready(setup)
    if stop_kind == 'file':
        (home/'STOP').write_text('stop', encoding='utf-8')
    else:
        with box(home) as notifier:
            notifier.stop(now=NOW)
    result = deliver(home)
    assert result['attempted_by_this_call'] is False
    with box(home) as notifier:
        assert notifier.get_entry(key)['attempts'] == []
        assert notifier.status(now=NOW)['monthly_used'] == 0


@pytest.mark.parametrize('block', ['deadline', 'stop'])
def test_timeout_remains_unknown_with_retry_key_and_budget(setup, block):
    home, key = ready(setup)
    deliver(home, stub_results=['timeout'])
    with box(home) as notifier:
        before = notifier.get_entry(key)
    changes = {}
    if block == 'deadline':
        changes['now'] = NOW.replace(minute=15)
    else:
        (home/'STOP').write_text('stop', encoding='utf-8')
    result = deliver(home, **changes)
    assert result['attempted_by_this_call'] is False
    with box(home) as notifier:
        after = notifier.get_entry(key)
        assert after['state'] == 'UNKNOWN'
        assert after['retry_key'] == before['retry_key']
        assert after['month'] == before['month']
        assert after['attempts'] == before['attempts']
        assert notifier.status(now=NOW)['monthly_used'] == 1


def test_other_run_queue_entry_is_untouched(setup):
    home, key = ready(setup)
    with box(home) as notifier:
        notifier.enqueue(key='other-run-risk', kind='RISK', proposal_id=None,
                         message={'type': 'text', 'text': 'other run'}, now=NOW)
    deliver(home)
    with box(home) as notifier:
        assert notifier.get_entry(key)['state'] == 'SENT'
        unrelated = notifier.get_entry('other-run-risk')
        assert unrelated['state'] == 'PENDING' and unrelated['attempts'] == []


@pytest.mark.parametrize('stage', ['run_only', 'plan_only'])
def test_requires_existing_plan_and_queue(setup, stage):
    home, execute = setup
    if stage == 'run_only':
        execute()
    else:
        home, _ = prepared(setup)
    with pytest.raises((RunError, ValueError)):
        deliver(home)


@pytest.mark.parametrize('change', ['tomorrow', 'recipient', 'invalid_stub'])
def test_invalid_input_cannot_attempt_delivery(setup, change):
    home, key = ready(setup)
    args = {'tomorrow': {'now': NOW+timedelta(days=1)},
            'recipient': {'settings': dict(CONFIG, line={'allowed_user_id': 'changed'})},
            'invalid_stub': {'stub_results': ['real']}}[change]
    with pytest.raises((RunError, ValueError)):
        deliver(home, **args)
    with box(home) as notifier:
        assert notifier.get_entry(key)['attempts'] == []
