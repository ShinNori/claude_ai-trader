"""Signal entry points preserve the primary transaction failure."""
from datetime import date, timedelta

import pytest

from aitrader import api, packet_cli


AS_OF = date(2026, 8, 28)


class PrimaryFailure(BaseException):
    pass


class OrdinaryPrimaryFailure(RuntimeError):
    pass


class RollbackFailure(RuntimeError):
    pass


class Result:
    def __init__(self, *, one=None, rows=None):
        self.one = one
        self.rows = rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, *, commit_failure=None, rollback_failure=None):
        self.commit_failure = commit_failure
        self.rollback_failure = rollback_failure
        self.commands = []
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.exited = True

    def execute(self, sql, _params=None):
        self.commands.append(sql)
        if sql == "COMMIT" and self.commit_failure is not None:
            raise self.commit_failure
        if sql == "ROLLBACK" and self.rollback_failure is not None:
            raise self.rollback_failure
        if sql.startswith("SELECT min(date)"):
            return Result(one=(AS_OF + timedelta(days=3),))
        if sql.startswith("SELECT max(date)"):
            return Result(one=(AS_OF,))
        return Result(rows=[])


def _patch_entry(monkeypatch, entry, connection, generation):
    if entry == "api":
        monkeypatch.setattr(api.db, "connect", lambda _home: connection)
        monkeypatch.setattr(api, "_generate_signals", generation)
    else:
        monkeypatch.setattr(packet_cli, "connect", lambda _home: connection)
        monkeypatch.setattr(packet_cli, "_run_signals_in_connection", generation)


def _run(entry, tmp_path):
    if entry == "api":
        return api.run_signals(tmp_path / "home", "margin_bucket_long", AS_OF)
    return packet_cli.generate(tmp_path / "home", "margin_bucket_long", AS_OF)


@pytest.mark.parametrize("entry", ["api", "packet"])
@pytest.mark.parametrize("failure_point", ["generation", "commit"])
@pytest.mark.parametrize("primary_type", [OrdinaryPrimaryFailure, PrimaryFailure])
def test_rollback_failure_does_not_mask_primary_failure(
        tmp_path, monkeypatch, entry, failure_point, primary_type):
    primary = primary_type(f"primary-{failure_point}")
    secondary = RollbackFailure("secondary-rollback")
    connection = Connection(
        commit_failure=primary if failure_point == "commit" else None,
        rollback_failure=secondary,
    )

    def generation(_connection, _strategy, _as_of):
        if failure_point == "generation":
            raise primary
        return []

    _patch_entry(monkeypatch, entry, connection, generation)

    with pytest.raises(primary_type) as raised:
        _run(entry, tmp_path)

    assert raised.value is primary
    assert connection.commands[0] == "BEGIN"
    assert connection.commands[-1] == "ROLLBACK"
    assert connection.exited is True


@pytest.mark.parametrize("entry", ["api", "packet"])
def test_successful_transaction_still_commits_without_rollback(
        tmp_path, monkeypatch, entry):
    connection = Connection()
    _patch_entry(monkeypatch, entry, connection,
                 lambda _connection, _strategy, _as_of: [])

    assert _run(entry, tmp_path) == []

    assert connection.commands[0] == "BEGIN"
    assert "COMMIT" in connection.commands
    assert "ROLLBACK" not in connection.commands
    assert connection.exited is True
