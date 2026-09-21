"""Notification artifacts use the shared exclusive JSON writer."""
import copy
import json
import os
from pathlib import Path

import pytest

from test_runner import setup
from test_notification_queue import prepared, queue, CONFIG, CONTEXTS, DAY, NOW
from test_mock_delivery import ready, deliver, box
from aitrader.runner import RunError
from aitrader_ops.notify import Notifier


def _output(home, route):
    name = {'plan': 'notification_plan.json',
            'queue': 'notification_queue.json',
            'delivery': 'mock_delivery.json'}[route]
    return home/'runs'/str(DAY)/'run1'/name


def _invoke(setup, route):
    if route == 'plan':
        home, plan = prepared(setup)
        return home, plan
    if route == 'queue':
        home, _ = prepared(setup)
        return home, queue(home)
    home, _ = ready(setup)
    return home, deliver(home)


@pytest.mark.parametrize('route,module_name', [
    ('plan', 'aitrader.notification_plan'),
    ('queue', 'aitrader.notification_queue'),
    ('delivery', 'aitrader.mock_delivery'),
])
def test_each_route_calls_shared_writer_with_redaction_disabled(setup, monkeypatch,
                                                                route, module_name):
    module = __import__(module_name, fromlist=['write_json'])
    original = module.write_json
    calls = []

    def observed(path, value, *, redact=True):
        calls.append((Path(path), redact))
        return original(path, value, redact=redact)

    monkeypatch.setattr(module, 'write_json', observed)
    home, result = _invoke(setup, route)
    output = _output(home, route)
    assert calls == [(output, False)]
    assert json.loads(output.read_text(encoding='utf-8')) == result


@pytest.mark.parametrize('route', ['plan', 'queue', 'delivery'])
def test_stale_fixed_temp_is_never_opened_or_changed(setup, monkeypatch, route):
    # Establish prerequisites without creating this route's output.
    if route == 'plan':
        home, execute = setup
        execute()
    elif route == 'queue':
        home, _ = prepared(setup)
    else:
        home, _ = ready(setup)
    output = _output(home, route)
    stale = output.with_suffix('.json.tmp')
    stale.write_bytes(b'attacker-sentinel')
    original_open = Path.open

    def guarded_open(self, *args, **kwargs):
        if self == stale:
            pytest.fail('legacy fixed temporary path was opened')
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded_open)
    if route == 'plan':
        from aitrader.notification_plan import prepare_notifications
        result = prepare_notifications(home, 'run1', DAY,
                                       contexts=copy.deepcopy(CONTEXTS),
                                       settings=copy.deepcopy(CONFIG))
    elif route == 'queue':
        result = queue(home)
    else:
        result = deliver(home)
    monkeypatch.undo()
    assert stale.read_bytes() == b'attacker-sentinel'
    assert json.loads(output.read_text(encoding='utf-8')) == result
    assert list(output.parent.glob('.'+output.name+'.*.tmp')) == []


def test_plan_persistence_keeps_report_value_unredacted(setup, monkeypatch):
    secret = 'notification-writer-secret'
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', secret)
    home, execute = setup
    execute()
    from aitrader.notification_plan import prepare_notifications
    contexts = copy.deepcopy(CONTEXTS)
    contexts['buy1']['name'] = secret
    plan = prepare_notifications(home, 'run1', DAY, contexts=contexts,
                                 settings=copy.deepcopy(CONFIG))
    raw = _output(home, 'plan').read_text(encoding='utf-8')
    assert json.loads(raw) == plan


def test_delivery_replace_failure_preserves_receipt_and_retry_does_not_resend(
        setup, monkeypatch):
    home, key = ready(setup)
    output = _output(home, 'delivery')
    stop = home/'STOP'
    stop.touch()
    blocked = deliver(home)
    old = output.read_bytes()
    assert blocked['attempted_by_this_call'] is False
    stop.unlink()
    original_replace = os.replace

    def fail_report(source, destination):
        if Path(destination) == output:
            raise OSError('injected report replace failure')
        return original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', fail_report)
        with pytest.raises(OSError, match='injected report replace failure'):
            deliver(home)
    assert output.read_bytes() == old
    remnants = list(output.parent.glob('.mock_delivery.json.*.tmp'))
    assert len(remnants) == 1
    with box(home) as notifier:
        durable = notifier.get_entry(key)
        assert durable['state'] == 'SENT'
        assert len(durable['attempts']) == 1
    monkeypatch.setattr(Notifier, '_send_stub',
                        lambda *a, **k: pytest.fail('retry attempted another send'))
    result = deliver(home)
    assert result['attempted_by_this_call'] is False
    assert result['repaired'] is False if 'repaired' in result else True
    with box(home) as notifier:
        assert notifier.get_entry(key) == durable


@pytest.mark.parametrize('route', ['plan', 'queue', 'delivery'])
@pytest.mark.parametrize('unsafe_part', ['target', 'parent'])
def test_notification_output_reparse_is_rejected(setup, monkeypatch, route,
                                                  unsafe_part):
    if route == 'plan':
        home, execute = setup
        execute()
    elif route == 'queue':
        home, _ = prepared(setup)
    else:
        home, _ = ready(setup)
        (home/'STOP').touch()
    output = _output(home, route)
    unsafe = output if unsafe_part == 'target' else output.parent
    if unsafe_part == 'target':
        if route == 'plan':
            from aitrader.notification_plan import prepare_notifications
            prepare_notifications(home, 'run1', DAY, contexts=copy.deepcopy(CONTEXTS),
                                  settings=copy.deepcopy(CONFIG))
        elif route == 'queue':
            queue(home)
        else:
            deliver(home)
    original = os.path.isjunction
    observed = False

    def junction(path):
        nonlocal observed
        if Path(path) == unsafe:
            observed = True
            return True
        return original(path)

    monkeypatch.setattr(os.path, 'isjunction', junction)
    with pytest.raises((RunError, OSError, ValueError)):
        if route == 'plan':
            from aitrader.notification_plan import prepare_notifications
            prepare_notifications(home, 'run1', DAY, contexts=copy.deepcopy(CONTEXTS),
                                  settings=copy.deepcopy(CONFIG))
        elif route == 'queue':
            queue(home)
        else:
            deliver(home)
    assert observed
