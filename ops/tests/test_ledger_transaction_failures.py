"""Primary ledger failures survive rollback failures without publishing state."""
import pytest

from aitrader_ops.ledger import Ledger
from test_v041_order4_review_claude import NOW, proposal


class Interrupted(BaseException):
    pass


class FaultConnection:
    def __init__(self, con, primary, point, fail_rollback):
        self.con, self.primary = con, primary
        self.point, self.fail_rollback = point, fail_rollback
        self.rolled_back = False

    def __getattr__(self, name):
        return getattr(self.con, name)

    def execute(self, sql, *args):
        if ((self.point == 'insert' and sql.startswith('INSERT INTO ledger_events'))
                or (self.point == 'commit' and sql == 'COMMIT')):
            raise self.primary
        result = self.con.execute(sql, *args)
        if sql == 'ROLLBACK':
            self.rolled_back = True
            if self.fail_rollback:
                raise RuntimeError('secondary rollback failure')
        return result


@pytest.mark.parametrize('failure_type', [RuntimeError, Interrupted])
@pytest.mark.parametrize('point', ['insert', 'commit'])
@pytest.mark.parametrize('fail_rollback', [False, True])
def test_ledger_preserves_primary_and_uncommitted_state(
        tmp_path, failure_type, point, fail_rollback):
    ledger = Ledger(tmp_path/'ledger.sqlite')
    try:
        ledger.init_snapshot(1_000_000, [], [], NOW.replace(hour=6))
        con = ledger._con
        before_events = con.execute('SELECT * FROM ledger_events').fetchall()
        before_audit = con.execute('SELECT * FROM ingest_attempts').fetchall()
        before_seq = ledger.seq()
        primary = failure_type('primary ledger failure')
        wrapper = FaultConnection(con, primary, point, fail_rollback)
        ledger._con = wrapper
        with pytest.raises(failure_type) as found:
            ledger.create_notice(proposal(), at=NOW)
        assert found.value is primary
        assert wrapper.rolled_back
        assert not con.in_transaction
        assert con.execute('SELECT * FROM ledger_events').fetchall() == before_events
        assert con.execute('SELECT * FROM ingest_attempts').fetchall() == before_audit
        assert ledger.seq() == before_seq
        # The same connection can accept a normal write once the fault is removed.
        ledger._con = con
        ledger.create_notice(proposal(), at=NOW)
        assert ledger.seq() == before_seq + 1
    finally:
        ledger.close()
