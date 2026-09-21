"""v0.3.9 independent evidence. All databases and CLI input are synthetic."""
from contextlib import closing
from datetime import timezone, timedelta
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest
from test_ops_v037_review import (make_ledger, proposal, trade, csv, pending,
                                  AT, H1, H2, H3, H4, H6, US)
from test_ops_v038_review import correction
from aitrader_ops.ledger import Ledger, LedgerError, MigrationError
from aitrader_ops.migrate import apply, check


def events(path):
    with closing(sqlite3.connect(path)) as con:
        return con.execute('SELECT seq,kind,at,payload FROM ledger_events ORDER BY seq').fetchall()


@pytest.mark.parametrize('source', ['csv_explicit', 'csv_pending', 'trade'])
@pytest.mark.parametrize('timeless_source', [False, True])
def test_u01_inheritance_chain_by_source(make_ledger, source, timeless_source):
    l = make_ledger()
    source_at = None if timeless_source else H1
    l.create_notice(proposal('target'), at=H6)
    if source == 'trade':
        assert l.report(trade('target', eid='f', qty=40, at=source_at)).applied
    else:
        result = l.import_csv_fills([csv('unknown' if source == 'csv_pending' else 'target',
                                        eid='f', qty=40, at=source_at)])
        if source == 'csv_pending' or timeless_source:
            assert result.pending == ['f']
            l.resolve_pending('f', 'target', H2.astimezone(timezone.utc), 'APPLY')
        else:
            assert result.applied == ['f']
    initial_seq = l.seq()
    assert correction(l, 'f', 't1', None).applied
    assert correction(l, 't1', 't2', None, 950).applied
    threshold = H2 if source == 'csv_pending' or (source == 'csv_explicit' and timeless_source) else source_at
    if threshold is not None:
        assert l.replay(threshold-US).cash == 1000000
        assert l.replay(threshold-US).positions == {}
        before, seq = l.view(), l.seq()
        result = correction(l, 't2', 'early', threshold-US)
        assert not result.applied and '訂正の業務時刻' in result.error
        assert l.view() == before and l.seq() == seq
    at = threshold or H1
    assert l.replay(at).cash == 962000 and l.replay(at).reserved == 0
    assert correction(l, 't2', 'dated', at.astimezone(timezone(timedelta(hours=-5))), 940).applied
    before_rows = events(l.path)
    assert l.replay(at).cash == 962400 and l.replay_known(initial_seq).cash == 960000
    assert l.replay_known(l.seq()) == l.view() == l.replay(H6)
    # Display and stored business times remain None; replay must not rewrite the database.
    timeless_rows = [r for r in before_rows if json.loads(r[3]).get('event_id') in ('t1', 't2')]
    assert len(timeless_rows) == 2
    assert all(r[2] is None and json.loads(r[3])['at'] is None for r in timeless_rows)
    assert events(l.path) == before_rows
    with closing(Ledger(l.path)) as reopened:
        assert reopened.replay(at) == l.replay(at) and reopened.view() == l.view()


@pytest.mark.parametrize('reverse_sequence', [False, True])
def test_u02_record_order_kept_for_dated_and_timeless_corrections(make_ledger, reverse_sequence):
    l = make_ledger()
    l.create_notice(proposal('target'), at=H6)
    for eid, qty, at in [('a', 40, H1), ('b', 20, H2)]:
        assert l.report(trade('target', eid=eid, qty=qty, at=at, broker_order_id=eid)).applied
    operations = [('a', 'a-dated', H3, 900, 40), ('b', 'b-timeless', None, 950, 20)]
    if reverse_sequence:
        operations.reverse()
    for target, eid, at, price, qty in operations:
        assert correction(l, target, eid, at, price, qty=qty).applied
    assert correction(l, 'a-dated', 'a-timeless', None, 940).applied
    assert l.replay(H1).cash == 960000
    assert l.replay(H2).cash == 941000
    assert l.replay(H3).cash == 943400
    assert l.replay(H3).positions['6857'].qty == 60
    assert l.replay(H6) == l.view()


@pytest.mark.parametrize('missing', ['notice', 'correction_target'])
@pytest.mark.parametrize('operation', ['replay', 'reopen', 'check'])
def test_u03_missing_references_remain_positioned_errors(make_ledger, missing, operation):
    l = make_ledger()
    l.create_notice(proposal('target'), at=H6)
    if missing == 'notice':
        l.set_notice_state('target', 'APPROVED', H1)
        kind, match = 'NOTICE_STATE', '通知 target がありません'
        with closing(sqlite3.connect(l.path)) as con, con:
            con.execute("DELETE FROM ledger_events WHERE kind='NOTICE_CREATED'")
    else:
        assert l.report(trade('target', eid='f', qty=40, at=H2)).applied
        assert correction(l, 'f', 't', None).applied
        kind, match = 'TRADE', '訂正対象 f'
        with closing(sqlite3.connect(l.path)) as con, con:
            con.execute("DELETE FROM ledger_events WHERE event_id='trade:f'")
    bad_seq = events(l.path)[-1][0]
    if operation == 'replay':
        with pytest.raises(LedgerError, match=match):
            l.replay(H1)
    else:
        with pytest.raises(MigrationError, match=match) as exc:
            if operation == 'check':
                check(l.path)
            else:
                with closing(Ledger(l.path)):
                    pass
        assert exc.value.kind == kind and exc.value.seq == bad_seq


def inject_correction(path, at, eid='early', target='t'):
    p = dict(event_id=eid, proposal_id='target', kind='CORRECTION', qty=40, price=950,
             fee=0, at=at.isoformat(), source='test', broker_order_id='b', replaces_event_id=target)
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    ('trade:'+eid, 'TRADE', p['at'], json.dumps(p), H6.isoformat()))


@pytest.mark.parametrize('after_marker', [False, True])
def test_u04_legacy_policy_boundary_and_real_check_cli(make_ledger, after_marker):
    l = make_ledger()
    l.create_notice(proposal('target'), at=AT)
    assert l.report(trade('target', eid='f', qty=40, at=H2)).applied
    assert correction(l, 'f', 't', None).applied
    path = l.path
    if after_marker:
        path = apply(path)['output']
    inject_correction(path, H1)
    from pathlib import Path
    path = Path(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    proc = subprocess.run([sys.executable, '-m', 'aitrader_ops.migrate', '--check', str(path)],
                          capture_output=True, text=True, encoding='utf-8', env=dict(os.environ, PYTHONUTF8='1'))
    output = json.loads(proc.stdout)
    if after_marker:
        assert proc.returncode == 1 and output['error'] == 'MigrationError'
        violation = output
    else:
        assert proc.returncode == 0
        violation = output['violation']
        migrated = apply(path)
        with closing(Ledger(migrated['output'])) as old:
            assert old.cash() == 962000
            assert old.replay_known(migrated['old_last_seq']).cash == 962000
            # Pre-marker Q09 was not enforced; the new-rule operation is rejected.
            r = correction(old, 'early', 'new-too-early', H1-US)
            assert not r.applied and '訂正の業務時刻' in r.error
    assert violation['kind'] == 'TRADE' and violation['seq'] == events(path)[-1][0]
    assert '訂正の業務時刻' in violation['reason']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('source_at', [None, AT-US])
def test_u05_cutover_pending_cannot_become_correction_target(make_ledger, source_at):
    from test_v034_contracts import provenance
    l = make_ledger(initialize=False)
    l.init_snapshot(1000000, [], [], AT, provenance=provenance())
    assert l.report(trade('target', eid='f', qty=40, at=source_at)).pending
    l.create_notice(proposal('target'), at=H6)
    before = l.view()
    with pytest.raises(LedgerError, match='cutover'):
        l.resolve_pending('f', 'target', H2, 'APPLY')
    result = correction(l, 'f', 't', None)
    assert result.pending and not result.applied
    with pytest.raises(LedgerError, match='cutover'):
        l.resolve_pending('t', 'target', H3, 'APPLY')
    assert l.view() == before
    assert l.replay(H2).cash == 1000000 and l.replay(H2).reserved == 0
    with closing(Ledger(l.path)) as reopened:
        assert reopened.pending_rows() == l.pending_rows()
