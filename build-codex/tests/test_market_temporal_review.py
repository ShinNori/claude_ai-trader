"""Phase-1 source and finite-result boundaries found by temporal review."""
from datetime import date
import shutil

import pytest

from aitrader.api import load_synthetic, run_backtest, run_signals
from aitrader.db import connect


@pytest.fixture(scope='module')
def synthetic_database(tmp_path_factory):
    home = tmp_path_factory.mktemp('temporal-source')
    load_synthetic(home, seed=42)
    return home/'market.duckdb'


@pytest.fixture
def market_home(tmp_path, synthetic_database):
    home = tmp_path/'home'
    home.mkdir()
    shutil.copyfile(synthetic_database, home/'market.duckdb')
    return home


def test_signals_reject_unknown_saved_data_mode(market_home):
    with connect(market_home) as database:
        database.execute("UPDATE provenance SET value='unknown-source' WHERE key='data_mode'")
    with pytest.raises(ValueError):
        run_signals(market_home, 'margin_bucket_long', date(2026, 8, 28))


def test_nonfinite_benchmark_is_rejected_before_result_artifacts(market_home, tmp_path):
    with connect(market_home) as database:
        database.execute(
            "UPDATE index_daily SET close='Infinity' WHERE name='TOPIX' AND date=?",
            [date(2026, 8, 31)])
    output = tmp_path/'results'
    with pytest.raises(ValueError):
        run_backtest(market_home, 'margin_bucket_long', date(2026, 8, 31),
                     date(2026, 8, 31), output)
    assert not (output/'margin_bucket_long_v1').exists()
