"""Independent v0.3.8 review. Synthetic ledgers; no external services."""
from contextlib import closing
import json
import sqlite3
from unittest.mock import patch
from datetime import timezone

import pytest
from test_ops_v037_review import (make_ledger, proposal, trade, pending, late,
                                  AT, H1, H2, H3, H4, H6, US)
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError, _State
from aitrader_ops.migrate import apply, check


def correction(l, target, eid, at, price=900, pid='target', qty=40):
    return l.report(trade(pid, eid=eid, kind='CORRECTION', qty=qty, price=price,
                          replaces_event_id=target, at=at))


@pytest.mark.parametrize('timeless', [False, True])
@pytest.mark.parametrize('cancel', [False, True])
def test_t01_effective_chain_partial_and_cancel(make_ledger, timeless, cancel):
    l = make_ledger()
    pending(l, at=None if timeless else H1)
    pending(l, 'c2', 20, H1)
    l.create_notice(proposal('target'), at=H6)
    l.resolve_pending('c1', 'target', H2, 'APPLY')
    l.resolve_pending('c2', 'target', H3, 'APPLY')
    if cancel:
        assert l.report(trade('target', eid='cancel', kind='CANCELLED', qty=40,
                              price=0, at=H3)).applied
    before, seq = l.view(), l.seq()
    assert not correction(l, 'c1', 'early', H2-US).applied
    assert l.view() == before and l.seq() == seq
    assert correction(l, 'c1', 'fix', H3.astimezone(timezone.utc)).applied
    assert not correction(l, 'fix', 'early2', H3-US).applied
    assert correction(l, 'fix', 'fix2', H4, 950).applied
    assert l.cash() == 942000 and l.positions()['6857'].qty == 60
    assert l.replay(H2).cash == 960000 and l.replay(H3).cash == 944000
    assert l.replay(H6) == l.view() == l.replay_known(l.seq())
    assert l.reserved() == (0 if cancel else 40080)
    with closing(Ledger(l.path)) as reopened:
        assert reopened.view() == l.view() and reopened.replay(H4).cash == 942000


def test_t02_sell_correction_preserves_acquisition_cost(make_ledger):
    from aitrader_ops.models import PositionIn
    l = make_ledger(positions=[PositionIn(code='6857', qty=100, avg_price=600)])
    l.create_notice(proposal('target', side='SELL'), at=AT)
    assert l.report(trade('target', eid='sell', qty=40, price=1000, at=H2)).applied
    assert correction(l, 'sell', 'fix', H3, 900).applied
    assert correction(l, 'fix', 'fix2', H4, 950).applied
    assert l.cash() == 1038000
    assert l.positions()['6857'].qty == 60 and l.positions()['6857'].avg_price == 600
    assert l.replay(H3).cash == 1036000
    with closing(Ledger(l.path)) as reopened:
        assert reopened.view() == l.view()


def insert_early(path, target='f', eid='old-fix'):
    payload = dict(event_id=eid, proposal_id='target', kind='CORRECTION', qty=40,
                   price=900, fee=0, at=H1.isoformat(), source='test',
                   broker_order_id='fix-broker', replaces_event_id=target)
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    (eid, 'TRADE', H1.isoformat(), json.dumps(payload), H4.isoformat()))


def test_t03_legacy_acceptance_marker_and_check_position(make_ledger):
    l = make_ledger()
    l.create_notice(proposal('target'), at=AT)
    assert l.report(trade('target', eid='f', qty=40, at=H2)).applied
    insert_early(l.path)
    assert check(l.path)['violation']['kind'] == 'TRADE'
    migrated = apply(l.path)
    with closing(Ledger(migrated['output'])) as old:
        assert old.cash() == 964000 and old.replay_known(migrated['old_last_seq']).cash == 964000
        # Old-rule accepted ordinary backdated correction still cannot replay in between.
        with pytest.raises(LedgerError, match='訂正対象'):
            old.replay(H1)
        assert not correction(old, 'old-fix', 'new-early', H1-US).applied
        insert_early(old.path, 'old-fix', 'injected-after-marker')
        # Force a genuinely earlier timestamp than old-fix for fault injection.
        with closing(sqlite3.connect(old.path)) as con, con:
            row = con.execute("SELECT payload FROM ledger_events WHERE event_id='injected-after-marker'").fetchone()
            p = json.loads(row[0]); p['at'] = (H1-US).isoformat()
            con.execute("UPDATE ledger_events SET at=?,payload=? WHERE event_id='injected-after-marker'",
                        (p['at'], json.dumps(p)))
        with pytest.raises(MigrationError) as error:
            check(old.path)
        assert error.value.kind == 'TRADE' and error.value.seq == old._last_seq + 1


def test_t04_rejected_context_then_apply_is_refused(make_ledger):
    l = make_ledger()
    pending(l)
    l.create_notice(proposal('target'), at=H6)
    l.set_notice_state('target', 'REJECTED', H1)
    before, seq, rows = l.view(), l.seq(), l.pending_rows()
    with pytest.raises(LedgerError, match='SKIPPED'):
        l.resolve_pending('c1', 'target', H2, 'APPLY')
    assert l.view() == before and l.seq() == seq and l.pending_rows() == rows
    assert l.replay(H2).cash == 1000000 and l.replay(H2).reserved == 0
    with closing(Ledger(l.path)) as reopened:
        assert reopened.replay(H6) == before


def test_t05_expire_does_not_load_unlisted_identity(make_ledger):
    l = make_ledger()
    l.create_notice(proposal('target'), at=H6)
    l.create_notice(proposal('other', code='7203'), at=H6)
    l.set_notice_state('target', 'APPROVED', H4)
    l.set_notice_state('target', 'SENT', H4)
    assert l.expire_notices(H1) == ['target']
    seen = []
    original = _State._on_notice_created
    def observe(state, payload):
        seen.append(payload['proposal']['proposal_id'])
        return original(state, payload)
    with patch.object(_State, '_on_notice_created', observe):
        view = l.replay(H1)
    assert seen == ['target'] and view.reserved == 0 and view.cash == 1000000


@pytest.mark.parametrize('operation', ['reopen', 'replay', 'check'])
def test_t06_missing_expire_reference_raises_ledger_error(make_ledger, operation):
    l = make_ledger()
    l.create_notice(proposal('target'), at=H6)
    l.set_notice_state('target', 'APPROVED', H4)
    l.set_notice_state('target', 'SENT', H4)
    l.expire_notices(H1)
    with closing(sqlite3.connect(l.path)) as con, con:
        con.execute("DELETE FROM ledger_events WHERE kind IN ('NOTICE_CREATED','NOTICE_STATE')")
    if operation == 'reopen':
        with pytest.raises(MigrationError):
            Ledger(l.path)
    elif operation == 'check':
        # Missing identity is invalid in legacy replay too: check must report position.
        with pytest.raises(MigrationError) as error:
            check(l.path)
        assert error.value.kind == 'EXPIRE'
    else:
        with pytest.raises(LedgerError):
            l.replay(H1)


def test_t07_timeless_correction_chain_remains_replayable(make_ledger):
    """v0.3.9: 時刻なし訂正はAPPLY時刻を継承し、先取りと前倒し再訂正を拒む。"""
    l = make_ledger()
    late(l)
    assert correction(l, 'c1', 'timeless', None).applied
    assert l.replay(H1).cash == 1000000 and l.replay(H2).cash == 964000
    before, seq = l.view(), l.seq()
    result = correction(l, 'timeless', 'dated', H1, 950)
    assert not result.applied and '訂正の業務時刻' in result.error
    assert l.view() == before and l.seq() == seq
    assert correction(l, 'timeless', 'equal', H2, 950).applied
    assert l.cash() == 962000 and l.replay(H2).cash == 962000


def test_t08_timeless_original_fill_and_dated_correction(make_ledger):
    l = make_ledger()
    l.create_notice(proposal('target'), at=AT)
    assert l.report(trade('target', eid='f', qty=40, at=None)).applied
    assert correction(l, 'f', 'dated', H1).applied
    assert l.replay(H1).cash == 964000
    with closing(Ledger(l.path)) as reopened:
        assert reopened.replay(H1) == l.replay(H1)


@pytest.mark.parametrize('at', [None, AT-US])
def test_t09_cutover_trade_has_no_apply_or_correction_bypass(make_ledger, at):
    from test_v034_contracts import provenance
    l = make_ledger(initialize=False)
    l.init_snapshot(1000000, [], [], AT, provenance=provenance())
    l.create_notice(proposal('target'), at=H6)
    assert l.report(trade('target', eid='blocked', qty=40, at=at)).pending
    before, seq, rows = l.view(), l.seq(), l.pending_rows()
    with pytest.raises(LedgerError, match='cutover'):
        l.resolve_pending('blocked', 'target', H2, 'APPLY')
    assert not correction(l, 'blocked', 'fix', H3).applied
    assert l.view() == before and l.seq() == seq and l.pending_rows() == rows
