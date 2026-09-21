"""Persisted candidate expiry and retry identity remain bound before effects."""
import json
import socket
import subprocess
from datetime import timedelta, timezone

import pytest

from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier
from test_v041_order4_review_claude import NOW, SETTINGS, proposal


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('process'))
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('network'))


@pytest.fixture
def queue(tmp_path):
    ledger = Ledger(tmp_path/'ledger.sqlite')
    ledger.init_snapshot(1_000_000, [], [], NOW.replace(hour=6))
    box = Notifier(ledger=ledger, state_path=tmp_path/'notify.sqlite',
                   settings=SETTINGS, transport='stub')
    yield ledger, box
    box.close()
    ledger.close()


def add_candidate(ledger, box, *, key='candidate', pid='candidate-1', minute=5):
    item = proposal(proposal_id=pid, expires_at=NOW.replace(hour=7, minute=minute))
    ledger.create_notice(item, at=NOW)
    ledger.set_notice_state(pid, 'APPROVED', NOW)
    box.enqueue(key=key, kind='NEW', message={'type': 'text', 'text': key},
                proposal_id=pid, now=NOW)
    return item


def snapshot(ledger, box):
    return (ledger.seq(), box._con.execute('SELECT * FROM outbox ORDER BY seq').fetchall(),
            box._con.execute('SELECT * FROM kv ORDER BY name').fetchall(),
            box._con.execute('SELECT * FROM controls ORDER BY seq').fetchall())


@pytest.mark.parametrize('stored', [
    '2026-09-09T08:59:00+09:00',
    '2026-09-09T07:01:00+09:00',
    None,
])
def test_candidate_expiry_change_is_rejected_before_original_deadline(queue, stored):
    ledger, box = queue
    add_candidate(ledger, box)
    box._con.execute('UPDATE outbox SET expires_at=? WHERE key=?', [stored, 'candidate'])
    before = snapshot(ledger, box)
    box.stub_results = ['success']
    with pytest.raises(ValueError, match='保存済み通知キュー'):
        box.flush(now=NOW.replace(hour=7, minute=10), keys=['candidate'])
    assert snapshot(ledger, box) == before
    assert box.stub_results == ['success']


def test_same_expiry_in_different_timezone_is_accepted(queue):
    ledger, box = queue
    item = add_candidate(ledger, box)
    equivalent = item.expires_at.astimezone(timezone.utc).isoformat()
    box._con.execute('UPDATE outbox SET expires_at=? WHERE key=?',
                     [equivalent, 'candidate'])
    box.stub_results = ['success']
    assert box.flush(now=NOW.replace(hour=7, minute=1),
                     keys=['candidate'])[0]['state'] == 'SENT'


def test_non_candidate_null_expiry_remains_valid(queue):
    _, box = queue
    box.enqueue(key='risk', kind='RISK', message={'type': 'text'},
                proposal_id=None, now=NOW)
    assert box._con.execute(
        "SELECT expires_at FROM outbox WHERE key='risk'").fetchone()[0] is None
    box.stub_results = ['success']
    assert box.flush(now=NOW, keys=['risk'])[0]['state'] == 'SENT'


def test_non_candidate_created_at_same_instant_utc_uses_jst_day(queue):
    _, box = queue
    box.enqueue(key='risk', kind='RISK', message={'type': 'text'},
                proposal_id=None, now=NOW)
    utc_text = NOW.astimezone(timezone.utc).isoformat()
    box._con.execute(
        "UPDATE outbox SET created_at=?,updated_at=? WHERE key='risk'",
        [utc_text, utc_text])
    box.stub_results = ['success']
    assert box.flush(now=NOW+timedelta(hours=1), keys=['risk'])[0]['state'] == 'SENT'


def test_later_bad_candidate_blocks_first_before_stub_clock_or_ledger(queue):
    ledger, box = queue
    add_candidate(ledger, box, key='first', pid='candidate-1')
    add_candidate(ledger, box, key='second', pid='candidate-2')
    box._con.execute("UPDATE outbox SET expires_at='2026-09-09T08:59:00+09:00' "
                     "WHERE key='second'")
    before = snapshot(ledger, box)
    box.stub_results = ['success', 'success']
    with pytest.raises(ValueError, match='保存済み通知キュー'):
        box.flush(now=NOW.replace(hour=7, minute=1), keys=['first', 'second'])
    assert snapshot(ledger, box) == before
    assert box.stub_results == ['success', 'success']


@pytest.mark.parametrize(('first_result', 'final_state'), [
    ('failure', 'SENT'), ('timeout', 'SENT'),
])
def test_normal_retry_keeps_one_retry_key(queue, first_result, final_state):
    ledger, box = queue
    add_candidate(ledger, box, minute=59)
    original = box.get_entry('candidate')['retry_key']
    box.stub_results = [first_result, 'success']
    box.flush(now=NOW, keys=['candidate'])
    box.flush(now=NOW+timedelta(minutes=1), keys=['candidate'])
    entry = box.get_entry('candidate')
    assert entry['state'] == final_state
    assert entry['retry_key'] == original
    assert [attempt['retry_key'] for attempt in entry['attempts']] == [original, original]


@pytest.mark.parametrize('mutation', ['row', 'attempt', 'missing'])
def test_unknown_retry_binding_corruption_is_rejected(queue, mutation):
    ledger, box = queue
    add_candidate(ledger, box, minute=59)
    box.stub_results = ['timeout']
    box.flush(now=NOW, keys=['candidate'])
    attempts = json.loads(box._con.execute(
        "SELECT attempts FROM outbox WHERE key='candidate'").fetchone()[0])
    if mutation == 'row':
        box._con.execute("UPDATE outbox SET retry_key='different' WHERE key='candidate'")
    else:
        if mutation == 'attempt':
            attempts[0]['retry_key'] = 'different'
        else:
            attempts[0].pop('retry_key')
        box._con.execute('UPDATE outbox SET attempts=? WHERE key=?',
                         [json.dumps(attempts), 'candidate'])
    before = snapshot(ledger, box)
    with pytest.raises(ValueError, match='保存済み通知キュー'):
        box.flush(now=NOW+timedelta(minutes=1), keys=['candidate'])
    assert snapshot(ledger, box) == before


def test_pending_empty_attempts_remain_valid(queue):
    ledger, box = queue
    add_candidate(ledger, box)
    entry = box.get_entry('candidate')
    assert entry['state'] == 'PENDING' and entry['attempts'] == []
