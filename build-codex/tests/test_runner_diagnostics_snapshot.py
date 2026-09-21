"""Corrupt snapshots rejected by ledger replay must never diagnose COMPLETE."""
import json
import sqlite3
from contextlib import closing

import pytest
from test_runner_diagnostics import setup, diagnose
from test_notification_plan import offline
from aitrader_ops.ledger import _State, LedgerError


@pytest.mark.parametrize('corruption',['missing_cash','bool_cash','negative_cash','zero_position','missing_at'])
def test_invalid_snapshot_not_complete(setup,monkeypatch,corruption):
    home,execute=setup
    execute()
    with closing(sqlite3.connect(home/'ledger.sqlite')) as con:
        seq,payload=con.execute("SELECT seq,payload FROM ledger_events WHERE kind='SNAPSHOT'").fetchone()
        value=json.loads(payload)
        if corruption=='missing_cash': value.pop('cash')
        elif corruption=='bool_cash': value['cash']=True
        elif corruption=='negative_cash': value['cash']=-1
        elif corruption=='zero_position': value['positions']=[{'code':'6857','qty':0,'avg_price':1000}]
        else: value.pop('at')
        # Call the actual in-memory replay handler; no Ledger constructor writes.
        with pytest.raises((LedgerError,KeyError,ValueError)):
            _State().apply('SNAPSHOT',value)
        con.execute('UPDATE ledger_events SET payload=? WHERE seq=?',[json.dumps(value),seq])
        con.commit()
    assert diagnose(home,monkeypatch)['classification']!='COMPLETE'
