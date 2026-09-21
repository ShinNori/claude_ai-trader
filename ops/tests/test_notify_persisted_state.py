"""Persisted outbox states are validated without widening payload inspection."""
import socket
import subprocess

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
    item = proposal()
    ledger.create_notice(item, at=NOW)
    ledger.set_notice_state(item.proposal_id, 'APPROVED', NOW)
    notifier = Notifier(ledger=ledger, state_path=tmp_path/'notify.sqlite',
                        settings=SETTINGS, transport='stub')
    notifier.enqueue(key='candidate', kind='NEW', message={'type': 'text'},
                     proposal_id=item.proposal_id, now=NOW)
    notifier.enqueue(key='other', kind='RISK', message={'type': 'text'},
                     proposal_id=None, now=NOW)
    yield ledger, notifier
    notifier.close()
    ledger.close()


def corrupt_state(notifier, key='candidate'):
    notifier._con.execute("UPDATE outbox SET state='BOGUS' WHERE key=?", [key])


def test_get_entry_rejects_unknown_state(queue):
    _, notifier = queue
    corrupt_state(notifier)
    with pytest.raises(ValueError, match='保存済み通知キュー'):
        notifier.get_entry('candidate')


@pytest.mark.parametrize('method', ['flush', 'reconcile_sent'])
@pytest.mark.parametrize('keys', [None, ['candidate']])
def test_operation_rejects_unknown_state_before_effects(queue, method, keys):
    ledger, notifier = queue
    corrupt_state(notifier)
    before = (ledger.seq(), notifier._con.execute(
        'SELECT key,state,attempts FROM outbox ORDER BY key').fetchall(),
              notifier._con.execute('SELECT * FROM kv ORDER BY name').fetchall())
    with pytest.raises(ValueError, match='状態を確認できません'):
        getattr(notifier, method)(now=NOW, keys=keys)
    after = (ledger.seq(), notifier._con.execute(
        'SELECT key,state,attempts FROM outbox ORDER BY key').fetchall(),
             notifier._con.execute('SELECT * FROM kv ORDER BY name').fetchall())
    assert after == before


@pytest.mark.parametrize('method', ['flush', 'reconcile_sent'])
def test_empty_keys_remain_no_op_even_with_unknown_state(queue, method):
    ledger, notifier = queue
    corrupt_state(notifier)
    before = ledger.seq(), notifier._con.execute('SELECT * FROM outbox').fetchall()
    assert getattr(notifier, method)(now=NOW, keys=[]) == []
    assert (ledger.seq(), notifier._con.execute('SELECT * FROM outbox').fetchall()) == before


@pytest.mark.parametrize('method', ['flush', 'reconcile_sent'])
def test_explicit_keys_ignore_unknown_state_outside_selection(queue, method):
    _, notifier = queue
    corrupt_state(notifier, 'other')
    result = getattr(notifier, method)(now=NOW, keys=['candidate'])
    assert isinstance(result, list)
    assert notifier._con.execute(
        "SELECT state FROM outbox WHERE key='other'").fetchone()[0] == 'BOGUS'


@pytest.mark.parametrize('state', ['SENT', 'EXPIRED'])
@pytest.mark.parametrize('method', ['flush', 'reconcile_sent'])
def test_legal_terminal_state_is_not_rejected(queue, state, method):
    _, notifier = queue
    notifier._con.execute('UPDATE outbox SET state=? WHERE key=?', [state, 'other'])
    result = getattr(notifier, method)(now=NOW, keys=['other'])
    assert result == []
