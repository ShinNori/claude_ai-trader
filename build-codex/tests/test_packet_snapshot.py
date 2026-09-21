"""Packet candidates and market inputs share one DuckDB transaction."""
from dataclasses import asdict
from datetime import date, timedelta

import pytest

import aitrader.api as api
import aitrader.packet_cli as packet_cli
from aitrader.db import init, connect
from aitrader.strategies.base import Candidate


AS_OF = date(2026, 9, 4)
NEXT = date(2026, 9, 7)
CANDIDATE = {'code': '6857', 'side': 'BUY', 'strategy': 'margin_bucket_long',
             'strategy_version': 'v1', 'reason': 'snapshot fixture'}


@pytest.fixture
def market(tmp_path):
    home = tmp_path/'market'
    init(home)
    with connect(home) as database:
        database.execute('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)',
                         ['6857', AS_OF, 1000.])
        database.execute('INSERT INTO calendar VALUES(?,true)', [NEXT])
        database.executemany('INSERT INTO provenance VALUES (?,?)',
                             [('data_mode', 'synthetic'), ('seed', 'old')])
    return home


def test_public_run_signals_signature_and_result_remain_compatible(market, monkeypatch):
    expected = Candidate('6857', 'BUY', 1., 1., 'fixture',
                         'margin_bucket_long', 'v1', AS_OF, 5, .005)

    class Strategy:
        def generate(self, as_of, database):
            assert as_of == AS_OF
            assert database.execute("SELECT value FROM provenance WHERE key='data_mode'").fetchone()[0] == 'synthetic'
            return [expected]

    monkeypatch.setattr(api, 'get_strategy', lambda name: Strategy())
    assert api.run_signals(market, 'margin_bucket_long', AS_OF) == [asdict(expected)]


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
    def execute(self, sql, *args, **kwargs):
        self.events.append(sql)
        return self.connection.execute(sql, *args, **kwargs)


def test_generate_uses_one_connection_and_transaction_for_all_saved_inputs(
        market, monkeypatch):
    real_connect = packet_cli.connect
    wrappers = []

    def observed_connect(home):
        wrapper = ObservedConnection(real_connect(home))
        wrappers.append(wrapper)
        return wrapper

    helper_connections = []
    def signals(connection, strategy, as_of):
        helper_connections.append(connection)
        connection.events.append('SIGNALS')
        return [CANDIDATE]

    monkeypatch.setattr(packet_cli, 'connect', observed_connect)
    monkeypatch.setattr(packet_cli, '_run_signals_in_connection', signals)
    proposals = packet_cli.generate(market, 'margin_bucket_long', AS_OF)
    assert len(proposals) == 1
    assert len(wrappers) == 1
    assert helper_connections == wrappers
    events = wrappers[0].events
    assert events[0] == 'BEGIN'
    assert events.index('SIGNALS') < next(
        i for i, sql in enumerate(events) if 'FROM calendar' in sql)
    assert events[-1] == 'COMMIT'
    assert 'ROLLBACK' not in events


def test_generate_exception_rolls_back_same_connection(market, monkeypatch):
    real_connect = packet_cli.connect
    wrappers = []
    def observed_connect(home):
        wrapper = ObservedConnection(real_connect(home))
        wrappers.append(wrapper)
        return wrapper
    monkeypatch.setattr(packet_cli, 'connect', observed_connect)
    monkeypatch.setattr(packet_cli, '_run_signals_in_connection',
                        lambda *a: (_ for _ in ()).throw(RuntimeError('strategy failed')))
    with pytest.raises(RuntimeError, match='strategy failed'):
        packet_cli.generate(market, 'margin_bucket_long', AS_OF)
    assert len(wrappers) == 1
    assert wrappers[0].events == ['BEGIN', 'ROLLBACK']


def test_concurrent_correction_is_not_mixed_and_appears_on_next_generation(
        market, monkeypatch):
    corrected_day = NEXT + timedelta(days=1)
    changed = False

    def signals_then_correct(connection, strategy, as_of):
        nonlocal changed
        # Establish the transaction snapshot before the independent writer.
        assert connection.execute(
            "SELECT value FROM provenance WHERE key='seed'").fetchone()[0] == 'old'
        with connect(market) as writer:
            writer.execute('UPDATE prices_daily SET close=2000 WHERE code=?', ['6857'])
            writer.execute('DELETE FROM calendar WHERE date=?', [NEXT])
            writer.execute('INSERT INTO calendar VALUES(?,true)', [corrected_day])
            writer.execute("UPDATE provenance SET value='new' WHERE key='seed'")
        changed = True
        return [CANDIDATE]

    monkeypatch.setattr(packet_cli, '_run_signals_in_connection', signals_then_correct)
    first = packet_cli.generate(market, 'margin_bucket_long', AS_OF)[0]
    assert changed
    # The first result is wholly from the old snapshot, despite the committed correction.
    assert first['limit_price'] == 1005.0
    assert first['expires_at'].date() == NEXT

    monkeypatch.setattr(packet_cli, '_run_signals_in_connection',
                        lambda *a: [CANDIDATE])
    second = packet_cli.generate(market, 'margin_bucket_long', AS_OF)[0]
    assert second['limit_price'] == 2010.0
    assert second['expires_at'].date() == corrected_day
    assert second['snapshot_id'] != first['snapshot_id']
