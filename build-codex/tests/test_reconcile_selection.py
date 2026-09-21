"""Independent crash recovery tests for run-scoped SENT reconciliation."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'common/tests/phase2'))
from test_notify import NOW, SETTINGS, proposal, offline
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier


@pytest.fixture
def crashed(tmp_path, monkeypatch):
    with closing(Ledger(tmp_path/'ledger.sqlite')) as ledger:
        ledger.init_snapshot(1_000_000, [], [], NOW-timedelta(days=1))
        for pid, code in [('a', '6857'), ('b', '7203')]:
            ledger.create_notice(proposal(proposal_id=pid, code=code), at=NOW)
            ledger.set_notice_state(pid, 'APPROVED', NOW)
        with closing(Notifier(ledger=ledger, state_path=tmp_path/'notify.sqlite',
                              settings=SETTINGS, stub_results=['success', 'success'])) as box:
            for pid in ('a', 'b'):
                box.enqueue(key=pid, kind='NEW', proposal_id=pid,
                            message={'type':'text','text':pid}, now=NOW)
            original = ledger.set_notice_state
            def crash(*args, **kwargs):
                raise RuntimeError('injected crash after durable queue success')
            monkeypatch.setattr(ledger, 'set_notice_state', crash)
            for pid in ('a', 'b'):
                with pytest.raises(RuntimeError, match='injected crash'):
                    box.flush(now=NOW, keys=[pid])
            monkeypatch.setattr(ledger, 'set_notice_state', original)
            assert all(box.get_entry(k)['state']=='SENT' for k in ('a','b'))
            assert all(ledger.notice(k)['notice_state']=='APPROVED' for k in ('a','b'))
            yield ledger, box


def test_selected_recovery_changes_only_selected_ledger(crashed):
    ledger, box = crashed
    entries = [box.get_entry(k) for k in ('a','b')]
    used = box.status(now=NOW)['monthly_used']
    seq = ledger.seq()
    assert box.reconcile_sent(now=NOW, keys=['b']) == ['b']
    assert ledger.notice('a')['notice_state']=='APPROVED'
    assert ledger.notice('b')['notice_state']=='SENT'
    assert ledger.seq()==seq+1
    assert [box.get_entry(k) for k in ('a','b')]==entries
    assert box.status(now=NOW)['monthly_used']==used==2
    assert box.reconcile_sent(now=NOW, keys=['b'])==[]
    assert ledger.seq()==seq+1


def test_duplicate_selection_applies_once(crashed):
    ledger, box=crashed
    seq=ledger.seq()
    assert box.reconcile_sent(now=NOW, keys=['a','a'])==['a']
    assert ledger.seq()==seq+1


@pytest.mark.parametrize('keys', [[], ()])
def test_empty_selection_noop(crashed, keys):
    ledger, box=crashed
    seq=ledger.seq()
    assert box.reconcile_sent(now=NOW, keys=keys)==[]
    assert ledger.seq()==seq


def test_unknown_selection_rejects_before_any_mutation(crashed):
    ledger, box=crashed
    seq=ledger.seq()
    with pytest.raises(KeyError):
        box.reconcile_sent(now=NOW, keys=['a','missing'])
    assert ledger.seq()==seq
    assert ledger.notice('a')['notice_state']=='APPROVED'


@pytest.mark.parametrize('keys', ['a', {'a'}, 1, True, [None], [''], [' '], ['a', 2]])
def test_invalid_selection_rejected_before_mutation(crashed, keys):
    ledger, box=crashed
    seq=ledger.seq()
    with pytest.raises(ValueError):
        box.reconcile_sent(now=NOW, keys=keys)
    assert ledger.seq()==seq


def test_default_recovers_all_in_queue_order(crashed):
    ledger, box=crashed
    assert box.reconcile_sent(now=NOW)==['a','b']
    assert all(ledger.notice(k)['notice_state']=='SENT' for k in ('a','b'))


def test_existing_non_sent_key_is_valid_noop(crashed):
    ledger, box=crashed
    box.enqueue(key='pending-status',kind='RISK',proposal_id=None,
                message={'type':'text','text':'synthetic'},now=NOW)
    seq=ledger.seq()
    assert box.reconcile_sent(now=NOW,keys=['pending-status'])==[]
    assert ledger.seq()==seq
    assert box.get_entry('pending-status')['state']=='PENDING'
