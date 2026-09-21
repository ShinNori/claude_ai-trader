"""Focused provenance boundaries for the read-only market inspection."""
from datetime import date

import pytest

from aitrader.db import connect, init
from aitrader.market_inspection import inspect_market_inputs


AS_OF = date(2026, 9, 4)


def _synthetic_market(tmp_path, seed):
    home = tmp_path / 'market'
    init(home)
    with connect(home) as database:
        database.execute("INSERT INTO provenance VALUES ('data_mode','synthetic')")
        database.execute("INSERT INTO provenance VALUES ('seed',?)", [seed])
    return home


@pytest.mark.parametrize('seed', ['0', '42', '-42'])
def test_load_synthetic_integer_seed_text_remains_accepted(tmp_path, seed):
    report = inspect_market_inputs(_synthetic_market(tmp_path, seed), as_of=AS_OF)

    assert report['status'] == 'INSPECTED'
    assert report['source'] == 'synthetic'
    assert 'PROVENANCE_INCOMPLETE' not in report['warnings']


@pytest.mark.parametrize('seed', ['--42', '+42', '-', '', '٤٢'])
def test_noncanonical_or_non_ascii_seed_is_incomplete(tmp_path, seed):
    report = inspect_market_inputs(_synthetic_market(tmp_path, seed), as_of=AS_OF)

    assert report['status'] == 'INSPECTED'
    assert report['source'] == 'synthetic'
    assert 'PROVENANCE_INCOMPLETE' in report['warnings']
