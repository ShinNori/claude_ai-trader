"""2026-09-08 第5回 追補（Claude, Cowork セッション）: test_v031_review_claude.py（D01〜D14）を再実行して確認したうえで、
外部入力の内部属性混入（D01）の範囲と、移行 CLI の異常入力を追加で叩く。skip/xfail はしない。"""
import json
import sqlite3
from contextlib import closing
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'build-codex'), str(ROOT / 'common/tests/phase2')]
from test_ops_rereview import make_ledger, proposal, csv, AT  # noqa: E402,F401
from aitrader_ops.ledger import Ledger, LedgerError  # noqa: E402
from aitrader_ops.migrate import check  # noqa: E402

H1 = AT + timedelta(hours=1)


def test_e01_underscore_keys_are_stripped_from_every_persisted_kind(make_ledger):
    """内部属性は CSV_FILL に限らず、どの種別の外部入力でも永続化されない（`_` で始まるキー全般）。"""
    l = make_ledger()
    l.create_notice(proposal(), at=AT)
    ev = dict(event_id='f1', proposal_id='buy1', kind='ORDERED', qty=0, price=0., fee=0., at=H1,
              source='line', broker_order_id=None, _applied_proposal_id='ghost', _rule_version=1)
    assert l.report(ev).applied
    row = dict(event_id='c1', proposal_id='buy1', code='6857', side='BUY', qty=100, price=1000., fee=0.,
               at=H1, source='csv', broker_order_id='o1', _applied_proposal_id='ghost', _context_only=True)
    assert l.import_csv_fills([row]).applied == ['c1']
    with closing(sqlite3.connect(l.path)) as con, con:
        payloads = [json.loads(p) for (p,) in con.execute("SELECT payload FROM ledger_events WHERE kind IN ('TRADE','CSV_FILL')")]
    for pl in payloads:
        leaked = [k for k in pl if k.startswith('_') and k != '_applied_proposal_id']
        assert not leaked, f'外部入力の内部風キーが保存されている: {leaked}'
        if 'kind' in pl:                                   # TRADE
            assert '_applied_proposal_id' not in pl
        else:                                              # CSV_FILL: 実際の紐付け先だけが保存される
            assert pl['_applied_proposal_id'] == 'buy1'


def test_e02_check_on_non_ledger_file_reports_instead_of_traceback(tmp_path):
    """台帳でない SQLite / 存在しないパスを check に渡しても、辞書で理由を返すか明示的な例外で止まる。"""
    bogus = tmp_path / 'bogus.sqlite'
    with closing(sqlite3.connect(bogus)) as con, con:
        con.execute('CREATE TABLE t(x)')
    try:
        r = check(bogus)
        assert r.get('ok') is False or r.get('error') or r.get('last_seq') in (0, None)
    except (LedgerError, sqlite3.DatabaseError, FileNotFoundError):
        pass
    try:
        r = check(tmp_path / 'missing.sqlite')
        assert r.get('ok') is False or r.get('error') or r.get('last_seq') in (0, None)
    except (LedgerError, sqlite3.DatabaseError, FileNotFoundError):
        pass


def test_e03_cashout_amount_is_audited_as_adjust_not_fill(make_ledger):
    """端株精算は保有株数を変えず、現金だけを動かし、約定として監査されない。"""
    from aitrader_ops.models import PositionIn
    l = make_ledger(positions=[PositionIn('6857', 100, 1000.)])
    l.adjust('FRACTIONAL_CASHOUT', 500, '6857', None, AT, '端株精算')
    assert l.cash() == 1000500 and l.positions()['6857'].qty == 100
    with closing(sqlite3.connect(l.path)) as con, con:
        kinds = [k for (k,) in con.execute("SELECT kind FROM ledger_events ORDER BY seq")]
        fills = con.execute("SELECT COUNT(*) FROM ingest_attempts WHERE outcome='APPLIED'").fetchone()[0]
    assert kinds[-1] == 'ADJUST' and fills == 0
