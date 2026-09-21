"""Candidate journal rows may only be reused under their exact saved binding."""
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta

import pytest

from aitrader.db import connect, init
from aitrader.runner import (RunError, Valuation, initialize_mock,
                             mock_verdicts, run)
from aitrader.runner_diagnostics import diagnose_mock_run
from aitrader_ops.ledger import Ledger
from aitrader_ops.models import JST
from test_ops_rereview import proposal


DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 7, 10, tzinfo=JST)


@pytest.fixture
def env(tmp_path):
    home = tmp_path/'mock'
    initialize_mock(home, 1_000_000, [], NOW-timedelta(days=1))
    init(home)
    with connect(home) as con:
        for offset in range(-10, 31):
            day = DAY+timedelta(days=offset)
            con.execute('INSERT INTO calendar VALUES(?,?)',
                        [day, day.weekday() < 5])
        con.executemany('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)', [
            ('6857', DAY-timedelta(days=1), 1000.),
            ('7203', DAY-timedelta(days=1), 1000.),
            ('6758', DAY-timedelta(days=1), 1000.),
        ])

    def execute(run_id='run1', item=None, decisions=None):
        item = item or proposal()
        verdicts = mock_verdicts([item], run_id, NOW, decisions)
        return run(home, run_id, DAY, [item], verdicts, NOW,
                   Valuation(1_000_000, 1_000_000))
    return home, execute


def durable_counts(home):
    with sqlite3.connect(home/'orchestration.sqlite') as con:
        journal = con.execute(
            'SELECT (SELECT count(*) FROM candidates),'
            '(SELECT count(*) FROM outbox)').fetchone()
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        return (*journal, ledger.reserved(), ledger.seq())


@pytest.mark.parametrize(('column', 'value'), [
    ('owner', 'other-run'), ('day', '2026-09-09'), ('side', 'SELL'),
    ('hash', '0'*64),
])
@pytest.mark.parametrize('approved', [True, False])
def test_diagnostics_flags_completed_binding_and_incomplete_run_rejects_reuse(
        env, column, value, approved):
    home, execute = env
    decisions = None if approved else {'claude': 'REJECT'}
    completed = execute(decisions=decisions)
    with sqlite3.connect(home/'orchestration.sqlite') as con:
        con.execute(f'UPDATE candidates SET {column}=?', [value])
    report = diagnose_mock_run(home, 'run1', DAY)
    assert report['classification'] == 'CONFLICT'
    assert report['candidates'][0]['classification'] == 'CONFLICT'
    # The established completed-run fast path returns its saved run result.
    assert execute(decisions=decisions) == completed
    with sqlite3.connect(home/'orchestration.sqlite') as con:
        con.execute('UPDATE runs SET result=NULL WHERE id=?', ['run1'])
    before = durable_counts(home)
    with pytest.raises(RunError):
        execute(decisions=decisions)
    assert durable_counts(home) == before


@pytest.mark.parametrize('approved', [True, False])
def test_completed_candidate_cannot_be_reused_by_another_run(env, approved):
    home, execute = env
    decisions = None if approved else {'claude': 'REJECT'}
    execute(decisions=decisions)
    before = durable_counts(home)
    with pytest.raises(RunError):
        execute(run_id='run2', decisions=decisions)
    assert durable_counts(home) == before


def test_unknown_candidate_state_is_rejected_without_side_effect(env):
    home, execute = env
    execute()
    with sqlite3.connect(home/'orchestration.sqlite') as con:
        con.execute("UPDATE candidates SET state='UNKNOWN_STATE'")
    assert diagnose_mock_run(home, 'run1', DAY)['classification'] == 'CONFLICT'
    with sqlite3.connect(home/'orchestration.sqlite') as con:
        con.execute('UPDATE runs SET result=NULL WHERE id=?', ['run1'])
    before = durable_counts(home)
    with pytest.raises(RunError):
        execute()
    assert durable_counts(home) == before


@pytest.mark.parametrize(('field', 'value'), [
    ('proposal_id', 'different-proposal'), ('status', 'REJECTED'),
])
def test_incomplete_run_rejects_prior_result_identity_mismatch(env, field, value):
    home, execute = env
    execute()
    with sqlite3.connect(home/'orchestration.sqlite') as con:
        con.execute('UPDATE runs SET result=NULL WHERE id=?', ['run1'])
        saved = json.loads(con.execute(
            'SELECT result FROM candidates WHERE pid=?', ['buy1']).fetchone()[0])
        saved[field] = value
        con.execute('UPDATE candidates SET result=? WHERE pid=?',
                    [json.dumps(saved, ensure_ascii=False), 'buy1'])
    before = durable_counts(home)
    with pytest.raises(RunError):
        execute()
    assert durable_counts(home) == before


@pytest.mark.parametrize('mutation', [
    lambda body: body.update(key='wrong-key'),
    lambda body: body.update(mode='not-mock'),
    lambda body: body.update(delivery='SENT'),
    lambda body: body.update(kind='EXIT'),
    lambda body: body.update(proposal={**body['proposal'], 'code': '7203'}),
])
def test_incomplete_approved_run_rejects_whole_outbox_body_mismatch(env, mutation):
    home, execute = env
    execute()
    with sqlite3.connect(home/'orchestration.sqlite') as con:
        con.execute('UPDATE runs SET result=NULL WHERE id=?', ['run1'])
        key, raw = con.execute(
            "SELECT key,body FROM outbox WHERE key LIKE '%:CANDIDATE'"
        ).fetchone()
        body = json.loads(raw)
        mutation(body)
        con.execute('UPDATE outbox SET body=? WHERE key=?',
                    [json.dumps(body, ensure_ascii=False), key])
    before = durable_counts(home)
    with pytest.raises(RunError):
        execute()
    assert durable_counts(home) == before


def test_unmodified_completed_run_rereads_and_distinct_run_keeps_daily_slots(env):
    home, execute = env
    first = execute()
    before = durable_counts(home)
    assert execute() == first
    assert durable_counts(home) == before
    second = execute(run_id='run2', item=proposal('buy2', code='7203'))
    assert second['candidates'][0]['status'] == 'APPROVED'
    after_second = durable_counts(home)
    third = execute(run_id='run3', item=proposal('buy3', code='6758'))
    assert 'DAILY_NEW_LIMIT' in third['candidates'][0]['gate']['reason_codes']
    assert durable_counts(home)[2] == after_second[2]
