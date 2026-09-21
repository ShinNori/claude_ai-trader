"""Independent public outbox reads: no network, CLI, or private SQL access."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'common/tests/phase2')]
import test_notify as n

offline = n.offline
ledger = n.ledger
FIELDS = {'key', 'kind', 'proposal_id', 'message', 'state', 'recipient',
          'retry_key', 'month', 'created_at', 'updated_at', 'attempts'}


def test_missing_entry_is_key_error_and_does_not_change_account(tmp_path, ledger):
    with closing(n.notifier(tmp_path, ledger)) as box:
        seq, status = ledger.seq(), box.status(now=n.NOW)
        with pytest.raises(KeyError):
            box.get_entry('not-enqueued')
        assert ledger.seq() == seq
        assert box.status(now=n.NOW) == status


def test_pending_entry_has_public_fields_without_sending(tmp_path, ledger):
    with closing(n.notifier(tmp_path, ledger)) as box:
        n.enqueue(box)
        seq, status = ledger.seq(), box.status(now=n.NOW)
        entry = box.get_entry('k1')
        assert set(entry) == FIELDS
        assert entry['key'] == 'k1'
        assert entry['kind'] == 'NEW'
        assert entry['proposal_id'] == n.proposal().proposal_id
        assert entry['message'] == n.message()
        assert entry['recipient'] == n.USER
        assert entry['state'] == 'PENDING'
        assert entry['month'] is None
        assert entry['retry_key']
        assert entry['created_at'] == entry['updated_at'] == n.NOW.isoformat()
        assert entry['attempts'] == []
        assert ledger.seq() == seq
        assert box.status(now=n.NOW) == status


def test_nested_message_and_attempt_edits_cannot_mutate_storage(tmp_path, ledger):
    with closing(n.notifier(tmp_path, ledger, ['timeout'])) as box:
        n.enqueue(box)
        box.flush(now=n.NOW)
        original = box.get_entry('k1')
        edited = box.get_entry('k1')
        edited['message']['contents']['body']['contents'][0]['text'] = 'changed'
        edited['attempts'][0]['result'] = 'success'
        edited['attempts'].append({'result': 'fabricated'})
        edited['state'] = 'SENT'
        assert box.get_entry('k1') == original


@pytest.mark.parametrize('result,state', [('timeout', 'UNKNOWN'), ('success', 'SENT')])
def test_delivery_read_is_observation_only(tmp_path, ledger, result, state):
    with closing(n.notifier(tmp_path, ledger, [result])) as box:
        n.enqueue(box)
        queued = box.get_entry('k1')
        at = n.NOW + timedelta(seconds=1)
        delivery = box.flush(now=at)[0]
        seq, status, account = ledger.seq(), box.status(now=at), ledger.replay()
        for _ in range(3):
            entry = box.get_entry('k1')
            assert entry['state'] == state == delivery['state']
            assert entry['retry_key'] == queued['retry_key'] == delivery['retry_key']
            assert entry['month'] == '2026-09'
            assert entry['created_at'] == n.NOW.isoformat()
            assert entry['updated_at'] == at.isoformat()
            assert entry['attempts'] == [{'at': at.isoformat(), 'result': result,
                                          'retry_key': queued['retry_key']}]
        assert ledger.seq() == seq
        assert ledger.replay() == account
        assert box.status(now=at) == status
        assert status['monthly_used'] == 1


def test_unknown_retry_read_retains_original_month_and_key(tmp_path, ledger):
    with closing(n.notifier(tmp_path, ledger, ['timeout', 'success'])) as box:
        at = n.NOW.replace(day=30, hour=23, minute=59, second=59)
        box.enqueue(key='reconcile', kind='RECONCILE', message=n.message('RECONCILE'),
                    proposal_id=None, now=at)
        box.flush(now=at)
        unknown = box.get_entry('reconcile')
        next_month = at + timedelta(seconds=1)
        box.flush(now=next_month)
        sent = box.get_entry('reconcile')
        assert unknown['state'] == 'UNKNOWN'
        assert sent['state'] == 'SENT'
        assert sent['month'] == unknown['month'] == '2026-09'
        assert sent['retry_key'] == unknown['retry_key']
        assert [a['result'] for a in sent['attempts']] == ['timeout', 'success']
        assert box.status(now=at)['monthly_used'] == 1
        assert box.status(now=next_month)['monthly_used'] == 0
