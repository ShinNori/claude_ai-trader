"""Market homes cannot switch between J-Quants and synthetic provenance."""
from datetime import date

import pytest

import aitrader.api as api
from aitrader.db import connect
from aitrader.jquants import fetch
from test_jquants import FixtureClient


TABLES = ('listed', 'prices_daily', 'margin_weekly', 'index_daily',
          'calendar', 'provenance')


def snapshot(home):
    with connect(home) as database:
        return {table: database.execute(
            f'SELECT * FROM {table} ORDER BY ALL').fetchall() for table in TABLES}


def fake_shared_build(monkeypatch, callback):
    class Loader:
        def exec_module(self, module):
            return None

    class Spec:
        loader = Loader()

    class Module:
        @staticmethod
        def build(seed):
            return callback(seed)

    monkeypatch.setattr(api.importlib.util, 'spec_from_file_location',
                        lambda *a, **k: Spec())
    monkeypatch.setattr(api.importlib.util, 'module_from_spec',
                        lambda spec: Module())


def test_jquants_home_rejects_synthetic_before_build_and_preserves_all_tables(
        tmp_path, monkeypatch):
    home = tmp_path/'jquants-market'
    fetch(home, date(2026, 8, 1), date(2026, 8, 31), FixtureClient())
    before = snapshot(home)
    called = False

    def forbidden(seed):
        nonlocal called
        called = True
        pytest.fail('synthetic build ran for a J-Quants home')

    fake_shared_build(monkeypatch, forbidden)
    with pytest.raises(ValueError, match='混在'):
        api.load_synthetic(home, seed=42)
    assert called is False
    assert snapshot(home) == before


def test_fresh_home_allows_small_synthetic_build(tmp_path, monkeypatch):
    home = tmp_path/'fresh-synthetic'
    calls = []
    fake_shared_build(monkeypatch,
                      lambda seed: calls.append(seed) or {})
    api.load_synthetic(home, seed=7)
    assert calls == [7]
    with connect(home) as database:
        provenance = dict(database.execute(
            'SELECT key,value FROM provenance').fetchall())
    assert provenance['data_mode'] == 'synthetic'
    assert provenance['seed'] == '7'


def test_existing_synthetic_home_allows_repeat_small_build(tmp_path, monkeypatch):
    home = tmp_path/'existing-synthetic'
    calls = []
    fake_shared_build(monkeypatch,
                      lambda seed: calls.append(seed) or {})
    api.load_synthetic(home, seed=7)
    first = snapshot(home)
    api.load_synthetic(home, seed=7)
    assert calls == [7, 7]
    assert snapshot(home) == first


@pytest.mark.parametrize('mode', ['unknown-source', None])
def test_unknown_or_null_mode_is_rejected_before_synthetic_build(
        tmp_path, monkeypatch, mode):
    home = tmp_path/'unknown-mode'
    fetch(home, date(2026, 8, 1), date(2026, 8, 31), FixtureClient())
    with connect(home) as database:
        database.execute("UPDATE provenance SET value=? WHERE key='data_mode'", [mode])
    before = snapshot(home)
    fake_shared_build(monkeypatch,
                      lambda seed: pytest.fail('build ran for unknown mode'))
    with pytest.raises(ValueError):
        api.load_synthetic(home, seed=42)
    assert snapshot(home) == before


@pytest.mark.parametrize('kept_table',
                         ['listed', 'prices_daily', 'margin_weekly',
                          'index_daily', 'calendar'])
def test_missing_mode_with_any_existing_market_table_is_rejected(
        tmp_path, monkeypatch, kept_table):
    home = tmp_path/f'missing-mode-{kept_table}'
    fetch(home, date(2026, 8, 1), date(2026, 8, 31), FixtureClient())
    with connect(home) as database:
        database.execute("DELETE FROM provenance WHERE key='data_mode'")
        for table in TABLES[:-1]:
            if table != kept_table:
                database.execute(f'DELETE FROM {table}')
    before = snapshot(home)
    fake_shared_build(monkeypatch,
                      lambda seed: pytest.fail('build ran for unlabelled data'))
    with pytest.raises(ValueError, match='出所不明'):
        api.load_synthetic(home, seed=42)
    assert snapshot(home) == before


def test_mode_change_during_shared_build_is_rechecked_before_commit(
        tmp_path, monkeypatch):
    home = tmp_path/'synthetic-race'
    fake_shared_build(monkeypatch, lambda seed: {})
    api.load_synthetic(home, seed=7)
    before = snapshot(home)
    changed = False

    def change_mode(seed):
        nonlocal changed
        with connect(home) as database:
            database.execute("UPDATE provenance SET value='jquants' "
                             "WHERE key='data_mode'")
        changed = True
        return {}

    fake_shared_build(monkeypatch, change_mode)
    with pytest.raises(ValueError, match='混在'):
        api.load_synthetic(home, seed=8)
    assert changed
    after = snapshot(home)
    assert dict(after['provenance'])['data_mode'] == 'jquants'
    assert dict(after['provenance'])['seed'] == '7'
    assert {key: value for key, value in after.items() if key != 'provenance'} == {
        key: value for key, value in before.items() if key != 'provenance'}


def test_fresh_and_existing_jquants_mode_are_compatible(tmp_path):
    home = tmp_path/'jquants-repeat'
    fetch(home, date(2026, 8, 1), date(2026, 8, 31), FixtureClient())
    first = snapshot(home)
    fetch(home, date(2026, 8, 1), date(2026, 8, 31), FixtureClient())
    assert snapshot(home) == first


def test_mode_change_during_client_fetch_is_rechecked_before_commit(tmp_path):
    home = tmp_path/'jquants-race'
    base = FixtureClient()
    fetch(home, date(2026, 8, 1), date(2026, 8, 31), base)
    before = snapshot(home)
    changed = False

    class ChangingClient(FixtureClient):
        def rows(self, endpoint, key, **params):
            nonlocal changed
            if key == 'topix' and not changed:
                with connect(home) as database:
                    database.execute("UPDATE provenance SET value='synthetic' "
                                     "WHERE key='data_mode'")
                changed = True
            return super().rows(endpoint, key, **params)

    with pytest.raises(ValueError, match='混在'):
        fetch(home, date(2026, 8, 1), date(2026, 8, 31), ChangingClient())
    assert changed
    after = snapshot(home)
    assert dict(after['provenance'])['data_mode'] == 'synthetic'
    assert {key: value for key, value in after.items() if key != 'provenance'} == {
        key: value for key, value in before.items() if key != 'provenance'}
