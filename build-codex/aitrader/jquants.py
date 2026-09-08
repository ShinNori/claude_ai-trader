"""Legacy v1 adapter implementing the shared contract; live service unverified."""
import os
import warnings
import pandas as pd
import requests
from .db import init,connect

class JQuantsClient:
    base = 'https://api.jquants.com/v1'

    def __init__(self, refresh_token, session=None):
        if not refresh_token:
            raise ValueError('JQUANTS_REFRESH_TOKEN が未設定です。合成データへ自動代替しません。')
        self.session = session or requests.Session()
        response = self.session.post(self.base+'/token/auth_refresh',params={'refreshtoken':refresh_token},timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f'J-Quants認証失敗: HTTP {response.status_code}（応答本文・トークンは非表示）')
        token = response.json().get('idToken')
        if not token:
            raise RuntimeError('J-Quants認証形式が想定v1と一致しません')
        self.headers = {'Authorization':f'Bearer {token}'}

    def rows(self, endpoint, key, **params):
        rows, seen = [], set()
        while True:
            r = self.session.get(self.base+endpoint,headers=self.headers,params=params,timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f'J-Quants取得失敗: {endpoint} HTTP {r.status_code}')
            payload = r.json()
            if not isinstance(payload.get(key),list):
                raise ValueError(f'想定v1形式と不一致: {key}')
            rows.extend(payload[key])
            token = payload.get('pagination_key')
            if not token:
                return rows
            if token in seen:
                raise ValueError('Pagination key repeated')
            seen.add(token); params['pagination_key'] = token

def fetch(home, start, end, client=None):
    if start>end:
        raise ValueError('Invalid period')
    init(home)
    with connect(home) as con:
        mode = con.execute("SELECT value FROM provenance WHERE key='data_mode'").fetchone()
        if mode and mode[0]=='synthetic':
            raise ValueError('合成データと実データは混在不可。別のAI_TRADER_HOMEを指定してください。')
    c = client or JQuantsClient(os.environ.get('JQUANTS_REFRESH_TOKEN'))
    params = {'from':str(start),'to':str(end)}
    prices = c.rows('/prices/daily_quotes','daily_quotes',**params)
    margin = c.rows('/markets/weekly_margin_interest','weekly_margin_interest',**params)
    listed = c.rows('/listed/info','info',date=str(end))
    index = c.rows('/indices/topix','topix',**params)
    calendar = c.rows('/markets/trading_calendar','trading_calendar',**params)
    if not all((prices,margin,listed,index,calendar)):
        raise ValueError('必須データが空です。DBへの部分取込は行いません。')
    # Unknown listing dates are left unknown, never invented to pass the universe.
    li = pd.DataFrame([dict(code=str(r['Code']),name=r['CompanyName'],
         market={'0111':'prime','0112':'standard','0113':'growth'}.get(str(r.get('MarketCode')),'unknown'),
         sector33=str(r.get('Sector33Code','')),listed_date=r.get('ListedDate')) for r in listed])
    def price_row(r):
        adjusted = all(r.get('Adjustment'+key) is not None for key in ('Open','High','Low','Close'))
        prefix = 'Adjustment' if adjusted else ''
        return dict(code=str(r['Code']),date=r['Date'],open=r[prefix+'Open'],high=r[prefix+'High'],
                    low=r[prefix+'Low'],close=r[prefix+'Close'],volume=r.get('AdjustmentVolume',r.get('Volume')),
                    turnover=r['TurnoverValue'],adj_factor=r.get('AdjustmentFactor',1))
    px = pd.DataFrame([price_row(r) for r in prices])
    estimated = any(not r.get('PublishedDate') and not r.get('PublishDate') for r in margin)
    mw = pd.DataFrame([dict(code=str(r['Code']),date=r['Date'],
         publish_date=r.get('PublishedDate') or r.get('PublishDate') or (pd.Timestamp(r['Date'])+pd.Timedelta(days=4)).date(),
         long_balance=r['LongMarginTradeVolume'],short_balance=r['ShortMarginTradeVolume']) for r in margin])
    ix = pd.DataFrame([dict(name='TOPIX',date=r['Date'],close=r['Close']) for r in index])
    ca = pd.DataFrame([dict(date=r['Date'],is_business_day=str(r['HolidayDivision'])=='1') for r in calendar])
    if px[['open','high','low','close']].isna().any().any():
        raise ValueError('価格欠損あり。休止銘柄の処理を検証してから取り込んでください。')
    for frame in (li,px,mw,ix,ca):
        for col in ('date','publish_date','listed_date'):
            if col in frame:
                frame[col] = pd.to_datetime(frame[col])
    with connect(home) as con:
        con.execute('BEGIN')
        try:
            for table,frame in [('listed',li),('prices_daily',px),('margin_weekly',mw),('index_daily',ix),('calendar',ca)]:
                con.register('incoming',frame)
                con.execute(f'INSERT OR REPLACE INTO {table} SELECT * FROM incoming')
                con.unregister('incoming')
            con.execute("INSERT OR REPLACE INTO provenance VALUES ('data_mode','jquants')")
            con.execute("INSERT OR REPLACE INTO provenance VALUES ('api_contract','legacy-v1-unverified')")
            con.execute("INSERT OR REPLACE INTO provenance VALUES ('publication_estimated',?)",[str(estimated)])
            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK'); raise
    if estimated:
        warnings.warn('共通仕様の基準日+4暦日を公表日の推定値に使用。研究用に限定してください。')
    if li.listed_date.isna().any():
        warnings.warn('上場日が不明な銘柄は候補から除外されます。別途確認済み上場日が必要です。')
