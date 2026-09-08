"""Read-only bridge between the phase-1 market database and phase-2 packets."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .api import run_signals
from .db import connect
from .packet import build_proposals


def _mapping(path):
    if path is None:
        return {}
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict):
        raise ValueError('設定JSONは銘柄コードをキーにしたオブジェクトが必要です')
    return value


def generate(home,strategy,as_of,budget_per_name=250000,policy_version='review-v1',events_path=None,lots_path=None):
    candidates = run_signals(home,strategy,as_of)
    events, specified_lots = _mapping(events_path), _mapping(lots_path)
    with connect(home) as con:
        next_day = con.execute('SELECT min(date) FROM calendar WHERE date>? AND is_business_day=true',[as_of]).fetchone()[0]
        if next_day is None:
            raise ValueError('営業日カレンダーの範囲不足です。次営業日を推測しません。')
        price_day = con.execute('SELECT max(date) FROM prices_daily WHERE date<=?',[as_of]).fetchone()[0]
        if price_day is None:
            raise ValueError('参照日以前の価格がありません')
        all_prices = dict(con.execute('SELECT code,close FROM prices_daily WHERE date=? ORDER BY code',[price_day]).fetchall())
        provenance = dict(con.execute('SELECT key,value FROM provenance ORDER BY key').fetchall())
    prices = {c['code']:all_prices.get(c['code']) for c in candidates}
    lots = {c['code']:specified_lots.get(c['code'],100) for c in candidates}
    snapshot = {'candidates':candidates,'as_of':as_of,'price_day':price_day,'execution_day':next_day,
                'prices':prices,'lots':lots,'events':events,'provenance':provenance}
    snapshot_id = hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False,
                    separators=(',',':'),default=str,allow_nan=False).encode('utf-8')).hexdigest()
    ps = build_proposals(candidates,as_of,prices,lots,events,snapshot_id,policy_version,budget_per_name,
                         business_days=[next_day])
    return [asdict(p) for p in ps]
