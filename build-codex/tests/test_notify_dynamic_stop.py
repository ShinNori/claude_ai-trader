"""Changing STOP decisions must protect attempts, budget and reservations."""
from contextlib import closing

import pytest

import test_runner  # establishes the shared acceptance-fixture import path
from test_notify import ledger, offline, NOW, SETTINGS, proposal, enqueue
from aitrader_ops.notify import Notifier


def create(tmp_path, ledger, **kwargs):
    return Notifier(ledger=ledger, state_path=tmp_path/'dynamic.sqlite', settings=SETTINGS,
                    transport='stub', **kwargs)


@pytest.mark.parametrize('first', ['failure', 'timeout'])
def test_new_dynamic_stop_preserves_previous_attempt_and_budget(tmp_path, ledger, first):
    stopped = [False]
    with closing(create(tmp_path, ledger, stop_check=lambda: stopped[0],
                        stub_results=[first, 'success'])) as box:
        enqueue(box)
        box.flush(now=NOW)
        before = box.get_entry('k1')
        reserved = ledger.reserved()
        stopped[0] = True
        outcome = box.flush(now=NOW)
        assert outcome[0]['state'] == ('UNKNOWN' if first == 'timeout' else 'STOPPED')
        assert box.get_entry('k1')['attempts'] == before['attempts']
        assert box.get_entry('k1')['month'] == before['month']
        assert ledger.reserved() == reserved
        assert box.status(now=NOW)['monthly_used'] == int(first == 'timeout')


def test_false_callback_cannot_cancel_persistent_stop(tmp_path, ledger):
    with closing(create(tmp_path, ledger, stop_check=lambda: False, stub_results=['success'])) as box:
        enqueue(box)
        box.stop(now=NOW)
        assert box.flush(now=NOW)[0]['state'] == 'STOPPED'
        assert box.get_entry('k1')['attempts'] == []
        assert box.status(now=NOW)['monthly_used'] == 0


@pytest.mark.parametrize('kind', ['EXIT', 'RECONCILE'])
def test_stop_callback_does_not_block_exit_or_status(tmp_path, ledger, kind):
    from aitrader_ops.models import PositionIn
    from aitrader_ops.ledger import Ledger
    # SELL needs a real synthetic holding, separate from the BUY fixture.
    with closing(Ledger(tmp_path/'held.sqlite')) as held:
        held.init_snapshot(1000000, [PositionIn('6857', 100, 1000.)], [], NOW)
        sell = proposal(proposal_id='sell', side='SELL')
        held.create_notice(sell, at=NOW)
        held.set_notice_state('sell', 'APPROVED', NOW)
        with closing(create(tmp_path, held, stop_check=lambda: True, stub_results=['success'])) as box:
            box.enqueue(key='allowed', kind=kind, message={'type':'text','text':'synthetic'},
                        proposal_id='sell' if kind == 'EXIT' else None, now=NOW)
            assert box.flush(now=NOW)[0]['state'] == 'SENT'
            assert len(box.get_entry('allowed')['attempts']) == 1


@pytest.mark.parametrize('value', [1, False, 'stop'])
def test_noncallable_stop_check_rejected(tmp_path, ledger, value):
    with pytest.raises((TypeError, ValueError)):
        with closing(create(tmp_path, ledger, stop_check=value)):
            pass


@pytest.mark.parametrize('value', [None, 1, 'false'])
def test_nonboolean_result_cannot_send(tmp_path, ledger, value):
    with closing(create(tmp_path, ledger, stop_check=lambda: value, stub_results=['success'])) as box:
        enqueue(box)
        try:
            box.flush(now=NOW)
        except (TypeError, ValueError):
            pass
        assert box.get_entry('k1')['attempts'] == []
        assert box.get_entry('k1')['month'] is None


def test_callback_exception_cannot_send(tmp_path, ledger):
    def broken():
        raise RuntimeError('unavailable stop source')
    with closing(create(tmp_path, ledger, stop_check=broken, stub_results=['success'])) as box:
        enqueue(box)
        try:
            box.flush(now=NOW)
        except (RuntimeError, ValueError):
            pass
        assert box.get_entry('k1')['attempts'] == []
        assert box.get_entry('k1')['month'] is None


def test_default_constructor_remains_sendable(tmp_path, ledger):
    with closing(create(tmp_path, ledger, stub_results=['success'])) as box:
        enqueue(box)
        assert box.flush(now=NOW)[0]['state'] == 'SENT'
