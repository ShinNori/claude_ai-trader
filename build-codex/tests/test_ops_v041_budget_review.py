"""Codex budget counterexamples; synthetic SQLite and stub transport only."""
from datetime import timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'common/tests/phase2')]
import test_notify as n
from aitrader_ops.notify import Notifier

offline = n.offline
ledger = n.ledger
END = n.NOW.replace(day=30, hour=23, minute=59, second=59)
START = END + timedelta(seconds=1)

@pytest.fixture
def box(tmp_path, ledger):
    obj = Notifier(ledger=ledger, state_path=tmp_path/'outbox.sqlite',
        settings=dict(n.SETTINGS, line=dict(n.SETTINGS['line'], monthly_budget=1)))
    yield obj
    obj.close()

def enqueue(obj, key, kind, at):
    return obj.enqueue(key=key, kind=kind, message=n.message(kind), proposal_id=None, now=at)

@pytest.mark.parametrize('kind', ['RECONCILE', 'RISK'])
@pytest.mark.parametrize('result', ['success', 'timeout', 'failure'])
def test_b01_unknown_retry_keeps_original_month_even_under_stop(box, kind, result):
    """An old uncertain delivery retains its old reservation through retry."""
    enqueue(box, 'old', kind, END)
    box.stub_results = ['timeout', result]
    first = box.flush(now=END)[0]
    assert first['state'] == 'UNKNOWN'
    box.stop(now=START)
    retry = box.flush(now=START.astimezone(timezone.utc))[0]
    assert retry['retry_key'] == first['retry_key']
    assert retry['state'] == ('SENT' if result == 'success' else 'UNKNOWN')
    assert box.status(now=END)['monthly_used'] == 1
    assert box.status(now=START)['monthly_used'] == 0

@pytest.mark.parametrize('kind', ['RECONCILE', 'RISK'])
def test_b02_new_month_budget_still_available_after_old_unknown_retry(box, kind):
    enqueue(box, 'old', kind, END)
    box.stub_results = ['timeout', 'success', 'success']
    box.flush(now=END)
    box.stop(now=START)
    enqueue(box, 'new', kind, START)
    outcomes = {r['key']: r['state'] for r in box.flush(now=START)}
    assert outcomes == {'old': 'SENT', 'new': 'SENT'}
    assert box.status(now=END)['monthly_used'] == 1
    assert box.status(now=START)['monthly_used'] == 1

@pytest.mark.parametrize('kind', ['RECONCILE', 'RISK'])
@pytest.mark.parametrize('state', ['SENT', 'UNKNOWN', 'SENDING'])
def test_b03_stop_does_not_exempt_operational_messages_from_budget(box, kind, state):
    enqueue(box, 'occupied', kind, n.NOW)
    box.stub_results = ['success' if state == 'SENT' else 'timeout']
    box.flush(now=n.NOW)
    if state == 'SENDING':
        # Fault injection: another live sender owns an unexpired durable claim.
        box._con.execute("UPDATE outbox SET state='SENDING', claimed_by='other', claimed_at=? WHERE key='occupied'", [n.NOW.isoformat()])
    box.stop(now=n.NOW)
    enqueue(box, 'next', kind, n.NOW)
    box.stub_results = ['failure'] if state == 'UNKNOWN' else []
    outcomes = {r['key']: r['state'] for r in box.flush(now=n.NOW)}
    assert outcomes['next'] == 'BUDGET_BLOCKED'
    assert box.status(now=n.NOW)['monthly_used'] == 1
    assert 'BUDGET_EXCEEDED' in box.status(now=n.NOW)['alerts']

@pytest.mark.parametrize('kind', ['RECONCILE', 'RISK'])
def test_b04_stale_previous_month_claim_preserves_reservation(box, kind):
    at = END - timedelta(minutes=11)
    enqueue(box, 'crashed', kind, at)
    entry = {'retry_key': box._con.execute("SELECT retry_key FROM outbox WHERE key='crashed'").fetchone()[0]}
    box._con.execute("UPDATE outbox SET state='SENDING', month='2026-09', claimed_by='dead', claimed_at=?, attempts=?", [at.isoformat(), json.dumps([{'at':at.isoformat(), 'result':'exception', 'retry_key':entry['retry_key']}])])
    box.stop(now=START)
    box.stub_results = ['success']
    assert box.flush(now=START)[0]['state'] == 'SENT'
    assert box.status(now=END)['monthly_used'] == 1
    assert box.status(now=START)['monthly_used'] == 0

@pytest.mark.parametrize('kind', ['RECONCILE', 'RISK'])
def test_b05_old_pending_expires_while_current_operational_message_sends(box, kind):
    enqueue(box, 'old', kind, END)
    box.stop(now=START)
    enqueue(box, 'new', kind, START)
    box.stub_results = ['success']
    assert {r['key']:r['state'] for r in box.flush(now=START)} == {'old':'EXPIRED', 'new':'SENT'}
    assert box.status(now=START)['monthly_used'] == 1

@pytest.mark.parametrize('kind', ['RECONCILE', 'RISK'])
def test_b06_exception_during_next_month_retry_keeps_original_reservation(box, kind, monkeypatch):
    enqueue(box, 'old', kind, END)
    box.stub_results = ['timeout']
    box.flush(now=END)
    box.stop(now=START)
    def crash():
        raise RuntimeError('synthetic send exception')
    monkeypatch.setattr(box, '_send_stub', crash)
    with pytest.raises(RuntimeError, match='synthetic'):
        box.flush(now=START)
    assert box.status(now=END)['monthly_used'] == 1
    assert box.status(now=START)['monthly_used'] == 0
