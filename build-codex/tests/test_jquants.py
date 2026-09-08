from datetime import date
import warnings
import pytest
from aitrader.db import init,connect
from aitrader.jquants import fetch,JQuantsClient

class FixtureClient:
    def rows(self, endpoint, key, **params):
        rows = {
            'daily_quotes':[dict(Code='1300',Date='2026-08-31',Open=100,High=110,Low=90,Close=105,Volume=10000,TurnoverValue=1000000)],
            'weekly_margin_interest':[dict(Code='1300',Date='2026-08-28',LongMarginTradeVolume=100,ShortMarginTradeVolume=50)],
            'info':[dict(Code='1300',CompanyName='Fixture',MarketCode='0111',Sector33Code='10')],
            'topix':[dict(Date='2026-08-31',Close=2000)],
            'trading_calendar':[dict(Date='2026-08-31',HolidayDivision='1')],
        }
        return rows[key]

def test_ingestion_repeated_and_unknown_listing(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        for _ in range(2): fetch(tmp_path,date(2026,8,1),date(2026,8,31),FixtureClient())
    with connect(tmp_path) as con:
        assert con.execute('SELECT count(*) FROM prices_daily').fetchone()[0]==1
        assert con.execute('SELECT listed_date FROM listed').fetchone()[0] is None
        assert con.execute('SELECT publish_date FROM margin_weekly').fetchone()[0]==date(2026,9,1)

def test_real_and_synthetic_cannot_mix(tmp_path):
    init(tmp_path)
    with connect(tmp_path) as con:
        con.execute("INSERT INTO provenance VALUES ('data_mode','synthetic')")
    with pytest.raises(ValueError,match='混在不可'):
        fetch(tmp_path,date(2026,8,1),date(2026,8,31),FixtureClient())

def test_empty_response_does_not_partially_insert(tmp_path):
    class Missing(FixtureClient):
        def rows(self,endpoint,key,**params):
            return [] if key=='topix' else super().rows(endpoint,key,**params)
    with pytest.raises(ValueError,match='必須データ'):
        fetch(tmp_path,date(2026,8,1),date(2026,8,31),Missing())
    with connect(tmp_path) as con:
        assert con.execute('SELECT count(*) FROM prices_daily').fetchone()[0]==0

def test_missing_credentials():
    with pytest.raises(ValueError,match='未設定'):
        JQuantsClient('')
