"""The lineage experiment never becomes input for normal trading research."""
from datetime import date, datetime, timezone

import pytest

from aitrader import api, jquants, packet_cli
from aitrader.db import connect
from aitrader.provenance_fixture import ingest_legacy_fixture_with_provenance
from test_jquants_ingestion_atomicity import Client


START, END = date(2026, 8, 1), date(2026, 8, 31)


def ingest(home, version=1):
    client = Client(version)
    fixture = {key: client.rows('', key) for key in (
        'daily_quotes', 'weekly_margin_interest', 'info', 'topix', 'trading_calendar')}
    return ingest_legacy_fixture_with_provenance(
        home, start=START, end=END, recorded_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
        fixture=fixture)


def snapshot(home):
    with connect(home) as con:
        names = con.execute("SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema='main' AND table_type='BASE TABLE'").fetchall()
        return {name: con.execute(
                    ('SELECT * EXCLUDE(recorded_at), CAST(recorded_at AS VARCHAR) '
                     'FROM market_ingest_runs ORDER BY ALL') if name == 'market_ingest_runs'
                    else 'SELECT * FROM "'+name+'" ORDER BY ALL').fetchall()
                for name, in names}


@pytest.mark.parametrize('operation', ['signals', 'backtest', 'synthetic', 'fetch', 'packet'])
def test_fixture_mode_is_rejected_by_existing_paths(tmp_path, operation):
    home = tmp_path/'fixture-home'
    ingest(home)
    before = snapshot(home)
    with connect(home) as con:
        assert con.execute("SELECT value FROM provenance WHERE key='data_mode'").fetchone() == ('provenance_fixture',)
    output = tmp_path/'never-created'
    class NoNetworkClient:
        def rows(self, *args, **kwargs):
            pytest.fail('An existing market path reached the client')
    with pytest.raises(ValueError):
        if operation == 'signals':
            api.run_signals(home, 'margin_bucket_long', END)
        elif operation == 'backtest':
            api.run_backtest(home, 'margin_bucket_long', START, END, output)
        elif operation == 'synthetic':
            api.load_synthetic(home, seed=42)
        elif operation == 'packet':
            packet_cli.generate(home, 'margin_bucket_long', END)
        else:
            jquants.fetch(home, START, END, client=NoNetworkClient())
    assert snapshot(home) == before
    assert not output.exists()
