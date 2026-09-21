"""Run-specific mock summaries never expose rejected order conditions."""
import json
from contextlib import closing

import pytest

from test_runner import setup, DAY, NOW, proposal, mock_verdicts, Ledger, RunError
from test_notification_plan import offline, SETTINGS, CONTEXTS
from aitrader.notification_plan import prepare_notifications
from aitrader.db import connect


@pytest.fixture(autouse=True)
def status_reference_price(setup):
    home, _ = setup
    with connect(home) as con:
        con.execute('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)',
                    ['9999', proposal().as_of, 1200.])


def prepare(home, *, include_status=True, contexts=None):
    return prepare_notifications(home, 'run1', DAY, contexts=contexts or {},
                                 settings=SETTINGS, include_status=include_status)


def execute_status(execute, status, partial=False):
    if status == 'NO_SIGNAL':
        return execute([])
    ps = [proposal('hidden', code='9999', limit_price=1234.)]
    if partial:
        ps.insert(0, proposal())
    votes = mock_verdicts(ps, 'run1', NOW)
    if status == 'REVIEW_INCOMPLETE':
        votes['hidden'].pop()
    elif status == 'NOT_APPROVED':
        from dataclasses import replace
        votes['hidden'][0] = replace(votes['hidden'][0], decision='REJECT')
    elif status == 'REVIEW_INVALID':
        from dataclasses import replace
        votes['hidden'][0] = replace(votes['hidden'][0], decision='INVALID')
    return execute(ps, votes=votes)


@pytest.mark.parametrize('status', ['NO_SIGNAL', 'REVIEW_INCOMPLETE', 'NOT_APPROVED', 'REVIEW_INVALID'])
def test_fixed_run_status_without_order_details(setup, status):
    home, execute = setup
    assert execute_status(execute, status)['status'] == status
    plan = prepare(home)
    assert plan['mode'] == 'mock' and plan['delivery'] == 'NOT_SENT'
    assert len(plan['plans']) == 1
    card = plan['plans'][0]
    assert card['key'] == f'{DAY}:run1:STATUS'
    assert card['kind'] == 'RECONCILE' and card['proposal_id'] is None
    assert card['source_status'] == status and card['plan_type'] == 'RUN_STATUS'
    assert card['message']['type'] == 'text' and card['message']['status'] is None
    text = card['message']['text']
    assert '模擬・未送信' in text and '日次全体の結果ではありません' in text
    assert not any(value in text for value in ('9999', '1234', '1,234', 'hidden'))
    assert prepare(home) == plan
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        assert ledger.reserved() == 0
    assert not (home/'notification.sqlite').exists()


@pytest.mark.parametrize('status', ['REVIEW_INCOMPLETE', 'REVIEW_INVALID'])
def test_partial_approval_has_candidate_and_separate_status(setup, status):
    home, execute = setup
    assert execute_status(execute, status, partial=True)['status'] == status
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        before = ledger.seq(), ledger.reserved()
    plan = prepare(home, contexts=CONTEXTS)
    assert [p['kind'] for p in plan['plans']] == ['NEW', 'RECONCILE']
    assert plan['plans'][0]['proposal_id'] == 'buy1'
    assert plan['plans'][1]['source_status'] == status
    assert '9999' not in json.dumps(plan['plans'], ensure_ascii=False)
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        assert (ledger.seq(), ledger.reserved()) == before
        assert ledger.notice('buy1')['notice_state'] == 'APPROVED'


def test_candidates_only_has_no_redundant_status(setup):
    home, execute = setup
    execute()
    plan = prepare(home, contexts=CONTEXTS)
    assert [p['kind'] for p in plan['plans']] == ['NEW']


def test_default_remains_candidates_only(setup):
    home, execute = setup
    execute([])
    plan = prepare_notifications(home, 'run1', DAY, contexts={}, settings=SETTINGS)
    assert plan['plans'] == [] and plan['delivery'] == 'NOT_SENT'


@pytest.mark.parametrize('first', [True, False])
def test_option_change_cannot_overwrite_fixed_plan(setup, first):
    home, execute = setup
    execute([])
    original = prepare(home, include_status=first)
    with pytest.raises(RunError, match='入力変更'):
        prepare(home, include_status=not first)
    path = home/'runs'/str(DAY)/'run1'/'notification_plan.json'
    assert json.loads(path.read_text(encoding='utf-8')) == original


@pytest.mark.parametrize('invalid', [1, 'true', None, []])
def test_status_option_requires_boolean(setup, invalid):
    home, execute = setup
    execute([])
    with pytest.raises(RunError, match='bool'):
        prepare(home, include_status=invalid)
