"""Durable notification rows are checked as one batch before effects."""
import json
import socket
import subprocess

import pytest

from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier
from test_v041_order4_review_claude import NOW, SETTINGS, proposal


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('external I/O is forbidden')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


@pytest.fixture
def queue(tmp_path):
    ledger = Ledger(tmp_path/'ledger.sqlite')
    ledger.init_snapshot(1_000_000, [], [], NOW.replace(hour=6))
    item = proposal()
    ledger.create_notice(item, at=NOW)
    ledger.set_notice_state(item.proposal_id, 'APPROVED', NOW)
    notifier = Notifier(ledger=ledger, state_path=tmp_path/'notify.sqlite',
                        settings=SETTINGS, transport='stub')
    yield ledger, notifier, item
    notifier.close()
    ledger.close()


def enqueue_candidate(notifier, item, key='candidate'):
    return notifier.enqueue(key=key, kind='NEW',
                            message={'type': 'text', 'text': 'fixed'},
                            proposal_id=item.proposal_id, now=NOW)


def unchanged(notifier, ledger, key):
    row = notifier._con.execute(
        'SELECT state,attempts,claimed_by,claimed_at,month FROM outbox WHERE key=?',
        [key]).fetchone()
    return (row, notifier._con.execute(
        'SELECT name,value FROM kv ORDER BY name').fetchall(), ledger.seq(),
        ledger.notice(proposal().proposal_id)['notice_state'])


@pytest.mark.parametrize(('column', 'value'), [
    ('kind', 'BROKEN'),
    ('message', json.dumps({'type': 'text', 'text': 'changed'})),
    ('message', '{broken-json'),
    ('content_hash', '0'*64),
    ('recipient', 'different-recipient'),
])
def test_flush_rejects_persisted_integrity_error_before_effects(
        queue, column, value):
    ledger, notifier, item = queue
    enqueue_candidate(notifier, item)
    notifier._con.execute(f'UPDATE outbox SET {column}=? WHERE key=?',
                          [value, 'candidate'])
    before = unchanged(notifier, ledger, 'candidate')
    notifier.stub_results = ['success']
    with pytest.raises(ValueError, match='保存済み通知キュー'):
        notifier.flush(now=NOW)
    assert unchanged(notifier, ledger, 'candidate') == before
    assert notifier.stub_results == ['success']


def test_later_corrupt_row_blocks_first_row_before_claim_alert_or_budget(queue):
    ledger, notifier, _ = queue
    notifier.enqueue(key='first', kind='RISK',
                     message={'type': 'text', 'text': 'first'},
                     proposal_id=None, now=NOW)
    notifier.enqueue(key='second', kind='RISK',
                     message={'type': 'text', 'text': 'second'},
                     proposal_id=None, now=NOW)
    notifier._con.execute("UPDATE outbox SET message='{bad' WHERE key='second'")
    before = notifier.get_entry('first')
    notifier.stub_results = ['success', 'success']
    with pytest.raises(ValueError, match='保存済み通知キュー'):
        notifier.flush(now=NOW, keys=['first', 'second'])
    assert notifier.get_entry('first') == before
    assert notifier._con.execute('SELECT count(*) FROM kv').fetchone()[0] == 0
    assert notifier.stub_results == ['success', 'success']


def test_deep_persisted_message_is_fixed_error_before_effects(queue):
    ledger, notifier, item = queue
    enqueue_candidate(notifier, item)
    deep = '{"type":' + '['*2000 + '0' + ']'*2000 + '}'
    notifier._con.execute('UPDATE outbox SET message=? WHERE key=?',
                          [deep, 'candidate'])
    before = unchanged(notifier, ledger, 'candidate')
    with pytest.raises(ValueError, match='保存済み通知キュー'):
        notifier.flush(now=NOW, keys=['candidate'])
    assert unchanged(notifier, ledger, 'candidate') == before


def test_new_changed_to_valid_exit_cannot_bypass_stop(queue):
    ledger, notifier, item = queue
    enqueue_candidate(notifier, item)
    notifier.stop(now=NOW, event_id='stop-before-flush')
    notifier._con.execute("UPDATE outbox SET kind='EXIT' WHERE key='candidate'")
    before = unchanged(notifier, ledger, 'candidate')
    notifier.stub_results = ['success']
    with pytest.raises(ValueError, match='保存済み通知キュー'):
        notifier.flush(now=NOW, keys=['candidate'])
    assert unchanged(notifier, ledger, 'candidate') == before
    assert notifier.stub_results == ['success']


def test_reconcile_rejects_corrupt_sent_row_before_ledger_change(queue):
    ledger, notifier, item = queue
    enqueue_candidate(notifier, item)
    notifier._con.execute(
        "UPDATE outbox SET state='SENT',month='2026-09',message='{bad' "
        "WHERE key='candidate'")
    before_seq = ledger.seq()
    assert ledger.notice(item.proposal_id)['notice_state'] == 'APPROVED'
    with pytest.raises(ValueError, match='保存済み通知キュー'):
        notifier.reconcile_sent(now=NOW, keys=['candidate'])
    assert ledger.seq() == before_seq
    assert ledger.notice(item.proposal_id)['notice_state'] == 'APPROVED'


def test_valid_failure_then_success_retry_remains_compatible(queue):
    ledger, notifier, item = queue
    enqueue_candidate(notifier, item)
    notifier.stub_results = ['failure', 'success']
    assert notifier.flush(now=NOW, keys=['candidate'])[0]['state'] == 'PENDING'
    assert notifier.flush(now=NOW, keys=['candidate'])[0]['state'] == 'SENT'
    assert notifier.get_entry('candidate')['state'] == 'SENT'
    assert ledger.notice(item.proposal_id)['notice_state'] == 'SENT'


def test_get_entry_shape_is_unchanged_and_ignores_other_corrupt_row(queue):
    _, notifier, _ = queue
    notifier.enqueue(key='good', kind='RISK', message={'type': 'text', 'text': 'good'},
                     proposal_id=None, now=NOW)
    notifier.enqueue(key='bad', kind='RISK', message={'type': 'text', 'text': 'bad'},
                     proposal_id=None, now=NOW)
    notifier._con.execute("UPDATE outbox SET content_hash='broken' WHERE key='bad'")
    entry = notifier.get_entry('good')
    assert set(entry) == {'key', 'kind', 'proposal_id', 'message', 'state', 'recipient',
                          'retry_key', 'month', 'created_at', 'updated_at', 'attempts'}
    assert entry['message'] == {'type': 'text', 'text': 'good'}


def test_empty_selection_ignores_unrelated_corruption(queue):
    ledger, notifier, item = queue
    enqueue_candidate(notifier, item)
    notifier._con.execute("UPDATE outbox SET message='{bad' WHERE key='candidate'")
    before = unchanged(notifier, ledger, 'candidate')
    assert notifier.flush(now=NOW, keys=[]) == []
    assert notifier.reconcile_sent(now=NOW, keys=[]) == []
    assert unchanged(notifier, ledger, 'candidate') == before


def test_flush_selected_good_row_ignores_unselected_corrupt_row(queue):
    _, notifier, _ = queue
    for key in ('good', 'bad'):
        notifier.enqueue(key=key, kind='RISK',
                         message={'type': 'text', 'text': key},
                         proposal_id=None, now=NOW)
    notifier._con.execute("UPDATE outbox SET message='{bad' WHERE key='bad'")
    notifier.stub_results = ['success']
    assert notifier.flush(now=NOW, keys=['good'])[0]['state'] == 'SENT'
    assert notifier._con.execute(
        "SELECT state FROM outbox WHERE key='bad'").fetchone()[0] == 'PENDING'


def test_reconcile_selected_good_row_ignores_unselected_corrupt_row(queue):
    ledger, notifier, first = queue
    second = proposal(proposal_id='A1-20260909-7203-02', code='7203')
    ledger.create_notice(second, at=NOW)
    ledger.set_notice_state(second.proposal_id, 'APPROVED', NOW)
    enqueue_candidate(notifier, first, 'good')
    notifier.enqueue(key='bad', kind='NEW',
                     message={'type': 'text', 'text': 'bad'},
                     proposal_id=second.proposal_id, now=NOW)
    notifier._con.execute("UPDATE outbox SET state='SENT',month='2026-09'")
    notifier._con.execute("UPDATE outbox SET content_hash='broken' WHERE key='bad'")
    assert notifier.reconcile_sent(now=NOW, keys=['good']) == ['good']
    assert ledger.notice(first.proposal_id)['notice_state'] == 'SENT'
    assert ledger.notice(second.proposal_id)['notice_state'] == 'APPROVED'
