"""Diagnostic COMPLETE must not hide divergent journals or invalid histories."""
import json
import sqlite3
from contextlib import closing

import pytest
from test_runner_diagnostics import setup, diagnose
from test_notification_plan import offline


def test_candidate_result_divergence_not_complete(setup,monkeypatch):
    home,execute=setup
    execute()
    with closing(sqlite3.connect(home/'orchestration.sqlite')) as con:
        con.execute("UPDATE candidates SET result=? WHERE pid='buy1'",
                    [json.dumps({'proposal_id':'buy1','status':'REJECTED','gate':{'allowed':False}})])
        con.commit()
    assert diagnose(home,monkeypatch)['classification']!='COMPLETE'


def test_duplicate_notice_creation_not_complete(setup,monkeypatch):
    home,execute=setup
    execute()
    with closing(sqlite3.connect(home/'ledger.sqlite')) as con:
        rows=con.execute("SELECT kind,at,payload,recorded_at FROM ledger_events WHERE kind IN ('NOTICE_CREATED','NOTICE_STATE') ORDER BY seq").fetchall()
        for i,(kind,at,payload,recorded) in enumerate(rows):
            con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                        [f'duplicate-{i}',kind,at,payload,recorded])
        con.commit()
    assert diagnose(home,monkeypatch)['classification']!='COMPLETE'


def test_invalid_created_to_sent_transition_not_complete(setup,monkeypatch):
    home,execute=setup
    execute()
    with closing(sqlite3.connect(home/'ledger.sqlite')) as con:
        seq,payload=con.execute("SELECT seq,payload FROM ledger_events WHERE kind='NOTICE_STATE'").fetchone()
        value=json.loads(payload);value['state']='SENT'
        con.execute('UPDATE ledger_events SET payload=? WHERE seq=?',[json.dumps(value),seq])
        con.commit()
    assert diagnose(home,monkeypatch)['classification']!='COMPLETE'
