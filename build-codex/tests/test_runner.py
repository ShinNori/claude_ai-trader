"""Real SQLite/DuckDB integration with deterministic Verdict fixtures."""
import sys
import json
import sqlite3
from pathlib import Path
from datetime import date, datetime, timedelta
from dataclasses import replace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'build-codex'), str(ROOT/'common/tests/phase2')]
from test_ops_rereview import proposal, trade
from aitrader.runner import run, initialize_mock, mock_verdicts, Valuation, RunError
from aitrader.db import init, connect
from aitrader_ops.ledger import Ledger
from aitrader_ops.models import JST

DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 7, 10, tzinfo=JST)


@pytest.fixture
def setup(tmp_path):
    home = tmp_path/'mock'
    initialize_mock(home, 1000000, [], NOW-timedelta(days=1))
    init(home)
    with connect(home) as con:
        for i in range(-10, 31):
            d = DAY+timedelta(days=i)
            con.execute('INSERT INTO calendar VALUES(?,?)', [d, d.weekday() < 5])
        # Explicit as-of market observations, independent of proposed limit prices.
        con.executemany('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)',
                        [(code, DAY-timedelta(days=1), 1000.)
                         for code in ('6857', '7203', '6758')])
    def execute(ps=None, run_id='run1', now=NOW, decisions=None, votes=None, valuation=None):
        ps = ps if ps is not None else [proposal()]
        vs = votes if votes is not None else mock_verdicts(ps, run_id, min(now, NOW), decisions)
        return run(home, run_id, DAY, ps, vs, now, valuation or Valuation(1000000,1000000))
    return home, execute


def test_two_candidates_compete_for_available(setup):
    home, execute = setup
    l = Ledger(home/'ledger.sqlite')
    l.create_notice(proposal('seed', code='9999', limit_price=8500.), at=NOW)
    assert l.report(trade('seed', price=8500., broker_order_id='seed')).applied
    l.close()
    with connect(home) as con:
        con.execute('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)', ['9999', DAY-timedelta(days=1), 8500.])
    result = execute([proposal(), proposal('buy2', code='7203')])
    assert [r['status'] for r in result['candidates']] == ['APPROVED', 'REJECTED']
    assert 'INSUFFICIENT_AVAILABLE' in result['candidates'][1]['gate']['reason_codes']


def test_daily_slots_across_runs(setup):
    _, execute = setup
    execute([proposal(), proposal('buy2', code='7203')])
    result = execute([proposal('buy3', code='6758')], run_id='run2')
    assert 'DAILY_NEW_LIMIT' in result['candidates'][0]['gate']['reason_codes']


def test_invalid_side_cannot_reserve(setup):
    home, execute = setup
    result = execute(decisions={'claude': 'INVALID'})
    assert result['candidates'][0]['status'] == 'REJECTED'
    l = Ledger(home/'ledger.sqlite')
    assert l.reserved() == 0; l.close()


@pytest.mark.parametrize('offset,approved', [(-1, True), (0, False), (1, False)])
def test_deadline_boundary(setup, offset, approved):
    _, execute = setup
    now = NOW.replace(minute=15)+timedelta(seconds=offset)
    result = execute(now=now)
    assert (result['candidates'][0]['status'] == 'APPROVED') is approved
    if not approved:
        assert result['status'] == 'REVIEW_INCOMPLETE'


def test_same_run_no_duplicate_and_artifacts(setup):
    home, execute = setup
    first = execute()
    l = Ledger(home/'ledger.sqlite'); seq = l.seq(); l.close()
    second = execute(now=NOW+timedelta(hours=2))
    assert first == second
    l = Ledger(home/'ledger.sqlite'); assert l.seq() == seq; l.close()
    folder = home/'runs'/str(DAY)/'run1'
    assert all((folder/f'{name}.json').exists() for name in ('manifest', 'proposals', 'verdicts', 'gate_results', 'result'))
    with sqlite3.connect(home/'orchestration.sqlite') as con:
        assert con.execute('SELECT count(*) FROM outbox').fetchone()[0] == 2


def test_mutated_run_rejected(setup):
    _, execute = setup
    execute()
    with pytest.raises(RunError, match='入力変更'):
        execute([proposal(limit_price=999.)])


def test_one_verdict_marks_incomplete(setup):
    _, execute = setup
    ps = [proposal()]; votes = mock_verdicts(ps, 'run1', NOW)
    votes[ps[0].proposal_id].pop()
    assert execute(ps, votes=votes)['status'] == 'REVIEW_INCOMPLETE'


def test_old_run_vote_invalid(setup):
    _, execute = setup
    votes = mock_verdicts([proposal()], 'old', NOW)
    assert execute(votes=votes)['candidates'][0]['status'] == 'REJECTED'


def test_market_calendar_holiday_is_used(setup):
    home, execute = setup
    with connect(home) as con:
        con.execute("UPDATE calendar SET is_business_day=false WHERE date='2026-09-09'")
    p = proposal(events={'next_earnings_date': date(2026,9,11), 'margin_regulated': False})
    result = execute([p])
    assert 'EARNINGS_BLACKOUT' in result['candidates'][0]['gate']['reason_codes']


def test_calendar_gap_records_failure(setup):
    home, execute = setup
    with connect(home) as con:
        con.execute("DELETE FROM calendar WHERE date='2026-09-07'")
    with pytest.raises(RunError):
        execute()
    assert json.loads((home/'runs'/str(DAY)/'run1'/'failure.json').read_text(encoding='utf-8'))['status'] == 'SYSTEM_ERROR'


def test_stop_blocks_buy(setup):
    home, execute = setup
    (home/'STOP').touch()
    assert 'STOP_NEW' in execute()['candidates'][0]['gate']['reason_codes']


def test_flow_adjusted_loss_boundary(setup):
    home, execute = setup
    l = Ledger(home/'ledger.sqlite')
    l.adjust('DEPOSIT', 100000, None, None, NOW, '入金')
    l.adjust('FEE', 20000, None, None, NOW, '損失'); l.close()
    result = execute(valuation=Valuation(1000000,1000000,100000))
    assert 'DAILY_LOSS_STOP' in result['candidates'][0]['gate']['reason_codes']


def test_empty_candidates_no_signal(setup):
    _, execute = setup
    assert execute([])['status'] == 'NO_SIGNAL'


def test_existing_unmarked_ledger_cannot_be_used(tmp_path):
    l = Ledger(tmp_path/'ledger.sqlite'); l.close()
    with pytest.raises(RunError):
        initialize_mock(tmp_path, 1000000, [], NOW)


def test_uncertain_notice_stops_instead_of_duplicate(setup):
    home, execute = setup
    l = Ledger(home/'ledger.sqlite'); l.create_notice(proposal(), at=NOW); seq = l.seq(); l.close()
    with pytest.raises(RunError, match='台帳だけ'):
        execute()
    l = Ledger(home/'ledger.sqlite'); assert l.seq() == seq; l.close()


def test_cli_json_proposal_accepted(setup):
    _, execute = setup
    from aitrader.runner import plain
    p = proposal()
    result = run(setup[0], 'jsonrun', DAY, [plain(p)], mock_verdicts([p], 'jsonrun', NOW), NOW,
                 Valuation(1000000,1000000))
    assert result['candidates'][0]['status'] == 'APPROVED'


def test_crash_after_create_keeps_reserve_and_stops_resume(setup, monkeypatch):
    home, execute = setup
    original = Ledger.set_notice_state
    def fail(*args, **kwargs):
        raise RuntimeError('injected stop')
    monkeypatch.setattr(Ledger, 'set_notice_state', fail)
    with pytest.raises(RuntimeError):
        execute()
    monkeypatch.setattr(Ledger, 'set_notice_state', original)
    with pytest.raises(RunError, match='台帳だけ'):
        execute()
    l = Ledger(home/'ledger.sqlite')
    assert l.reserved() == 100200 and l.notice('buy1')['notice_state'] == 'CREATED'
    l.close()


def test_missing_mark_stops(setup):
    home, execute = setup
    l = Ledger(home/'ledger.sqlite'); l.create_notice(proposal('seed'), at=NOW)
    assert l.report(trade('seed')).applied; l.close()
    with connect(home) as con:
        con.execute('DELETE FROM prices_daily WHERE code=?', ['6857'])
    with pytest.raises(RunError, match='評価価格'):
        execute()
