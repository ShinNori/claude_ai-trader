"""Canonical batch identity is independent of row order and scalar spelling."""
from copy import deepcopy
from datetime import date, datetime, timezone
import random

from aitrader.db import connect
from aitrader.provenance_fixture import ingest_legacy_fixture_with_provenance
from test_jquants_ingestion_atomicity import Client


KEYS = ('daily_quotes', 'weekly_margin_interest', 'info', 'topix', 'trading_calendar')


def fixture():
    client = Client()
    result = {key: client.rows('', key) for key in KEYS}
    for key in KEYS:
        extra = deepcopy(result[key][0])
        if 'Code' in extra:
            extra['Code'] = '1301'
        else:
            extra['Date'] = '2026-08-30'
        result[key].append(extra)
    return result


def ingest(home, value, *, second=0):
    return ingest_legacy_fixture_with_provenance(
        home, start=date(2026, 8, 1), end=date(2026, 8, 31),
        recorded_at=datetime(2026, 9, 4, 0, 0, second, tzinfo=timezone.utc), fixture=value)


def test_row_and_endpoint_order_do_not_create_new_runs(tmp_path):
    home = tmp_path/'ordered'
    original = fixture()
    retained = deepcopy(original)
    first = ingest(home, original)
    assert first['result'] == 'COMPLETED'
    assert original == retained
    for seed in range(5):
        rng = random.Random(seed)
        keys = list(KEYS)
        rng.shuffle(keys)
        shuffled = {key: deepcopy(original[key]) for key in keys}
        for rows in shuffled.values():
            rng.shuffle(rows)
        result = ingest(home, shuffled, second=seed+1)
        assert result['result'] == 'NO_OP'
        assert result['run_id'] == first['run_id']
    with connect(home) as con:
        assert con.execute('SELECT count(*) FROM market_ingest_runs').fetchone()[0] == 1
        assert con.execute('SELECT count(*) FROM market_ingest_run_rows').fetchone()[0] == 10
        stamp = con.execute("SELECT CAST(recorded_at AS VARCHAR) FROM market_ingest_runs").fetchone()[0]
        assert datetime.fromisoformat(stamp).astimezone(timezone.utc) == datetime(2026, 9, 4, tzinfo=timezone.utc)


def test_equivalent_date_and_numeric_spellings_share_batch_identity(tmp_path):
    home = tmp_path/'scalars'
    original = fixture()
    first = ingest(home, original)
    alternate = deepcopy(original)
    for rows in alternate.values():
        for row in rows:
            for key, value in list(row.items()):
                if key in ('Date', 'ListedDate', 'PublishedDate'):
                    row[key] = date.fromisoformat(value)
                elif key == 'Code':
                    row[key] = int(value)
                elif key in ('Open', 'High', 'Low', 'Close', 'Volume', 'TurnoverValue',
                             'AdjustmentFactor', 'LongMarginTradeVolume', 'ShortMarginTradeVolume'):
                    row[key] = str(float(value))
    result = ingest(home, alternate)
    assert result['result'] == 'NO_OP'
    assert result['run_id'] == first['run_id']
