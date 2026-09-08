"""New contracts; older contradictory reviewer assertions remain unchanged."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'build-codex'), str(ROOT/'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, trade, AT
from test_migration import legacy
from aitrader_ops.ledger import Ledger, MigrationError
from aitrader_ops.migrate import apply


def test_f01_failed_validation_cleans_and_allows_retry(legacy, monkeypatch):
    before = legacy.read_bytes()
    original = Ledger.replay_known
    def fail(*args):
        raise RuntimeError('injected verification failure')
    monkeypatch.setattr(Ledger, 'replay_known', fail)
    with pytest.raises(RuntimeError):
        apply(legacy)
    assert not legacy.with_name(legacy.stem+'.upgraded.sqlite').exists()
    assert not list(legacy.parent.glob('*.upgrading.sqlite*'))
    assert legacy.read_bytes() == before
    monkeypatch.setattr(Ledger, 'replay_known', original)
    assert apply(legacy)['verified']


@pytest.mark.parametrize('duplicate', [False, True])
def test_f02_invalid_boundary_or_second_marker_rejected(legacy, duplicate):
    path = Path(apply(legacy)['output']) if duplicate else legacy
    with sqlite3.connect(path) as con:
        previous = con.execute('SELECT max(seq) FROM ledger_events').fetchone()[0]
        con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                    ['extra-marker','POLICY_UPGRADE',AT.isoformat(),
                     json.dumps(dict(schema_version=3,legacy_last_seq=previous if duplicate else 999,at=AT.isoformat())),AT.isoformat()])
    with pytest.raises(MigrationError) as exc:
        Ledger(path)
    assert exc.value.seq == previous+1 and exc.value.kind == 'POLICY_UPGRADE'


def test_f03_recursive_sanitizing_keeps_public_values(make_ledger):
    l = make_ledger(); l.create_notice(proposal(), at=AT)
    payload = vars(trade()).copy()
    payload.update(_rule_version=2, meta={'_context_only':True,'public':[{'_hidden':'bad','text':'_value'}]})
    assert l.report(payload).applied
    with sqlite3.connect(l.path) as con:
        stored = json.loads(con.execute("SELECT payload FROM ledger_events WHERE kind='TRADE'").fetchone()[0])
    assert '_rule_version' not in stored
    assert stored['meta'] == {'public':[{'text':'_value'}]}


def test_f04_adjust_boundary_removes_internal_names(make_ledger):
    l = make_ledger()
    l._commit('ADJUST', dict(kind='DEPOSIT', amount=1, _applied_proposal_id='ghost',
                            _rule_version=2, at=AT), AT)
    with sqlite3.connect(l.path) as con:
        stored = json.loads(con.execute("SELECT payload FROM ledger_events WHERE kind='ADJUST'").fetchone()[0])
    assert not any(key.startswith('_') for key in stored)
    assert l.cash() == 1000001
