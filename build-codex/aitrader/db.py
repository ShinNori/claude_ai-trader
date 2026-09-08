import os
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

def connect(home):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(home / 'market.duckdb'))
    con.execute('SET threads=1')
    return con

def init(home):
    with connect(home) as con:
        con.execute(SCHEMA)
