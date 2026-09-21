"""Offline provenance prototype for fixed legacy-v1 fixtures only.

This deliberately uses a distinct data_mode and is not connected to fetch,
signals, backtests, daily runs, or any live-data readiness decision.
"""
from __future__ import annotations

import hashlib
import json
import math
import stat
from datetime import date, datetime, timedelta
from pathlib import Path

from .db import SCHEMA, _runtime_path_guard, connect
from .market_inspection import _SchemaError, _verify_schema


KEYS = ('daily_quotes', 'weekly_margin_interest', 'info', 'topix',
        'trading_calendar')
TABLES = ('listed', 'prices_daily', 'margin_weekly', 'index_daily', 'calendar')
CONTRACT = 'legacy-v1-unverified-fixture'
PROVENANCE_SCHEMA = '1'

PROVENANCE_SCHEMA_SQL = """
CREATE TABLE market_ingest_runs(
 run_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version=1),
 contract TEXT NOT NULL CHECK(contract='legacy-v1-unverified-fixture'),
 request_start DATE NOT NULL, request_end DATE NOT NULL,
 normalized_input_sha256 TEXT NOT NULL, recorded_at TIMESTAMPTZ NOT NULL,
 status TEXT NOT NULL CHECK(status='COMPLETED'));
CREATE TABLE market_ingest_run_rows(
 run_id TEXT NOT NULL, table_name TEXT NOT NULL, row_key_json TEXT NOT NULL,
 normalized_row_sha256 TEXT NOT NULL, normalized_row_json TEXT NOT NULL,
 PRIMARY KEY(run_id,table_name,row_key_json));
CREATE TABLE market_row_provenance(
 table_name TEXT NOT NULL, row_key_json TEXT NOT NULL, run_id TEXT NOT NULL,
 ohlc_basis TEXT, volume_basis TEXT, turnover_basis TEXT,
 factor_basis TEXT, publication_source TEXT,
 PRIMARY KEY(table_name,row_key_json));
"""


def _fail():
    raise ValueError('固定fixtureの市場来歴を確認できません')


def _number(value, *, nullable=True):
    if value is None and nullable:
        return None
    if isinstance(value, bool):
        _fail()
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        _fail()
    if not math.isfinite(result):
        _fail()
    return result


def _text(value):
    if not isinstance(value, str):
        _fail()
    return value


def _scalar_text(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        _fail()
    return str(value)


def _day(value, *, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, datetime):
        _fail()
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            _fail()
        if parsed.isoformat() != value:
            _fail()
        return parsed
    _fail()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _row_key(table, values):
    if table == 'listed':
        key = [values['code']]
    elif table in ('prices_daily', 'margin_weekly'):
        key = [values['code'], values['date']]
    elif table == 'index_daily':
        key = [values['name'], values['date']]
    else:
        key = [values['date']]
    return _canonical([item.isoformat() if type(item) is date else item for item in key])


def _normalize_fixture(fixture, start, end):
    if not isinstance(fixture, dict) or set(fixture) != set(KEYS):
        _fail()
    if any(not isinstance(fixture[key], list) or not fixture[key] for key in KEYS):
        _fail()
    if any(not isinstance(row, dict) for key in KEYS for row in fixture[key]):
        _fail()

    normalized = {table: [] for table in TABLES}
    details = {}
    for row in fixture['info']:
        market_code = row.get('MarketCode')
        values = {'code': _scalar_text(row['Code']),
                  'name': _text(row['CompanyName']),
                  'market': {'0111': 'prime', '0112': 'standard',
                             '0113': 'growth'}.get(
                                 _scalar_text(market_code) if market_code is not None else '',
                                 'unknown'),
                  'sector33': _scalar_text(row.get('Sector33Code', '')),
                  'listed_date': _day(row.get('ListedDate'), nullable=True)}
        normalized['listed'].append(values)
    for row in fixture['daily_quotes']:
        availability = [row.get('Adjustment' + key) is not None
                        for key in ('Open', 'High', 'Low', 'Close')]
        if any(availability) and not all(availability):
            _fail()
        adjusted = all(availability)
        prefix = 'Adjustment' if adjusted else ''
        values = {'code': _scalar_text(row['Code']), 'date': _day(row['Date']),
                  'open': _number(row[prefix + 'Open'], nullable=False),
                  'high': _number(row[prefix + 'High'], nullable=False),
                  'low': _number(row[prefix + 'Low'], nullable=False),
                  'close': _number(row[prefix + 'Close'], nullable=False),
                  'volume': _number(row.get('AdjustmentVolume', row.get('Volume'))),
                  'turnover': _number(row.get('TurnoverValue')),
                  'adj_factor': _number(row.get('AdjustmentFactor', 1))}
        key = _row_key('prices_daily', values)
        details[('prices_daily', key)] = {
            'ohlc_basis': 'ADJUSTED' if adjusted else 'RAW',
            'volume_basis': ('MISSING' if values['volume'] is None else
                             'ADJUSTED' if 'AdjustmentVolume' in row else 'RAW'),
            'turnover_basis': 'MISSING' if values['turnover'] is None else 'RAW',
            'factor_basis': ('PROVIDED' if 'AdjustmentFactor' in row else 'DEFAULT_ONE'),
            'publication_source': None}
        normalized['prices_daily'].append(values)
    for row in fixture['weekly_margin_interest']:
        base = _day(row['Date'])
        explicit = row.get('PublishedDate') or row.get('PublishDate')
        values = {'code': _scalar_text(row['Code']), 'date': base,
                  'publish_date': _day(explicit) if explicit else base + timedelta(days=4),
                  'long_balance': _number(row.get('LongMarginTradeVolume')),
                  'short_balance': _number(row.get('ShortMarginTradeVolume'))}
        key = _row_key('margin_weekly', values)
        details[('margin_weekly', key)] = {
            'ohlc_basis': None, 'volume_basis': None, 'turnover_basis': None,
            'factor_basis': None,
            'publication_source': 'EXPLICIT' if explicit else 'DATE_PLUS_4'}
        normalized['margin_weekly'].append(values)
    for row in fixture['topix']:
        normalized['index_daily'].append(
            {'name': 'TOPIX', 'date': _day(row['Date']),
             'close': _number(row.get('Close'))})
    for row in fixture['trading_calendar']:
        division = _scalar_text(row['HolidayDivision'])
        if division not in ('0', '1'):
            _fail()
        normalized['calendar'].append(
            {'date': _day(row['Date']),
             'is_business_day': division == '1'})

    for table, rows in normalized.items():
        seen = set()
        for values in rows:
            key = _row_key(table, values)
            if key in seen:
                _fail()
            seen.add(key)
            if (table, key) not in details:
                details[(table, key)] = dict.fromkeys(
                    ('ohlc_basis', 'volume_basis', 'turnover_basis',
                     'factor_basis', 'publication_source'))
        rows.sort(key=lambda item: _row_key(table, item))
    return normalized, details


def _jsonable_row(table, row, detail):
    converted = {key: value.isoformat() if type(value) is date else value
                 for key, value in row.items()}
    return {'table': table, 'values': converted, 'provenance': detail}


def _build_manifest(normalized, details, start, end):
    tables = {}
    row_records = []
    for table in TABLES:
        tables[table] = []
        for row in normalized[table]:
            key = _row_key(table, row)
            payload = _jsonable_row(table, row, details[(table, key)])
            row_json = _canonical(payload)
            digest = hashlib.sha256(row_json.encode('utf-8')).hexdigest()
            row_records.append((table, key, digest, row_json))
            tables[table].append(payload)
    envelope = {'schema_version': 1, 'contract': CONTRACT,
                'request_start': start.isoformat(), 'request_end': end.isoformat(),
                'tables': tables}
    content = _canonical(envelope).encode('utf-8')
    digest = hashlib.sha256(content).hexdigest()
    return digest, 'mprov1:' + digest, row_records


def _verify_provenance_schema(con):
    try:
        _verify_schema(con)
    except _SchemaError:
        _fail()
    expected = {'data_mode': 'provenance_fixture',
                'market_provenance_schema': PROVENANCE_SCHEMA,
                'market_input_contract': CONTRACT}
    rows = con.execute(
        "SELECT key,value FROM provenance WHERE key IN "
        "('data_mode','market_provenance_schema','market_input_contract')"
    ).fetchall()
    if len(rows) != 3 or dict(rows) != expected:
        _fail()
    required = {'market_ingest_runs', 'market_ingest_run_rows',
                'market_row_provenance'}
    found = {row[0] for row in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main' AND table_type='BASE TABLE'"
    ).fetchall()}
    if not required <= found:
        _fail()
    expected_columns = {
        'market_ingest_runs': (
            ('run_id', 'VARCHAR', 'NO'), ('schema_version', 'INTEGER', 'NO'),
            ('contract', 'VARCHAR', 'NO'), ('request_start', 'DATE', 'NO'),
            ('request_end', 'DATE', 'NO'), ('normalized_input_sha256', 'VARCHAR', 'NO'),
            ('recorded_at', 'TIMESTAMP WITH TIME ZONE', 'NO'), ('status', 'VARCHAR', 'NO')),
        'market_ingest_run_rows': (
            ('run_id', 'VARCHAR', 'NO'), ('table_name', 'VARCHAR', 'NO'),
            ('row_key_json', 'VARCHAR', 'NO'), ('normalized_row_sha256', 'VARCHAR', 'NO'),
            ('normalized_row_json', 'VARCHAR', 'NO')),
        'market_row_provenance': (
            ('table_name', 'VARCHAR', 'NO'), ('row_key_json', 'VARCHAR', 'NO'),
            ('run_id', 'VARCHAR', 'NO'), ('ohlc_basis', 'VARCHAR', 'YES'),
            ('volume_basis', 'VARCHAR', 'YES'), ('turnover_basis', 'VARCHAR', 'YES'),
            ('factor_basis', 'VARCHAR', 'YES'), ('publication_source', 'VARCHAR', 'YES')),
    }
    for table, expected_schema in expected_columns.items():
        actual = tuple(con.execute(
            "SELECT column_name,data_type,is_nullable FROM information_schema.columns "
            "WHERE table_schema='main' AND table_name=? ORDER BY ordinal_position",
            [table]).fetchall())
        if actual != expected_schema:
            _fail()
    expected_primary_keys = {
        'market_ingest_runs': ('run_id',),
        'market_ingest_run_rows': ('run_id', 'table_name', 'row_key_json'),
        'market_row_provenance': ('table_name', 'row_key_json'),
    }
    for table, expected_key in expected_primary_keys.items():
        actual_key = tuple(row[0] for row in con.execute(
            "SELECT k.column_name FROM information_schema.table_constraints t "
            "JOIN information_schema.key_column_usage k "
            "ON t.constraint_name=k.constraint_name AND t.table_name=k.table_name "
            "WHERE t.table_schema='main' AND t.table_name=? "
            "AND t.constraint_type='PRIMARY KEY' ORDER BY k.ordinal_position",
            [table]).fetchall())
        if actual_key != expected_key:
            _fail()
    return True


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail()
        value[key] = item
    return value


def _valid_iso_day(value, *, nullable=False):
    if value is None:
        return nullable
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_json_number(value, *, nullable=True):
    if value is None:
        return nullable
    return type(value) is float and math.isfinite(value)


def _valid_historical_values(table, values):
    if table == 'listed':
        return (all(isinstance(values[name], str)
                    for name in ('code', 'name', 'market', 'sector33'))
                and _valid_iso_day(values['listed_date'], nullable=True))
    if table == 'prices_daily':
        return (isinstance(values['code'], str) and _valid_iso_day(values['date'])
                and all(_valid_json_number(values[name], nullable=False)
                        for name in ('open', 'high', 'low', 'close'))
                and all(_valid_json_number(values[name])
                        for name in ('volume', 'turnover', 'adj_factor')))
    if table == 'margin_weekly':
        return (isinstance(values['code'], str) and _valid_iso_day(values['date'])
                and _valid_iso_day(values['publish_date'])
                and _valid_json_number(values['long_balance'])
                and _valid_json_number(values['short_balance']))
    if table == 'index_daily':
        return (isinstance(values['name'], str) and _valid_iso_day(values['date'])
                and _valid_json_number(values['close']))
    return (_valid_iso_day(values['date'])
            and type(values['is_business_day']) is bool)


def _valid_lineage_relation(table, values, provenance):
    if table == 'prices_daily':
        volume_missing = values['volume'] is None
        turnover_missing = values['turnover'] is None
        if (volume_missing != (provenance['volume_basis'] == 'MISSING')
                or turnover_missing != (provenance['turnover_basis'] == 'MISSING')):
            return False
        if (provenance['factor_basis'] == 'DEFAULT_ONE'
                and values['adj_factor'] != 1.0):
            return False
    elif (table == 'margin_weekly'
          and provenance['publication_source'] == 'DATE_PLUS_4'):
        base = date.fromisoformat(values['date'])
        published = date.fromisoformat(values['publish_date'])
        if published != base + timedelta(days=4):
            return False
    return True


def _verify_provenance_integrity(con, *, allow_empty=False):
    """SELECT-only verification shared with the independent inspection API."""
    _verify_provenance_schema(con)
    runs = con.execute(
        'SELECT run_id,schema_version,contract,request_start,request_end,'
        'normalized_input_sha256,CAST(recorded_at AS VARCHAR),status '
        'FROM market_ingest_runs ORDER BY run_id'
    ).fetchall()
    completed = set()
    for run_id, version, contract, start, end, expected_hash, recorded_at, status in runs:
        try:
            parsed_recorded_at = datetime.fromisoformat(recorded_at)
        except (TypeError, ValueError):
            _fail()
        if (not isinstance(run_id, str) or not run_id.startswith('mprov1:')
                or len(run_id) != 71 or type(version) is not int or version != 1
                or contract != CONTRACT or type(start) is not date or type(end) is not date
                or start > end or status != 'COMPLETED'
                or not isinstance(expected_hash, str) or len(expected_hash) != 64
                or any(character not in '0123456789abcdef' for character in expected_hash)
                or parsed_recorded_at.tzinfo is None
                or parsed_recorded_at.utcoffset() is None
                or run_id != 'mprov1:' + expected_hash):
            _fail()
        records = con.execute(
            'SELECT table_name,row_key_json,normalized_row_sha256,normalized_row_json '
            'FROM market_ingest_run_rows WHERE run_id=? ORDER BY table_name,row_key_json',
            [run_id]).fetchall()
        tables = {table: [] for table in TABLES}
        seen = set()
        for table, key, row_hash, row_json in records:
            if (table not in TABLES or not isinstance(key, str)
                    or not isinstance(row_hash, str) or len(row_hash) != 64
                    or any(character not in '0123456789abcdef' for character in row_hash)
                    or not isinstance(row_json, str)
                    or hashlib.sha256(row_json.encode('utf-8')).hexdigest() != row_hash):
                _fail()
            try:
                payload = json.loads(row_json, object_pairs_hook=_strict_object,
                                     parse_constant=lambda _: _fail())
            except (TypeError, ValueError, json.JSONDecodeError):
                _fail()
            if (not isinstance(payload, dict) or payload.get('table') != table
                    or set(payload) != {'table', 'values', 'provenance'}
                    or not isinstance(payload['values'], dict)
                    or not isinstance(payload['provenance'], dict)
                    or _canonical(payload) != row_json):
                _fail()
            value_columns = {
                'listed': {'code', 'name', 'market', 'sector33', 'listed_date'},
                'prices_daily': {'code', 'date', 'open', 'high', 'low', 'close',
                                 'volume', 'turnover', 'adj_factor'},
                'margin_weekly': {'code', 'date', 'publish_date', 'long_balance',
                                  'short_balance'},
                'index_daily': {'name', 'date', 'close'},
                'calendar': {'date', 'is_business_day'},
            }[table]
            if set(payload['values']) != value_columns:
                _fail()
            if not _valid_historical_values(table, payload['values']):
                _fail()
            provenance = payload['provenance']
            if set(provenance) != {'ohlc_basis', 'volume_basis', 'turnover_basis',
                                   'factor_basis', 'publication_source'}:
                _fail()
            if table == 'prices_daily':
                valid = (provenance['ohlc_basis'] in ('ADJUSTED', 'RAW')
                         and provenance['volume_basis'] in ('ADJUSTED', 'RAW', 'MISSING')
                         and provenance['turnover_basis'] in ('RAW', 'MISSING')
                         and provenance['factor_basis'] in ('PROVIDED', 'DEFAULT_ONE')
                         and provenance['publication_source'] is None)
            elif table == 'margin_weekly':
                valid = (all(provenance[name] is None for name in
                             ('ohlc_basis', 'volume_basis', 'turnover_basis', 'factor_basis'))
                         and provenance['publication_source'] in ('EXPLICIT', 'DATE_PLUS_4'))
            else:
                valid = all(value is None for value in provenance.values())
            if not valid or not _valid_lineage_relation(table, payload['values'], provenance):
                _fail()
            expected_key_fields = {
                'listed': ['code'], 'prices_daily': ['code', 'date'],
                'margin_weekly': ['code', 'date'], 'index_daily': ['name', 'date'],
                'calendar': ['date']}[table]
            if _canonical([payload['values'].get(name) for name in expected_key_fields]) != key:
                _fail()
            marker = (table, key)
            if marker in seen:
                _fail()
            seen.add(marker)
            tables[table].append(payload)
        if any(not tables[table] for table in TABLES):
            _fail()
        envelope = {'schema_version': 1, 'contract': CONTRACT,
                    'request_start': start.isoformat(), 'request_end': end.isoformat(),
                    'tables': tables}
        if hashlib.sha256(_canonical(envelope).encode('utf-8')).hexdigest() != expected_hash:
            _fail()
        completed.add(run_id)

    orphan = con.execute(
        'SELECT 1 FROM market_ingest_run_rows rr LEFT JOIN market_ingest_runs r '
        'ON rr.run_id=r.run_id WHERE r.run_id IS NULL LIMIT 1').fetchone()
    if orphan is not None:
        _fail()
    if not allow_empty and not runs:
        _fail()

    current = con.execute(
        'SELECT table_name,row_key_json,run_id,ohlc_basis,volume_basis,'
        'turnover_basis,factor_basis,publication_source '
        'FROM market_row_provenance').fetchall()
    current_keys = set()
    for table, key, run_id, *detail_values in current:
        if table not in TABLES or run_id not in completed or (table, key) in current_keys:
            _fail()
        current_keys.add((table, key))
        record = con.execute(
            'SELECT normalized_row_json FROM market_ingest_run_rows '
            'WHERE run_id=? AND table_name=? AND row_key_json=?',
            [run_id, table, key]).fetchone()
        if record is None:
            _fail()
        payload = json.loads(record[0], object_pairs_hook=_strict_object,
                             parse_constant=lambda _: _fail())
        detail = payload['provenance']
        names = ('ohlc_basis', 'volume_basis', 'turnover_basis',
                 'factor_basis', 'publication_source')
        if tuple(detail.get(name) for name in names) != tuple(detail_values):
            _fail()

    queries = {
        'listed': ("SELECT code,name,market,sector33,listed_date FROM listed",
                   ('code', 'name', 'market', 'sector33', 'listed_date')),
        'prices_daily': ("SELECT code,date,open,high,low,close,volume,turnover,adj_factor FROM prices_daily",
                         ('code', 'date', 'open', 'high', 'low', 'close', 'volume', 'turnover', 'adj_factor')),
        'margin_weekly': ("SELECT code,date,publish_date,long_balance,short_balance FROM margin_weekly",
                          ('code', 'date', 'publish_date', 'long_balance', 'short_balance')),
        'index_daily': ("SELECT name,date,close FROM index_daily", ('name', 'date', 'close')),
        'calendar': ("SELECT date,is_business_day FROM calendar", ('date', 'is_business_day')),
    }
    actual = set()
    actual_values = {}
    for table in TABLES:
        query, columns = queries[table]
        key_columns = {'listed': 1, 'prices_daily': 2, 'margin_weekly': 2,
                       'index_daily': 2, 'calendar': 1}[table]
        for row in con.execute(query).fetchall():
            converted = [value.isoformat() if type(value) is date else value for value in row]
            key = _canonical(converted[:key_columns])
            actual.add((table, key))
            actual_values[(table, key)] = dict(zip(columns, converted))
    if actual != current_keys:
        _fail()
    if not allow_empty:
        for table in TABLES:
            if not any(item[0] == table for item in actual):
                _fail()
    for table, key, run_id, *_ in current:
        record = con.execute(
            'SELECT normalized_row_json FROM market_ingest_run_rows '
            'WHERE run_id=? AND table_name=? AND row_key_json=?',
            [run_id, table, key]).fetchone()
        payload = json.loads(record[0], object_pairs_hook=_strict_object,
                             parse_constant=lambda _: _fail())
        if payload['values'] != actual_values[(table, key)]:
            _fail()
    return {'runs': len(runs), 'current_rows': len(current_keys)}


def _insert_rows(con, table, rows):
    columns = list(rows[0])
    placeholders = ','.join('?' for _ in columns)
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    con.executemany(sql, [[row[column] for column in columns] for row in rows])


def ingest_legacy_fixture_with_provenance(home, *, start, end, recorded_at, fixture):
    """Store one fixed fixture atomically in an isolated provenance_fixture DB."""
    if type(start) is not date or type(end) is not date or start > end:
        _fail()
    if (not isinstance(recorded_at, datetime) or recorded_at.tzinfo is None
            or recorded_at.utcoffset() is None):
        _fail()
    try:
        normalized, details = _normalize_fixture(fixture, start, end)
    except KeyError:
        _fail()
    input_hash, run_id, row_records = _build_manifest(
        normalized, details, start, end)
    try:
        target = _runtime_path_guard(home)
    except (TypeError, ValueError, OSError):
        _fail()
    try:
        target.lstat()
        fresh = False
    except FileNotFoundError:
        fresh = True
    except OSError:
        _fail()
    if fresh:
        try:
            target.mkdir(parents=False, exist_ok=False)
        except (FileExistsError, OSError):
            _fail()
    else:
        try:
            database_info = (target / 'market.duckdb').lstat()
        except (FileNotFoundError, OSError):
            _fail()
        if not stat.S_ISREG(database_info.st_mode):
            _fail()
    with connect(target) as con:
        con.execute('BEGIN')
        try:
            if fresh:
                con.execute(SCHEMA)
                con.execute(PROVENANCE_SCHEMA_SQL)
                con.executemany('INSERT INTO provenance VALUES (?,?)', [
                    ('data_mode', 'provenance_fixture'),
                    ('market_provenance_schema', PROVENANCE_SCHEMA),
                    ('market_input_contract', CONTRACT)])
            if fresh:
                _verify_provenance_schema(con)
            else:
                _verify_provenance_integrity(con)
            prior = con.execute(
                'SELECT schema_version,contract,request_start,request_end,'
                'normalized_input_sha256,status FROM market_ingest_runs WHERE run_id=?',
                [run_id]).fetchone()
            if prior is not None:
                expected = (1, CONTRACT, start, end, input_hash, 'COMPLETED')
                indexed = con.execute(
                    'SELECT table_name,row_key_json,normalized_row_sha256,normalized_row_json '
                    'FROM market_ingest_run_rows WHERE run_id=? ORDER BY table_name,row_key_json',
                    [run_id]).fetchall()
                if prior != expected or indexed != sorted(row_records):
                    _fail()
                con.execute('ROLLBACK')
                return {'run_id': run_id, 'normalized_input_sha256': input_hash,
                        'result': 'NO_OP',
                        'tables': {table: len(normalized[table]) for table in TABLES}}
            for table in TABLES:
                _insert_rows(con, table, normalized[table])
            con.execute(
                'INSERT INTO market_ingest_runs VALUES (?,?,?,?,?,?,?,?)',
                [run_id, 1, CONTRACT, start, end, input_hash, recorded_at, 'COMPLETED'])
            con.executemany(
                'INSERT INTO market_ingest_run_rows VALUES (?,?,?,?,?)',
                [[run_id, *row] for row in row_records])
            for table, key, _, _ in row_records:
                detail = details[(table, key)]
                con.execute(
                    'INSERT OR REPLACE INTO market_row_provenance VALUES (?,?,?,?,?,?,?,?)',
                    [table, key, run_id, detail['ohlc_basis'], detail['volume_basis'],
                     detail['turnover_basis'], detail['factor_basis'],
                     detail['publication_source']])
            _verify_provenance_integrity(con)
            con.execute('COMMIT')
        except BaseException:
            try:
                con.execute('ROLLBACK')
            except BaseException:
                pass
            raise
    return {'run_id': run_id, 'normalized_input_sha256': input_hash,
            'result': 'COMPLETED',
            'tables': {table: len(normalized[table]) for table in TABLES}}
