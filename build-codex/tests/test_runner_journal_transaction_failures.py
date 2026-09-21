"""Journal finalization rollback must not hide its triggering failure."""
import sqlite3

import pytest

from test_runner import setup, DAY
from aitrader import runner
from aitrader_ops.ledger import Ledger


class InjectedInterrupt(BaseException):
    pass


class FaultConnection:
    def __init__(self, connection, fail_sql, original):
        self.connection = connection
        self.fail_sql = fail_sql
        self.original = original
        self.injected = False
        self.rollback_seen = False

    def execute(self, sql, parameters=()):
        if not self.injected and sql.startswith(self.fail_sql):
            self.injected = True
            raise self.original
        if sql == 'ROLLBACK':
            self.rollback_seen = True
            self.connection.execute(sql)
            raise RuntimeError('secondary rollback failure')
        return self.connection.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self.connection, name)


@pytest.mark.parametrize('failure_type', [RuntimeError, InjectedInterrupt])
@pytest.mark.parametrize('fail_sql', ['INSERT INTO outbox', 'COMMIT'])
def test_journal_failure_keeps_intent_reservation_and_original_exception(
        setup, monkeypatch, fail_sql, failure_type):
    home, execute = setup
    real_connect = sqlite3.connect
    fault = None

    def connect(database, *args, **kwargs):
        nonlocal fault
        connection = real_connect(database, *args, **kwargs)
        if str(database).endswith('orchestration.sqlite'):
            original = failure_type('original journal failure')
            fault = FaultConnection(connection, fail_sql, original)
            return fault
        return connection

    monkeypatch.setattr(runner.sqlite3, 'connect', connect)
    with pytest.raises(failure_type, match='original journal failure') as caught:
        execute()

    assert fault is not None and fault.injected and fault.rollback_seen
    assert caught.value is fault.original
    with real_connect(home/'orchestration.sqlite', timeout=1) as con:
        assert con.execute("SELECT state FROM candidates WHERE pid='buy1'").fetchone() == ('INTENT',)
        assert con.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
        con.execute('BEGIN IMMEDIATE')
        con.execute('ROLLBACK')
    ledger = Ledger(home/'ledger.sqlite')
    try:
        assert ledger.notice('buy1')['notice_state'] == 'APPROVED'
        assert ledger.reserved() > 0
    finally:
        ledger.close()
    if failure_type is RuntimeError:
        assert (home/'runs'/str(DAY)/'run1'/'failure.json').is_file()
