"""Public replay validation shares snapshot rules without opening a database."""
import copy
import sqlite3
from contextlib import closing

import pytest

from test_runner import NOW
from aitrader_ops.ledger import Ledger


def validate(payload):
    from aitrader_ops.ledger import validate_snapshot_for_replay
    return validate_snapshot_for_replay(payload)


def payload():
    return {'cash': 1000000, 'positions': [], 'open_orders': [], 'at': NOW.isoformat()}


def no_database(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure snapshot validation opened a database')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


def test_minimal_snapshot_validates_without_database(monkeypatch):
    value = payload()
    original = copy.deepcopy(value)
    no_database(monkeypatch)
    assert validate(value) is None
    assert value == original


def test_nested_snapshot_is_not_mutated(monkeypatch):
    value = payload()
    value['positions'] = [{'code': '6857', 'qty': 100, 'avg_price': 1000., 'stop_order': True}]
    value['open_orders'] = [{'code': '7203', 'side': 'BUY', 'qty': 100, 'limit_price': 1000.,
                             'broker_order_id': 'synthetic-order'}]
    value['policy'] = {'schema_version': 1, 'custom': {'nested': ['retain']}}
    original = copy.deepcopy(value)
    no_database(monkeypatch)
    assert validate(value) is None
    assert value == original


@pytest.mark.parametrize('change', [
    {'cash': -1}, {'cash': True}, {'cash': 'bad'},
    {'positions': [{'code': '6857', 'qty': 0, 'avg_price': 1000}]},
    {'positions': [{'code': '6857', 'qty': True, 'avg_price': 1000}]},
    {'positions': [{'code': '6857', 'qty': 100, 'avg_price': 'bad'}]},
])
def test_invalid_snapshot_matches_ledger_rejection(tmp_path, monkeypatch, change):
    value = payload()
    value.update(change)
    original = copy.deepcopy(value)
    with closing(Ledger(tmp_path/'reference.sqlite')) as ledger:
        with pytest.raises(Exception) as reference:
            ledger.init_snapshot(value['cash'], value['positions'], value['open_orders'], NOW)
    no_database(monkeypatch)
    with pytest.raises(type(reference.value)):
        validate(value)
    assert value == original
