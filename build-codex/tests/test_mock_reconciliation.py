"""Recovery uses durable simulated success, never a second send."""
import copy
import json
from pathlib import Path
from datetime import timedelta

import pytest

from test_mock_delivery import setup, ready, deliver, box, NOW, DAY, CONTEXTS, CONFIG, RunError, offline
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier


def reconcile(home, **changes):
    from aitrader.mock_delivery import reconcile_prepared_mock
    args = dict(now=NOW, contexts=copy.deepcopy(CONTEXTS), settings=copy.deepcopy(CONFIG))
    args.update(changes)
    return reconcile_prepared_mock(home, 'run1', DAY, **args)


def interrupted(setup, monkeypatch):
    home, key = ready(setup)
    with monkeypatch.context() as patch:
        def crash(*args, **kwargs):
            raise RuntimeError('simulated ledger write interruption')
        patch.setattr(Ledger, 'set_notice_state', crash)
        with pytest.raises(RuntimeError):
            deliver(home)
    with box(home) as notifier:
        assert notifier.get_entry(key)['state'] == 'SENT'
        assert notifier.ledger.notice('buy1')['notice_state'] == 'APPROVED'
    return home, key


def forbid_send(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('reconciliation must neither enqueue nor send')
    monkeypatch.setattr(Notifier, 'enqueue', forbidden)
    monkeypatch.setattr(Notifier, 'flush', forbidden)


def test_successful_queue_repairs_ledger_without_resending(setup, monkeypatch):
    home, key = interrupted(setup, monkeypatch)
    with box(home) as notifier:
        before = notifier.get_entry(key)
        reserved = notifier.ledger.reserved()
    forbid_send(monkeypatch)
    result = reconcile(home)
    assert result['repaired_keys'] == [key]
    assert result['attempted_by_this_call'] is False
    assert result['real_sent_by_this_call'] is False
    with box(home) as notifier:
        assert notifier.ledger.notice('buy1')['notice_state'] == 'SENT'
        assert notifier.ledger.reserved() == reserved
        assert notifier.get_entry(key) == before
        seq = notifier.ledger.seq()
    assert reconcile(home)['repaired_keys'] == []
    with box(home) as notifier:
        assert notifier.ledger.seq() == seq


@pytest.mark.parametrize('result', ['timeout', 'failure'])
def test_no_success_evidence_never_marks_sent(setup, monkeypatch, result):
    home, key = ready(setup)
    deliver(home, stub_results=[result])
    with box(home) as notifier:
        before = notifier.get_entry(key)
    forbid_send(monkeypatch)
    assert reconcile(home)['repaired_keys'] == []
    with box(home) as notifier:
        assert notifier.get_entry(key) == before
        assert notifier.ledger.notice('buy1')['notice_state'] == 'APPROVED'


@pytest.mark.parametrize('tamper', ['plan', 'receipt'])
def test_tampered_record_is_rejected_before_repair(setup, monkeypatch, tamper):
    home, key = interrupted(setup, monkeypatch)
    path = home/'runs'/str(DAY)/'run1'/('notification_plan.json' if tamper == 'plan' else 'notification_queue.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    data['plan_hash'] = 'tampered'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises((RunError, ValueError)):
        reconcile(home)
    with box(home) as notifier:
        assert notifier.ledger.notice('buy1')['notice_state'] == 'APPROVED'


def test_repair_before_send_timestamp_is_rejected(setup, monkeypatch):
    home, key = interrupted(setup, monkeypatch)
    with pytest.raises((RunError, ValueError)):
        reconcile(home, now=NOW-timedelta(seconds=1))
    with box(home) as notifier:
        assert notifier.ledger.notice('buy1')['notice_state'] == 'APPROVED'


def test_report_write_failure_retries_without_duplicate_ledger_event(setup, monkeypatch):
    home, key = interrupted(setup, monkeypatch)
    with monkeypatch.context() as patch:
        import aitrader.runner as runner
        original = runner.os.replace

        def fail_report(source, target):
            if Path(target).name == 'mock_reconciliation.json':
                raise OSError('simulated report write failure')
            return original(source, target)
        patch.setattr(runner.os, 'replace', fail_report)
        with pytest.raises(OSError):
            reconcile(home)
    with box(home) as notifier:
        assert notifier.ledger.notice('buy1')['notice_state'] == 'SENT'
        seq = notifier.ledger.seq()
    forbid_send(monkeypatch)
    assert reconcile(home)['repaired_keys'] == []
    with box(home) as notifier:
        assert notifier.ledger.seq() == seq
    assert (home/'runs'/str(DAY)/'run1'/'mock_reconciliation.json').is_file()


def test_another_run_success_is_not_repaired(setup, monkeypatch):
    from test_runner import proposal
    home, key = interrupted(setup, monkeypatch)
    with box(home) as notifier:
        notifier.ledger.create_notice(proposal('other', code='7203'), at=NOW)
        notifier.ledger.set_notice_state('other', 'APPROVED', NOW)
        notifier.enqueue(key='other-run-key', kind='NEW', proposal_id='other',
                         message={'type': 'text', 'text': 'other run'}, now=NOW)
        notifier.stub_results = ['success']
        with monkeypatch.context() as patch:
            def crash(*args, **kwargs):
                raise RuntimeError('interrupted other run')
            patch.setattr(Ledger, 'set_notice_state', crash)
            with pytest.raises(RuntimeError):
                notifier.flush(now=NOW, keys=['other-run-key'])
        before = notifier.get_entry('other-run-key')
    forbid_send(monkeypatch)
    assert reconcile(home)['repaired_keys'] == [key]
    with box(home) as notifier:
        assert notifier.ledger.notice('other')['notice_state'] == 'APPROVED'
        assert notifier.get_entry('other-run-key') == before
