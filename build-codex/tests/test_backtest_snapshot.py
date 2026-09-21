"""Backtest prices, benchmark, provenance and strategy share one snapshot."""
from datetime import date
from pathlib import Path

import pytest

import aitrader.backtest as backtest
from aitrader.db import init, connect


DAYS = [date(2026, 9, 4), date(2026, 9, 7),
        date(2026, 9, 11), date(2026, 9, 14)]


@pytest.fixture
def market(tmp_path):
    home = tmp_path/'market'
    init(home)
    with connect(home) as database:
        for index, day in enumerate(DAYS):
            close = 1000. + index
            database.execute('INSERT INTO prices_daily '
                             '(code,date,open,close) VALUES (?,?,?,?)',
                             ['6857', day, close, close])
            database.execute('INSERT INTO index_daily VALUES (?,?,?)',
                             ['TOPIX', day, 2000.+index])
        database.executemany('INSERT INTO provenance VALUES (?,?)',
                             [('data_mode', 'synthetic'), ('seed', 'old')])
    return home


class SnapshotStrategy:
    version = 'v1'

    def __init__(self, home, *, correct=False, fail=False):
        self.home = home
        self.correct = correct
        self.fail = fail
        self.observed = []

    def generate(self, as_of, database):
        seed = database.execute(
            "SELECT value FROM provenance WHERE key='seed'").fetchone()[0]
        close = database.execute(
            "SELECT close FROM prices_daily WHERE code='6857' AND date=?",
            [DAYS[0]]).fetchone()[0]
        self.observed.append((as_of, seed, close))
        if self.correct:
            self.correct = False
            with connect(self.home) as writer:
                writer.execute("UPDATE provenance SET value='new' WHERE key='seed'")
                writer.execute("UPDATE prices_daily SET close=2000,open=2000 "
                               "WHERE code='6857' AND date=?", [DAYS[0]])
                writer.execute("UPDATE index_daily SET close=3000 WHERE date=?", [DAYS[0]])
        if self.fail:
            raise RuntimeError('strategy snapshot failure')
        return []


def test_concurrent_correction_is_old_for_whole_run_and_new_for_next_run(
        market, tmp_path, monkeypatch):
    first_strategy = SnapshotStrategy(market, correct=True)
    monkeypatch.setattr(backtest, 'get_strategy', lambda name: first_strategy)
    first = backtest.run(market, 'fixture', DAYS[0], DAYS[-1], tmp_path/'first')
    assert first['data_mode'] == 'synthetic'
    assert len(first_strategy.observed) == 2
    assert {(seed, close) for _, seed, close in first_strategy.observed} == {('old', 1000.)}

    second_strategy = SnapshotStrategy(market)
    monkeypatch.setattr(backtest, 'get_strategy', lambda name: second_strategy)
    second = backtest.run(market, 'fixture', DAYS[0], DAYS[-1], tmp_path/'second')
    assert len(second_strategy.observed) == 2
    assert {(seed, close) for _, seed, close in second_strategy.observed} == {('new', 2000.)}
    assert first['benchmark_cagr'] != second['benchmark_cagr']


class ObservedConnection:
    def __init__(self, connection):
        self.connection = connection
        self.events = []
    def __enter__(self):
        self.connection.__enter__()
        return self
    def __exit__(self, *args):
        return self.connection.__exit__(*args)
    def __getattr__(self, name):
        return getattr(self.connection, name)
    def execute(self, statement, *args, **kwargs):
        self.events.append(statement)
        return self.connection.execute(statement, *args, **kwargs)


def test_success_commits_read_transaction(market, tmp_path, monkeypatch):
    real_connect = backtest.connect
    wrappers = []
    monkeypatch.setattr(backtest, 'connect', lambda home:
                        wrappers.append(ObservedConnection(real_connect(home))) or wrappers[-1])
    monkeypatch.setattr(backtest, 'get_strategy',
                        lambda name: SnapshotStrategy(market))
    summary = backtest.run(market, 'fixture', DAYS[0], DAYS[-1], tmp_path/'success')
    assert summary['trades'] == 0
    assert len(wrappers) == 1
    assert wrappers[0].events[0] == 'BEGIN'
    assert wrappers[0].events[-1] == 'COMMIT'
    assert 'ROLLBACK' not in wrappers[0].events


def test_failure_rolls_back_without_artifacts_and_keeps_external_correction(
        market, tmp_path, monkeypatch):
    real_connect = backtest.connect
    wrappers = []
    monkeypatch.setattr(backtest, 'connect', lambda home:
                        wrappers.append(ObservedConnection(real_connect(home))) or wrappers[-1])
    strategy = SnapshotStrategy(market, correct=True, fail=True)
    monkeypatch.setattr(backtest, 'get_strategy', lambda name: strategy)
    output = tmp_path/'failed'
    with pytest.raises(RuntimeError, match='strategy snapshot failure'):
        backtest.run(market, 'fixture', DAYS[0], DAYS[-1], output)
    assert len(wrappers) == 1
    assert wrappers[0].events[0] == 'BEGIN'
    assert wrappers[0].events[-1] == 'ROLLBACK'
    assert 'COMMIT' not in wrappers[0].events
    assert not output.exists()
    with connect(market) as database:
        assert database.execute(
            "SELECT value FROM provenance WHERE key='seed'").fetchone()[0] == 'new'
        assert database.execute(
            "SELECT close FROM prices_daily WHERE code='6857' AND date=?",
            [DAYS[0]]).fetchone()[0] == 2000.
