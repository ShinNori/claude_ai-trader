"""Read-only integrity observation of the isolated lineage experiment."""
from contextlib import closing
from pathlib import Path

import duckdb

from .db import _runtime_path_guard
from .market_inspection import _fingerprint, _sidecars, _verify_schema, _SchemaError
from .provenance_fixture import _verify_provenance_integrity, TABLES


_EXTRA_TABLES = {
    'market_ingest_runs': (
        [('run_id', 'VARCHAR'), ('schema_version', 'INTEGER'), ('contract', 'VARCHAR'),
         ('request_start', 'DATE'), ('request_end', 'DATE'),
         ('normalized_input_sha256', 'VARCHAR'), ('recorded_at', 'TIMESTAMP WITH TIME ZONE'),
         ('status', 'VARCHAR')], ('run_id',)),
    'market_ingest_run_rows': (
        [('run_id', 'VARCHAR'), ('table_name', 'VARCHAR'), ('row_key_json', 'VARCHAR'),
         ('normalized_row_sha256', 'VARCHAR'), ('normalized_row_json', 'VARCHAR')],
        ('run_id', 'table_name', 'row_key_json')),
    'market_row_provenance': (
        [('table_name', 'VARCHAR'), ('row_key_json', 'VARCHAR'), ('run_id', 'VARCHAR'),
         ('ohlc_basis', 'VARCHAR'), ('volume_basis', 'VARCHAR'),
         ('turnover_basis', 'VARCHAR'), ('factor_basis', 'VARCHAR'),
         ('publication_source', 'VARCHAR')], ('table_name', 'row_key_json')),
}


def _result(status, reason, *, counts=None, runs=None, estimated=None):
    return {'status': status, 'reason': reason, 'tables': counts or {},
            'runs': runs, 'estimated_current_rows': estimated,
            'fixture_only': True, 'read_only': True, 'current_signal': False,
            'ready_for_live': False, 'automatic_resume_allowed': False,
            'observation_atomic': False, 'observation_unchanged': status == 'VERIFIED_FIXTURE'}


def _schema(con):
    _verify_schema(con)
    for table, (columns, key) in _EXTRA_TABLES.items():
        kind = con.execute("SELECT table_type FROM information_schema.tables "
                           "WHERE table_schema='main' AND table_name=?", [table]).fetchall()
        actual = con.execute("SELECT column_name,data_type FROM information_schema.columns "
                             "WHERE table_schema='main' AND table_name=? ORDER BY ordinal_position",
                             [table]).fetchall()
        keys = con.execute("SELECT constraint_column_names FROM duckdb_constraints() "
                           "WHERE schema_name='main' AND table_name=? AND constraint_type='PRIMARY KEY'",
                           [table]).fetchall()
        if kind != [('BASE TABLE',)] or actual != columns or keys != [(list(key),)]:
            raise ValueError('schema')


def inspect_provenance_fixture(home):
    """Verify recorded consistency, never market correctness or trade readiness."""
    try:
        target = _runtime_path_guard(home)
        database = target/'market.duckdb'
        if not database.is_file():
            return _result('UNKNOWN', 'DATABASE_MISSING')
        if database.stat().st_size > 128 * 1024 * 1024:
            return _result('UNKNOWN', 'OBSERVATION_LIMIT_EXCEEDED')
        before = _fingerprint(database)
        if any(_sidecars(database)):
            return _result('UNKNOWN', 'DATABASE_BUSY_OR_UNCHECKED')
        with closing(duckdb.connect(str(database), read_only=True, config={
                'enable_external_access': 'false', 'autoinstall_known_extensions': 'false',
                'autoload_known_extensions': 'false', 'threads': '1'})) as con:
            _schema(con)
            for table in (*TABLES, *_EXTRA_TABLES):
                if con.execute(f'SELECT count(*) FROM {table}').fetchone()[0] > 100000:
                    return _result('UNKNOWN', 'OBSERVATION_LIMIT_EXCEEDED')
            verified = _verify_provenance_integrity(con)
            counts = {table: con.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                      for table in TABLES}
            if verified['runs'] < 1 or any(count < 1 for count in counts.values()):
                return _result('UNKNOWN', 'SCHEMA_OR_LINEAGE_UNVERIFIED')
            estimated = con.execute("SELECT count(*) FROM market_row_provenance "
                                    "WHERE publication_source='DATE_PLUS_4'").fetchone()[0]
        _runtime_path_guard(target)
        if _fingerprint(database) != before or any(_sidecars(database)):
            return _result('UNKNOWN', 'OBSERVATION_CHANGED')
        return _result('VERIFIED_FIXTURE', 'RECORDED_LINEAGE_CONSISTENT',
                       counts=counts, runs=verified['runs'], estimated=estimated)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError,
            duckdb.Error, _SchemaError):
        return _result('UNKNOWN', 'SCHEMA_OR_LINEAGE_UNVERIFIED')
