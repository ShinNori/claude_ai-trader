"""Independent selection boundaries for offline notification delivery."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'common/tests/phase2')]
import test_notify as n
from aitrader_ops.notify import Notifier

offline = n.offline
ledger = n.ledger


def enqueue(box, key):
    box.enqueue(key=key, kind='RISK', message=n.message('RISK'),
                proposal_id=None, now=n.NOW)


def test_none_preserves_full_flush(tmp_path, ledger):
    with closing(n.notifier(tmp_path, ledger, ['success', 'success'])) as box:
        enqueue(box, 'a'); enqueue(box, 'b')
        assert [r['key'] for r in box.flush(now=n.NOW, keys=None)] == ['a', 'b']
        assert box.status(now=n.NOW)['monthly_used'] == 2


@pytest.mark.parametrize('keys', [[], ()])
def test_empty_selection_is_noop(tmp_path, ledger, keys):
    with closing(n.notifier(tmp_path, ledger, ['success'])) as box:
        enqueue(box, 'a')
        before, status, seq = box.get_entry('a'), box.status(now=n.NOW), ledger.seq()
        assert box.flush(now=n.NOW, keys=keys) == []
        assert box.get_entry('a') == before
        assert box.status(now=n.NOW) == status
        assert ledger.seq() == seq
        assert box.stub_results == ['success']


@pytest.mark.parametrize('keys', ['a', 1, True, {'a'}, {'a': 1}, [''], [None], [1], ['a', '']])
def test_invalid_selection_rejected_before_delivery(tmp_path, ledger, keys):
    with closing(n.notifier(tmp_path, ledger, ['success'])) as box:
        enqueue(box, 'a')
        before = box.get_entry('a')
        with pytest.raises(ValueError):
            box.flush(now=n.NOW, keys=keys)
        assert box.get_entry('a') == before
        assert box.stub_results == ['success']
        assert box.status(now=n.NOW)['monthly_used'] == 0


def test_unknown_key_rejected_before_known_key_delivery(tmp_path, ledger):
    with closing(n.notifier(tmp_path, ledger, ['success'])) as box:
        enqueue(box, 'a')
        before = box.get_entry('a')
        with pytest.raises(KeyError):
            box.flush(now=n.NOW, keys=['a', 'missing'])
        assert box.get_entry('a') == before
        assert box.stub_results == ['success']
        assert box.status(now=n.NOW)['monthly_used'] == 0


@pytest.mark.parametrize('container', [list, tuple])
def test_duplicate_key_delivers_once(tmp_path, ledger, container):
    with closing(n.notifier(tmp_path, ledger, ['success', 'timeout'])) as box:
        enqueue(box, 'a'); enqueue(box, 'b')
        assert len(box.flush(now=n.NOW, keys=container(['a', 'a']))) == 1
        assert len(box.get_entry('a')['attempts']) == 1
        assert box.get_entry('b')['state'] == 'PENDING'
        assert box.stub_results == ['timeout']


@pytest.mark.parametrize('state', ['PENDING', 'UNKNOWN', 'SENDING'])
def test_unselected_entry_untouched_including_stale_claim(tmp_path, ledger, state):
    with closing(n.notifier(tmp_path, ledger, ['failure'])) as box:
        enqueue(box, 'outside'); enqueue(box, 'selected')
        if state != 'PENDING':
            # A prior process's durable state, including an expired claim that
            # an unrestricted flush would recover and alert about.
            with closing(sqlite3.connect(tmp_path/'notify.sqlite')) as con:
                con.execute('UPDATE outbox SET state=?, month=?, claimed_by=?, claimed_at=? WHERE key=?',
                            [state, '2026-09', 'old-worker',
                             (n.NOW-timedelta(hours=1)).isoformat(), 'outside'])
                con.commit()
        before, status = box.get_entry('outside'), box.status(now=n.NOW)
        result = box.flush(now=n.NOW, keys=['selected'])
        assert [r['key'] for r in result] == ['selected']
        assert box.get_entry('outside') == before
        assert box.status(now=n.NOW) == status
        assert box.stub_results == []


def test_selected_entry_obeys_budget_reserved_by_unselected_unknown(tmp_path, ledger):
    settings = dict(n.SETTINGS, line=dict(n.SETTINGS['line'], monthly_budget=1))
    with closing(Notifier(ledger=ledger, state_path=tmp_path/'notify.sqlite',
                          settings=settings, transport='stub', stub_results=['timeout', 'success'])) as box:
        enqueue(box, 'outside'); enqueue(box, 'selected')
        box.flush(now=n.NOW, keys=['outside'])
        before = box.get_entry('outside')
        results = box.flush(now=n.NOW, keys=['selected'])
        assert results[0]['state'] == 'BUDGET_BLOCKED'
        assert box.get_entry('outside') == before
        assert box.get_entry('selected')['state'] == 'PENDING'
        assert box.get_entry('selected')['attempts'] == []
        assert box.status(now=n.NOW)['monthly_used'] == 1
        assert box.stub_results == ['success']
