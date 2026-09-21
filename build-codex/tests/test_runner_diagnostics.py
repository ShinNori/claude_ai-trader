"""Read-only diagnostic invariants with no constructor or filesystem writes."""
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest
from test_runner import setup, DAY, NOW, Ledger
from test_notification_plan import offline
from aitrader_ops.notify import Notifier


def hashes(home):
    return {str(p.relative_to(home)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in home.rglob('*') if p.is_file()}


def diagnose(home,monkeypatch):
    from aitrader.runner_diagnostics import diagnose_mock_run
    def forbidden(*a,**k):
        raise AssertionError('Diagnostic must not construct ledger or deliver')
    monkeypatch.setattr(Ledger,'__init__',forbidden)
    monkeypatch.setattr(Notifier,'flush',forbidden)
    before=hashes(home)
    report=diagnose_mock_run(home,'run1',DAY)
    assert hashes(home)==before
    assert report['automatic_resume_allowed'] is False
    assert report['repaired'] is False
    return report


def test_complete_readonly(setup,monkeypatch):
    home,execute=setup
    execute()
    assert diagnose(home,monkeypatch)['classification']=='COMPLETE'


@pytest.mark.parametrize('missing',['orchestration.sqlite','ledger.sqlite'])
def test_missing_db_never_created(setup,monkeypatch,missing):
    home,execute=setup
    execute()
    (home/missing).unlink()
    assert diagnose(home,monkeypatch)['classification']=='MISSING'
    assert not (home/missing).exists()


@pytest.mark.parametrize('artifact',['manifest','proposals','result','gate_results'])
def test_tampered_artifact_conflict(setup,monkeypatch,artifact):
    home,execute=setup
    execute()
    (home/'runs'/str(DAY)/'run1'/f'{artifact}.json').write_text('{}',encoding='utf-8')
    assert diagnose(home,monkeypatch)['classification']=='CONFLICT'


def intent(home,owner='run1'):
    with closing(sqlite3.connect(home/'orchestration.sqlite')) as con:
        con.execute('UPDATE runs SET result=NULL WHERE id=?',['run1'])
        con.execute("UPDATE candidates SET state='INTENT',owner=? WHERE pid='buy1'",[owner])
        con.commit()
    with closing(sqlite3.connect(home/'ledger.sqlite')) as con:
        con.execute("DELETE FROM ledger_events WHERE kind='NOTICE_STATE'")
        con.commit()


def test_intent_created_requires_reconciliation(setup,monkeypatch):
    home,execute=setup
    execute()
    intent(home)
    report=diagnose(home,monkeypatch)
    assert report['classification']=='NEEDS_RECONCILIATION'
    assert report['candidates'][0]['ledger_state']=='CREATED'


def test_intent_owner_conflict(setup,monkeypatch):
    home,execute=setup
    execute()
    intent(home,'other-run')
    assert diagnose(home,monkeypatch)['classification']=='CONFLICT'


def test_complex_history_is_conservative(setup,monkeypatch):
    home,execute=setup
    execute()
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        ledger.expire_notices(NOW.replace(hour=10))
        # A real SENT -> EXPIRE history requires replay rather than latest-state guessing.
        ledger.set_notice_state('buy1','SENT',NOW)
        ledger.expire_notices(NOW.replace(hour=10))
    report=diagnose(home,monkeypatch)
    assert report['classification']=='NEEDS_RECONCILIATION'
    assert report['candidates'][0]['ledger_state']=='UNKNOWN'


@pytest.mark.parametrize('database',['ledger.sqlite','orchestration.sqlite'])
def test_live_wal_never_returns_stale_complete(setup,monkeypatch,database):
    home,execute=setup
    execute()
    with closing(sqlite3.connect(home/database)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        if database=='orchestration.sqlite':
            writer.execute("UPDATE candidates SET owner='live-other',state='INTENT' WHERE pid='buy1'")
        else:
            row=writer.execute("SELECT seq,payload FROM ledger_events WHERE kind='NOTICE_STATE' ORDER BY seq DESC LIMIT 1").fetchone()
            value=json.loads(row[1]); value['state']='CREATED'
            writer.execute('UPDATE ledger_events SET payload=? WHERE seq=?',[json.dumps(value),row[0]])
        writer.commit()
        assert (home/(database+'-wal')).stat().st_size>0
        content_before=writer.execute('SELECT * FROM '+('ledger_events' if database=='ledger.sqlite' else 'candidates')).fetchall()
        report=diagnose(home,monkeypatch)
        assert report['classification']=='NEEDS_RECONCILIATION'
        assert 'LIVE_DATABASE_UNSUPPORTED' in report['reasons']
        assert report['candidates']==[]
        assert writer.execute('SELECT * FROM '+('ledger_events' if database=='ledger.sqlite' else 'candidates')).fetchall()==content_before
