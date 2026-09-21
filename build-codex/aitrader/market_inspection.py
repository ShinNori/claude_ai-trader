"""Bounded, read-only inspection of an existing phase-1 market database."""
from __future__ import annotations

import hashlib
import os
import stat
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import duckdb


_TABLES = {
    'listed': ([('code', 'VARCHAR'), ('name', 'VARCHAR'), ('market', 'VARCHAR'),
                ('sector33', 'VARCHAR'), ('listed_date', 'DATE')], ('code',), 'listed_date'),
    'prices_daily': ([('code', 'VARCHAR'), ('date', 'DATE'), ('open', 'DOUBLE'),
                      ('high', 'DOUBLE'), ('low', 'DOUBLE'), ('close', 'DOUBLE'),
                      ('volume', 'DOUBLE'), ('turnover', 'DOUBLE'), ('adj_factor', 'DOUBLE')],
                     ('code', 'date'), 'date'),
    'margin_weekly': ([('code', 'VARCHAR'), ('date', 'DATE'), ('publish_date', 'DATE'),
                       ('long_balance', 'DOUBLE'), ('short_balance', 'DOUBLE')],
                      ('code', 'date'), 'date'),
    'index_daily': ([('name', 'VARCHAR'), ('date', 'DATE'), ('close', 'DOUBLE')],
                    ('name', 'date'), 'date'),
    'calendar': ([('date', 'DATE'), ('is_business_day', 'BOOLEAN')], ('date',), 'date'),
    'provenance': ([('key', 'VARCHAR'), ('value', 'VARCHAR')], ('key',), None),
}
_SIDECARS = ('.wal', '.tmp')
_REPARSE_POINT = 0x400
_LIMIT_WARNINGS = ('ADJUSTMENT_BASIS_UNVERIFIED', 'LOT_SIZE_UNVERIFIED',
                   'EVENT_INPUT_UNVERIFIED')


class _SchemaError(Exception):
    pass


def _unknown(as_of, reason):
    return {'status': 'UNKNOWN', 'source': 'UNKNOWN', 'as_of': as_of.isoformat(),
            'tables': {}, 'warnings': [], 'read_only': True, 'current_signal': False,
            'ready_for_live': False, 'observation_atomic': False,
            'observation_unchanged': False, 'reason': reason}


def _is_link(path, observed):
    return (stat.S_ISLNK(observed.st_mode)
            or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))
            or bool(getattr(observed, 'st_file_attributes', 0) & _REPARSE_POINT))


def _guard_path(path):
    for component in reversed((path, *path.parents)):
        try:
            observed = component.lstat()
        except FileNotFoundError:
            continue
        if _is_link(component, observed):
            raise OSError('unsafe path')


def _fingerprint(path):
    _guard_path(path)
    before = path.lstat()
    if _is_link(path, before) or not stat.S_ISREG(before.st_mode):
        raise OSError('unsafe database')
    identity = lambda item: (item.st_dev, item.st_ino, item.st_mode)
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or identity(opened) != identity(before):
            raise OSError('changed database')
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    after = path.lstat()
    if (_is_link(path, after) or identity(after) != identity(before)
            or size != before.st_size or size != opened.st_size
            or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns)):
        raise OSError('changed database')
    return identity(before), before.st_size, before.st_mtime_ns, digest.hexdigest()


def _sidecars(database):
    result = []
    for suffix in _SIDECARS:
        sidecar = Path(str(database) + suffix)
        try:
            _guard_path(sidecar)
            observed = sidecar.lstat()
        except FileNotFoundError:
            result.append(False)
            continue
        if _is_link(sidecar, observed) or not stat.S_ISREG(observed.st_mode):
            raise OSError('unsafe sidecar')
        result.append(True)
    return tuple(result)


def _verify_schema(connection):
    table_rows = connection.execute(
        "SELECT table_name,table_type FROM information_schema.tables "
        "WHERE table_schema='main'").fetchall()
    actual_tables = {name: kind for name, kind in table_rows}
    if any(actual_tables.get(name) != 'BASE TABLE' for name in _TABLES):
        raise _SchemaError
    for table, (expected_columns, expected_pk, _) in _TABLES.items():
        columns = connection.execute(
            "SELECT column_name,data_type FROM information_schema.columns "
            "WHERE table_schema='main' AND table_name=? ORDER BY ordinal_position", [table]
        ).fetchall()
        if columns != expected_columns:
            raise _SchemaError
        primary = connection.execute(
            "SELECT k.column_name FROM information_schema.table_constraints t "
            "JOIN information_schema.key_column_usage k "
            "ON t.constraint_catalog=k.constraint_catalog AND t.constraint_schema=k.constraint_schema "
            "AND t.constraint_name=k.constraint_name "
            "WHERE t.table_schema='main' AND t.table_name=? "
            "AND t.constraint_type='PRIMARY KEY' ORDER BY k.ordinal_position", [table]
        ).fetchall()
        if tuple(item[0] for item in primary) != expected_pk:
            raise _SchemaError


def _has(connection, query, params=None):
    return connection.execute(query, params or []).fetchone()[0] > 0


def _is_ascii_integer_text(value):
    if not isinstance(value, str) or not value:
        return False
    digits = value[1:] if value.startswith('-') else value
    return bool(digits) and all('0' <= character <= '9' for character in digits)


def inspect_market_inputs(home, *, as_of: date):
    """Inspect aggregates only; this does not establish live-data readiness."""
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ValueError('as_ofはdateで指定してください')
    try:
        home = Path(home).absolute()
    except (TypeError, ValueError, OSError):
        return _unknown(as_of, 'INVALID_HOME')
    if any('dropbox' in part.lower() for part in home.parts):
        return _unknown(as_of, 'DROPBOX_HOME_REJECTED')
    database = home/'market.duckdb'
    try:
        before_sidecars = _sidecars(database)
        if any(before_sidecars):
            return _unknown(as_of, 'DATABASE_SIDECAR_PRESENT')
        before = _fingerprint(database)
    except (OSError, ValueError):
        return _unknown(as_of, 'DATABASE_UNAVAILABLE')

    warnings = list(_LIMIT_WARNINGS)
    tables = {}
    source = 'UNKNOWN'
    try:
        with closing(duckdb.connect(
                str(database), read_only=True,
                config={'enable_external_access': 'false',
                        'autoinstall_known_extensions': 'false',
                        'autoload_known_extensions': 'false'})) as connection:
            _verify_schema(connection)
            for table, (_, _, date_column) in _TABLES.items():
                if table == 'provenance':
                    continue
                row = connection.execute(
                    f'SELECT count(*),min({date_column}),max({date_column}) FROM {table}'
                ).fetchone()
                count, minimum, maximum = row
                if type(count) is not int or count < 0:
                    raise _SchemaError
                tables[table] = {'rows': count,
                                 'date_min': minimum.isoformat() if isinstance(minimum, date) else None,
                                 'date_max': maximum.isoformat() if isinstance(maximum, date) else None,
                                 'scope': 'SAVED_ALL_ROWS'}
                if count == 0:
                    warnings.append('REQUIRED_TABLE_EMPTY')
                elif (not isinstance(minimum, date) or not isinstance(maximum, date)
                      or minimum > maximum):
                    warnings.append('DATE_RANGE_EMPTY_OR_INVALID')

            provenance_rows = connection.execute(
                "SELECT key,value FROM provenance WHERE key IN "
                "('data_mode','seed','api_contract','publication_estimated') ORDER BY key"
            ).fetchall()
            provenance = {}
            for key, value in provenance_rows:
                if key in provenance:
                    raise _SchemaError
                provenance[key] = value
            mode = provenance.get('data_mode')
            if mode == 'synthetic':
                source = 'synthetic'
                seed = provenance.get('seed')
                if (not _is_ascii_integer_text(seed)
                        or 'api_contract' in provenance or 'publication_estimated' in provenance):
                    warnings.append('PROVENANCE_INCOMPLETE')
            elif mode == 'jquants' and provenance.get('api_contract') == 'legacy-v1-unverified':
                source = 'jquants_legacy_unverified'
                warnings.append('PUBLICATION_ESTIMATE_HISTORY_UNVERIFIED')
                if (provenance.get('publication_estimated') not in ('True', 'False')
                        or 'seed' in provenance):
                    warnings.append('PROVENANCE_INCOMPLETE')
            else:
                warnings.append('PROVENANCE_INCOMPLETE')

            required_missing = (
                _has(connection, 'SELECT count(*) FROM prices_daily WHERE code IS NULL OR date IS NULL '
                     'OR open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL '
                     'OR volume IS NULL OR turnover IS NULL OR adj_factor IS NULL')
                or _has(connection, 'SELECT count(*) FROM margin_weekly WHERE code IS NULL OR date IS NULL '
                        'OR long_balance IS NULL OR short_balance IS NULL')
                or _has(connection, 'SELECT count(*) FROM index_daily WHERE name IS NULL OR date IS NULL OR close IS NULL')
                or _has(connection, 'SELECT count(*) FROM calendar WHERE date IS NULL OR is_business_day IS NULL'))
            if required_missing:
                warnings.append('REQUIRED_VALUE_MISSING')
            if _has(connection, 'SELECT count(*) FROM calendar WHERE is_business_day IS NULL'):
                warnings.append('CALENDAR_VALUE_INVALID')
            if _has(connection, 'SELECT count(*) FROM listed WHERE listed_date IS NULL'):
                warnings.append('LISTED_DATE_MISSING')
            if _has(connection, 'SELECT count(*) FROM margin_weekly WHERE publish_date IS NULL'):
                warnings.append('PUBLICATION_DATE_MISSING')
            numeric = (
                _has(connection, 'SELECT count(*) FROM prices_daily WHERE NOT isfinite(open) OR NOT isfinite(high) '
                     'OR NOT isfinite(low) OR NOT isfinite(close) OR NOT isfinite(volume) '
                     'OR NOT isfinite(turnover) OR NOT isfinite(adj_factor)')
                or _has(connection, 'SELECT count(*) FROM margin_weekly WHERE NOT isfinite(long_balance) '
                        'OR NOT isfinite(short_balance)')
                or _has(connection, 'SELECT count(*) FROM index_daily WHERE NOT isfinite(close)'))
            if numeric:
                warnings.append('NONFINITE_NUMERIC_PRESENT')
            if (_has(connection, 'SELECT count(*) FROM prices_daily WHERE open<=0 OR high<=0 OR low<=0 OR close<=0')
                    or _has(connection, 'SELECT count(*) FROM index_daily WHERE close<=0')):
                warnings.append('NONPOSITIVE_PRICE_PRESENT')
            if _has(connection, 'SELECT count(*) FROM prices_daily WHERE high<low OR open<low OR open>high '
                     'OR close<low OR close>high'):
                warnings.append('OHLC_RANGE_INVALID')
            if (_has(connection, 'SELECT count(*) FROM prices_daily WHERE date>?', [as_of])
                    or _has(connection, 'SELECT count(*) FROM margin_weekly WHERE date>?', [as_of])
                    or _has(connection, 'SELECT count(*) FROM index_daily WHERE date>?', [as_of])):
                warnings.append('FUTURE_MARKET_ROWS')
            if not connection.execute('SELECT count(*) FROM calendar WHERE date=?', [as_of]).fetchone()[0]:
                warnings.append('CALENDAR_AS_OF_MISSING')
            if not connection.execute('SELECT count(*) FROM prices_daily WHERE date=?', [as_of]).fetchone()[0]:
                warnings.append('AS_OF_PRICE_MISSING')
            if not connection.execute(
                    'SELECT count(*) FROM calendar WHERE date>? AND is_business_day=true', [as_of]
            ).fetchone()[0]:
                warnings.append('CALENDAR_NEXT_SESSION_MISSING')
    except _SchemaError:
        return _unknown(as_of, 'SCHEMA_UNVERIFIED')
    except (duckdb.Error, OSError, TypeError, ValueError, OverflowError):
        return _unknown(as_of, 'DATABASE_UNREADABLE')

    try:
        after = _fingerprint(database)
        after_sidecars = _sidecars(database)
    except (OSError, ValueError):
        return _unknown(as_of, 'OBSERVATION_CHANGED')
    if before != after or before_sidecars != after_sidecars or any(after_sidecars):
        return _unknown(as_of, 'OBSERVATION_CHANGED')
    return {'status': 'INSPECTED', 'source': source, 'as_of': as_of.isoformat(),
            'tables': tables, 'warnings': sorted(set(warnings)), 'read_only': True,
            'current_signal': False, 'ready_for_live': False,
            'observation_atomic': False, 'observation_unchanged': True}
