"""Externally supplied BUY proposals still require a usable as-of close."""
from contextlib import closing
from datetime import timedelta

import pytest

from test_runner import setup, proposal, DAY, Ledger, RunError
from aitrader.db import connect


@pytest.mark.parametrize('case', ['missing', 'other_code', 'older', 'zero', 'negative',
                                  'nan', 'positive_infinity', 'negative_infinity'])
def test_unusable_buy_reference_price_cannot_reserve(setup, case):
    home, execute = setup
    p = proposal()
    with connect(home) as con:
        con.execute('DELETE FROM prices_daily')
        if case != 'missing':
            value = {'zero': 0., 'negative': -1., 'nan': float('nan'),
                     'positive_infinity': float('inf'), 'negative_infinity': -float('inf')}.get(case, 1000.)
            con.execute('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)',
                        ['OTHER' if case == 'other_code' else p.code,
                         p.as_of-timedelta(days=1) if case == 'older' else p.as_of, value])
    try:
        result = execute([p])
    except RunError:
        pass
    else:
        assert not any(c['status'] == 'APPROVED' for c in result['candidates'])
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        assert ledger.reserved() == 0


def test_finite_positive_asof_price_can_approve(setup):
    home, execute = setup
    p = proposal()
    with connect(home) as con:
        con.execute('INSERT OR REPLACE INTO prices_daily(code,date,close) VALUES(?,?,?)',
                    [p.code, p.as_of, 1000.])
    result = execute([p])
    assert result['candidates'][0]['status'] == 'APPROVED'
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        assert ledger.reserved() > 0
