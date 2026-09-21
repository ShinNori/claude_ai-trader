import os
import stat
from pathlib import Path
import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS listed(code TEXT PRIMARY KEY,name TEXT,market TEXT,sector33 TEXT,listed_date DATE);
CREATE TABLE IF NOT EXISTS prices_daily(code TEXT,date DATE,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE,turnover DOUBLE,adj_factor DOUBLE,PRIMARY KEY(code,date));
CREATE TABLE IF NOT EXISTS margin_weekly(code TEXT,date DATE,publish_date DATE,long_balance DOUBLE,short_balance DOUBLE,PRIMARY KEY(code,date));
CREATE TABLE IF NOT EXISTS index_daily(name TEXT,date DATE,close DOUBLE,PRIMARY KEY(name,date));
CREATE TABLE IF NOT EXISTS calendar(date DATE PRIMARY KEY,is_business_day BOOLEAN);
CREATE TABLE IF NOT EXISTS provenance(key TEXT PRIMARY KEY,value TEXT);
"""

def default_home():
    return Path(os.environ.get('AI_TRADER_HOME') or Path.home() / '.ai-trader')

_REPARSE_POINT = 0x400


def _runtime_path_guard(home):
    """Validate operational DuckDB paths without resolving away links."""
    try:
        home = Path(home).absolute()
    except (TypeError, ValueError, OSError):
        raise ValueError('市場DBの実行先を安全に確認できません') from None
    if any('dropbox' in part.lower() for part in home.parts):
        raise ValueError('市場DBの実行先はDropbox外にしてください')

    def observe(path, expected):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise ValueError('市場DBの実行先を安全に確認できません') from None
        unsafe = (stat.S_ISLNK(info.st_mode)
                  or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))
                  or bool(getattr(info, 'st_file_attributes', 0) & _REPARSE_POINT))
        valid_type = stat.S_ISDIR(info.st_mode) if expected == 'directory' else stat.S_ISREG(info.st_mode)
        if unsafe or not valid_type:
            raise ValueError('市場DBの実行先を安全に確認できません')
        return True

    for parent in reversed((home, *home.parents)):
        observe(parent, 'directory')
    database = home/'market.duckdb'
    observe(database, 'file')
    observe(Path(str(database)+'.wal'), 'file')
    observe(Path(str(database)+'.tmp'), 'directory')
    return home


def connect(home):
    home = _runtime_path_guard(home)
    home.mkdir(parents=True, exist_ok=True)
    home = _runtime_path_guard(home)
    con = duckdb.connect(str(home / 'market.duckdb'))
    try:
        con.execute('SET threads=1')
    except BaseException:
        try:
            con.close()
        except BaseException:
            pass
        raise
    return con

def init(home):
    with connect(home) as con:
        con.execute(SCHEMA)


def require_data_mode(con, expected):
    """Permit a same-source reload, or a schema with no market rows yet."""
    if expected not in ('synthetic', 'jquants'):
        raise ValueError('Unsupported data mode')
    row = con.execute("SELECT value FROM provenance WHERE key='data_mode'").fetchone()
    if row is not None:
        if row[0] != expected:
            raise ValueError('異なる出所のデータは混在不可。別のAI_TRADER_HOMEを指定してください。')
        return
    for table in ('listed', 'prices_daily', 'margin_weekly', 'index_daily', 'calendar'):
        if con.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() is not None:
            raise ValueError('出所不明の既存データは使用できません。別のAI_TRADER_HOMEを指定してください。')


def require_known_data_mode(con):
    row = con.execute("SELECT value FROM provenance WHERE key='data_mode'").fetchone()
    if row is None or row[0] not in ('synthetic', 'jquants'):
        raise ValueError('Data provenance missing or unsupported; load verified data first')
    return row[0]
