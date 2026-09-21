"""Read-only bridge between the phase-1 market database and phase-2 packets."""
import hashlib
import json
import math
import os
import stat
from dataclasses import asdict
from pathlib import Path

from .api import _run_signals_in_connection
from .strategies import validate_strategy_name
from .db import connect
from .packet import build_proposals


_MAX_MAPPING_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('設定JSONのキーが重複しています')
        value[key] = item
    return value


def _is_link(path, observed):
    return (stat.S_ISLNK(observed.st_mode)
            or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))
            or bool(getattr(observed, 'st_file_attributes', 0)
                    & _REPARSE_POINT))


def _mapping(path):
    if path is None:
        return {}
    path = Path(path).absolute()
    for component in reversed((path, *path.parents)):
        try:
            observed = component.lstat()
        except FileNotFoundError:
            continue
        if _is_link(component, observed):
            raise ValueError('設定JSONにlink/reparse pointは使用できません')
    before = path.lstat()
    if (_is_link(path, before) or not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAX_MAPPING_BYTES):
        raise ValueError('設定JSONは1MiB以下の通常fileが必要です')
    with path.open('rb') as stream:
        opened_before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened_before.st_mode)
                or (opened_before.st_dev, opened_before.st_ino,
                    opened_before.st_mode)
                != (before.st_dev, before.st_ino, before.st_mode)):
            raise ValueError('設定JSONが観測中に変化しました')
        body = stream.read(_MAX_MAPPING_BYTES + 1)
        opened_after = os.fstat(stream.fileno())
    after = path.lstat()
    if (len(body) > _MAX_MAPPING_BYTES or _is_link(path, after)
            or len(body) != before.st_size
            or (after.st_dev, after.st_ino, after.st_mode, after.st_size,
                after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_mode, before.st_size,
                before.st_mtime_ns)
            or (opened_after.st_dev, opened_after.st_ino,
                opened_after.st_mode, opened_after.st_size,
                opened_after.st_mtime_ns)
            != (opened_before.st_dev, opened_before.st_ino,
                opened_before.st_mode, opened_before.st_size,
                opened_before.st_mtime_ns)):
        raise ValueError('設定JSONが観測中に変化しました')
    value = json.loads(
        body.decode('utf-8-sig'), object_pairs_hook=_unique_object,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f'設定JSONに非有限値は使用できません: {token}')))
    if not isinstance(value,dict):
        raise ValueError('設定JSONは銘柄コードをキーにしたオブジェクトが必要です')
    def finite(item):
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError('設定JSONに非有限値は使用できません')
        if isinstance(item, dict):
            for nested in item.values():
                finite(nested)
        elif isinstance(item, list):
            for nested in item:
                finite(nested)
    finite(value)
    return value


def generate(home,strategy,as_of,budget_per_name=250000,policy_version='review-v1',events_path=None,lots_path=None):
    validate_strategy_name(strategy)
    events, specified_lots = _mapping(events_path), _mapping(lots_path)
    with connect(home) as con:
        con.execute('BEGIN')
        try:
            candidates = _run_signals_in_connection(con, strategy, as_of)
            next_day = con.execute('SELECT min(date) FROM calendar WHERE date>? AND is_business_day=true',[as_of]).fetchone()[0]
            if next_day is None:
                raise ValueError('営業日カレンダーの範囲不足です。次営業日を推測しません。')
            price_day = con.execute('SELECT max(date) FROM prices_daily WHERE date<=?',[as_of]).fetchone()[0]
            if price_day is None:
                raise ValueError('参照日以前の価格がありません')
            if price_day != as_of:
                raise ValueError('基準日当日の価格がありません。古い価格では候補を生成しません。')
            all_prices = dict(con.execute('SELECT code,close FROM prices_daily WHERE date=? ORDER BY code',[price_day]).fetchall())
            provenance = dict(con.execute('SELECT key,value FROM provenance ORDER BY key').fetchall())
            con.execute('COMMIT')
        except BaseException:
            try:
                con.execute('ROLLBACK')
            except BaseException:
                pass
            raise
    prices = {c['code']:all_prices.get(c['code']) for c in candidates}
    lots = {c['code']:specified_lots.get(c['code'],100) for c in candidates}
    snapshot = {'candidates':candidates,'as_of':as_of,'price_day':price_day,'execution_day':next_day,
                'prices':prices,'lots':lots,'events':events,'provenance':provenance}
    snapshot_id = hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False,
                    separators=(',',':'),default=str,allow_nan=False).encode('utf-8')).hexdigest()
    ps = build_proposals(candidates,as_of,prices,lots,events,snapshot_id,policy_version,budget_per_name,
                         business_days=[next_day])
    return [asdict(p) for p in ps]
