"""Crash observations for mock delivery; no implicit ledger repair is promised.

Queue SENT with ledger APPROVED is currently read as ALREADY_SENT. It is not
repaired here: global reconcile_sent would affect unrelated runs. This limitation
is asserted explicitly, alongside no resend after loss of the output report.
"""
import json
from pathlib import Path

import pytest

from test_mock_delivery import setup, offline, ready, deliver, box, DAY, NOW
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier
from aitrader.runner import RunError


def report_path(home):
    return home/'runs'/str(DAY)/'run1'/'mock_delivery.json'


def forbid_send(*args, **kwargs):
    pytest.fail('durable SENT must not send again')


def test_missing_report_reconstructs_from_durable_sent_without_resending(setup, monkeypatch):
    home, key = ready(setup)
    deliver(home)
    with box(home) as notifier:
        before = notifier.get_entry(key)
    report_path(home).unlink()
    monkeypatch.setattr(Notifier, '_send_stub', forbid_send)
    result = deliver(home)
    assert json.loads(report_path(home).read_text(encoding='utf-8')) == result
    assert result['attempted_by_this_call'] is False
    assert result['simulated_sent_by_this_call'] is False
    assert result['results'][0]['state'] == 'ALREADY_SENT'
    with box(home) as notifier:
        assert notifier.get_entry(key) == before
        assert notifier.status(now=NOW)['monthly_used'] == 1


@pytest.mark.parametrize('failure_point', ['write', 'replace'])
def test_report_io_failure_after_durable_sent_does_not_repeat_delivery(setup, monkeypatch, failure_point):
    home, key = ready(setup)
    with monkeypatch.context() as patch:
        import aitrader.runner as runner
        if failure_point == 'write':
            original = runner.tempfile.mkstemp

            def fail(*args, **kwargs):
                prefix = kwargs.get('prefix', args[0] if args else '')
                if prefix.startswith('.mock_delivery.json.'):
                    raise OSError('injected report write failure')
                return original(*args, **kwargs)

            patch.setattr(runner.tempfile, 'mkstemp', fail)
        else:
            original = runner.os.replace

            def fail(path, target):
                if Path(target) == report_path(home):
                    raise OSError('injected report replacement failure')
                return original(path, target)

            patch.setattr(runner.os, 'replace', fail)
        with pytest.raises(OSError, match='injected report'):
            deliver(home)
    assert not report_path(home).exists()
    with box(home) as notifier:
        before = notifier.get_entry(key)
        assert before['state'] == 'SENT' and len(before['attempts']) == 1
        assert notifier.ledger.notice('buy1')['notice_state'] == 'SENT'
    monkeypatch.setattr(Notifier, '_send_stub', forbid_send)
    result = deliver(home)
    assert result['attempted_by_this_call'] is False
    assert report_path(home).is_file()
    with box(home) as notifier:
        assert notifier.get_entry(key) == before


def test_ledger_sent_with_pending_queue_is_rejected_before_send(setup, monkeypatch):
    home, key = ready(setup)
    with box(home) as notifier:
        notifier.ledger.set_notice_state('buy1', 'SENT', NOW)
        before = notifier.get_entry(key)
    monkeypatch.setattr(Notifier, '_send_stub', forbid_send)
    with pytest.raises(RunError, match='台帳の送信記録と通知キュー'):
        deliver(home)
    assert not report_path(home).exists()
    with box(home) as notifier:
        assert notifier.get_entry(key) == before
        assert notifier.status(now=NOW)['monthly_used'] == 0


def test_durable_queue_sent_with_approved_ledger_is_observed_not_implicitly_repaired(setup, monkeypatch):
    home, key = ready(setup)
    original = Ledger.set_notice_state

    def fail_sent(ledger, proposal_id, state, at):
        if state == 'SENT':
            raise OSError('injected crash before ledger SENT')
        return original(ledger, proposal_id, state, at)

    with monkeypatch.context() as patch:
        patch.setattr(Ledger, 'set_notice_state', fail_sent)
        with pytest.raises(OSError, match='before ledger SENT'):
            deliver(home)
    with box(home) as notifier:
        before = notifier.get_entry(key)
        assert before['state'] == 'SENT'
        assert notifier.ledger.notice('buy1')['notice_state'] == 'APPROVED'
    monkeypatch.setattr(Notifier, '_send_stub', forbid_send)
    result = deliver(home)
    assert result['results'][0]['state'] == 'ALREADY_SENT'
    assert result['attempted_by_this_call'] is False
    with box(home) as notifier:
        assert notifier.get_entry(key) == before
        assert notifier.ledger.notice('buy1')['notice_state'] == 'APPROVED'
        assert notifier.status(now=NOW)['monthly_used'] == 1
