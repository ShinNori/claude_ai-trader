"""Transaction cleanup keeps the original failure, including interruption."""
from datetime import timedelta

import pytest

from aitrader_ops.notify import Notifier
from test_v041_order4_review_claude import NOW, SETTINGS, ledger


class InjectedInterrupt(BaseException):
    pass


class ConnectionFault:
    """Run the real rollback, then raise a secondary failure from that call."""
    def __init__(self, connection, fail_text, original):
        self.connection = connection
        self.fail_text = fail_text
        self.original = original
        self.injected = False
        self.rollback_seen = False

    def execute(self, sql, parameters=()):
        if not self.injected and self.fail_text in sql:
            self.injected = True
            raise self.original
        if sql == 'ROLLBACK':
            self.rollback_seen = True
            self.connection.execute(sql)
            raise RuntimeError('secondary rollback failure')
        return self.connection.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self.connection, name)


@pytest.fixture
def box(tmp_path, ledger):
    notifier = Notifier(ledger=ledger, state_path=tmp_path/'notification.sqlite',
                        settings=SETTINGS, transport='stub', stub_results=[])
    yield notifier
    notifier.close()


@pytest.mark.parametrize('failure_type', [RuntimeError, InjectedInterrupt])
@pytest.mark.parametrize('operation,fail_text', [
    ('stop', 'INSERT INTO controls'),
    ('resume', 'INSERT OR REPLACE INTO kv'),
    ('enqueue', 'INSERT INTO outbox'),
    ('flush', "UPDATE outbox SET state='SENDING'"),
])
def test_original_failure_survives_secondary_rollback_failure(
        box, operation, fail_text, failure_type, monkeypatch):
    if operation == 'resume':
        box.stop(now=NOW)
    if operation == 'flush':
        box.enqueue(key='risk', kind='RISK', message={'type': 'text', 'text': 'x'},
                    proposal_id=None, now=NOW)
        monkeypatch.setattr(box, '_send_stub',
                            lambda: pytest.fail('send must not start'))

    original_connection = box._con
    original = failure_type('original transaction failure')
    fault = ConnectionFault(original_connection, fail_text, original)
    box._con = fault
    call = {
        'stop': lambda: box.stop(now=NOW + timedelta(minutes=1)),
        'resume': lambda: box.resume(now=NOW + timedelta(minutes=1),
                                    reconciled_at=NOW),
        'enqueue': lambda: box.enqueue(
            key='risk', kind='RISK', message={'type': 'text', 'text': 'x'},
            proposal_id=None, now=NOW),
        'flush': lambda: box.flush(now=NOW),
    }[operation]

    with pytest.raises(failure_type, match='original transaction failure') as caught:
        call()
    assert caught.value is original
    assert fault.injected and fault.rollback_seen

    # The real rollback ran before its injected secondary error: no open lock or
    # partial row remains, and the same connection can begin again.
    box._con = original_connection
    original_connection.execute('BEGIN IMMEDIATE')
    original_connection.execute('ROLLBACK')
    if operation == 'stop':
        assert box.is_stopped() is False
    elif operation == 'resume':
        assert box.is_stopped() is True
    elif operation == 'enqueue':
        assert original_connection.execute(
            "SELECT count(*) FROM outbox WHERE key='risk'").fetchone()[0] == 0
    else:
        entry = box.get_entry('risk')
        assert entry['state'] == 'PENDING' and entry['attempts'] == []


def test_four_normal_transactions_remain_usable(box):
    assert box.stop(now=NOW) == 'STOPPED'
    assert box.resume(now=NOW + timedelta(minutes=1), reconciled_at=NOW) == 'RESUMED'
    assert box.enqueue(key='risk', kind='RISK', message={'type': 'text', 'text': 'x'},
                       proposal_id=None, now=NOW)['state'] == 'PENDING'
    box.stub_results.append('failure')
    assert box.flush(now=NOW)[0]['state'] == 'PENDING'
