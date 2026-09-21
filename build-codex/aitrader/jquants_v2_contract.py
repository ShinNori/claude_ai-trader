"""Strict local shape inspection for proposed J-Quants V2 fixture pages.

This module performs no communication and does not establish official API,
plan, economic-value, or live-readiness compliance.
"""
from __future__ import annotations

import math
from datetime import date


_ERROR = 'V2応答のローカル形状を確認できません'

_FIELDS = {
    'prices': frozenset(('Date', 'Code', 'O', 'H', 'L', 'C', 'Vo', 'Va',
                         'AdjFactor', 'AdjO', 'AdjH', 'AdjL', 'AdjC', 'AdjVo')),
    'margin': frozenset(('Date', 'Code', 'ShrtVol', 'LongVol')),
    'master': frozenset(('Date', 'Code', 'CoName', 'Mkt')),
    'topix': frozenset(('Date', 'O', 'H', 'L', 'C')),
    'calendar': frozenset(('Date', 'HolDiv')),
}

_NUMERIC = {
    'prices': frozenset(('O', 'H', 'L', 'C', 'Vo', 'Va', 'AdjFactor',
                         'AdjO', 'AdjH', 'AdjL', 'AdjC', 'AdjVo')),
    'margin': frozenset(('ShrtVol', 'LongVol')),
    'master': frozenset(),
    'topix': frozenset(('O', 'H', 'L', 'C')),
    'calendar': frozenset(),
}


def _fail():
    raise ValueError(_ERROR) from None


def _iso_day(value):
    if not isinstance(value, str):
        _fail()
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail()
    if parsed.isoformat() != value:
        _fail()


def _number(value):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail()
    try:
        finite = math.isfinite(float(value))
    except OverflowError:
        _fail()
    if not finite:
        _fail()


def inspect_v2_page(dataset, payload):
    """Validate one in-memory proposed V2 page and return metadata only."""
    if not isinstance(dataset, str) or dataset not in _FIELDS:
        _fail()
    if not isinstance(payload, dict):
        _fail()
    rows = payload.get('data')
    if not isinstance(rows, list):
        _fail()
    token = payload.get('pagination_key')
    if token is None or token == '':
        has_more = False
    elif isinstance(token, str):
        has_more = True
    else:
        _fail()

    required = _FIELDS[dataset]
    numeric = _NUMERIC[dataset]
    for row in rows:
        if not isinstance(row, dict) or not required.issubset(row):
            _fail()
        _iso_day(row['Date'])
        if dataset in ('prices', 'margin', 'master'):
            code = row['Code']
            if not isinstance(code, str) or not code:
                _fail()
        if dataset == 'master':
            if (not isinstance(row['CoName'], str) or not row['CoName']
                    or not isinstance(row['Mkt'], str)):
                _fail()
        if dataset == 'calendar' and not isinstance(row['HolDiv'], str):
            _fail()
        for field in numeric:
            _number(row[field])

    return {'contract': 'v2-shape-only', 'dataset': dataset,
            'row_count': len(rows), 'has_more': has_more,
            'read_only': True, 'ready_for_live': False}
