"""Opening and closing failures still release other runner connections."""
import pytest

import aitrader.runner as runner
from test_runner import setup


class CloseFault(RuntimeError):
    pass


class ConnectionSpy:
    def __init__(self, connection, *, fail_close=False, fail_rollback=False):
        self.connection = connection
        self.closed = False
        self.fail_close = fail_close
        self.fail_rollback = fail_rollback

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, *args, **kwargs):
        if sql == 'ROLLBACK' and self.fail_rollback:
            raise CloseFault('rollback')
        return self.connection.execute(sql, *args, **kwargs)

    def close(self):
        self.connection.close()
        self.closed = True
        if self.fail_close:
            raise CloseFault('close')


def test_second_connection_failure_closes_first(setup, monkeypatch):
    home, execute = setup
    real = runner.sqlite3.connect
    opened = []

    def connect(path, *args, **kwargs):
        if str(path).endswith('orchestration.sqlite'):
            raise CloseFault('journal open')
        con = ConnectionSpy(real(path, *args, **kwargs))
        opened.append(con)
        return con

    monkeypatch.setattr(runner.sqlite3, 'connect', connect)
    with pytest.raises(CloseFault, match='journal open'):
        execute()
    assert len(opened) == 1 and opened[0].closed
    assert not (home / 'orchestration.sqlite').exists()


@pytest.mark.parametrize('fault', ['ledger', 'journal', 'rollback'])
def test_cleanup_failure_still_closes_remaining_connections(setup, monkeypatch, fault):
    _, execute = setup
    real = runner.sqlite3.connect
    observed = {}

    def connect(path, *args, **kwargs):
        con = real(path, *args, **kwargs)
        for name in ('runner-lock.sqlite', 'orchestration.sqlite'):
            if str(path).endswith(name):
                con = ConnectionSpy(
                    con, fail_close=fault == 'journal' and name == 'orchestration.sqlite',
                    fail_rollback=fault == 'rollback' and name == 'runner-lock.sqlite')
                observed[name] = con
        return con

    if fault == 'ledger':
        real_close = runner.Ledger.close

        def close(ledger):
            real_close(ledger)
            raise CloseFault('ledger close')

        monkeypatch.setattr(runner.Ledger, 'close', close)
    monkeypatch.setattr(runner.sqlite3, 'connect', connect)
    with pytest.raises(CloseFault):
        execute()
    assert set(observed) == {'runner-lock.sqlite', 'orchestration.sqlite'}
    assert all(con.closed for con in observed.values())
