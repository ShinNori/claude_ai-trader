"""Independent, offline acceptance checks for immutable notification rendering."""
import copy
import json
import socket
import sqlite3
import subprocess
from contextlib import closing

import pytest

from test_runner import setup, DAY, NOW, Ledger, RunError
from aitrader.notification_plan import prepare_notifications


SETTINGS = {'broker': {'link_template': 'https://www.rakuten-sec.co.jp/web/market/search/{code}'}}
CONTEXTS = {'buy1': {'name': '模擬銘柄', 'account_confirmed_at': NOW.isoformat(),
                     'available_after': 899800, 'new_sent_today': 0, 'max_new_per_day': 2}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('notification preparation attempted external I/O')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def prepare(home, contexts=None, settings=None):
    return prepare_notifications(home, 'run1', DAY,
                                 contexts=copy.deepcopy(CONTEXTS if contexts is None else contexts),
                                 settings=copy.deepcopy(SETTINGS if settings is None else settings))


def folder(home):
    return home/'runs'/str(DAY)/'run1'


def snapshot(home):
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        return ledger.seq(), ledger.reserved(), ledger.notice('buy1')


def test_prepare_is_render_only_and_persisted(setup):
    home, execute = setup
    original = execute()
    before = snapshot(home)
    plan = prepare(home)
    assert plan['mode'] == 'mock' and plan['delivery'] == 'NOT_SENT'
    assert plan['run_id'] == 'run1'
    cards = [p for p in plan['plans'] if p['kind'] == 'NEW']
    assert len(cards) == 1 and cards[0]['proposal_id'] == 'buy1'
    assert cards[0]['message']['type'] == 'flex'
    assert '模擬銘柄' in json.dumps(cards[0]['message'], ensure_ascii=False)
    assert snapshot(home) == before
    assert before[2]['notice_state'] == 'APPROVED'
    assert not (home/'notification.sqlite').exists()
    assert json.loads((folder(home)/'result.json').read_text(encoding='utf-8')) == original
    assert json.loads((folder(home)/'notification_plan.json').read_text(encoding='utf-8')) == plan


def test_same_input_reuses_plan_without_new_reservations(setup):
    home, execute = setup
    execute()
    first = prepare(home)
    before = snapshot(home)
    assert prepare(home) == first
    assert snapshot(home) == before


@pytest.mark.parametrize('changed', ['name', 'available_after', 'settings'])
def test_changed_render_input_rejected(setup, changed):
    home, execute = setup
    execute()
    first = prepare(home)
    contexts, settings = copy.deepcopy(CONTEXTS), copy.deepcopy(SETTINGS)
    if changed == 'settings':
        settings['broker']['link_template'] += '?view=changed'
    else:
        contexts['buy1'][changed] = '別銘柄' if changed == 'name' else 1
    with pytest.raises((RunError, ValueError)):
        prepare(home, contexts, settings)
    assert json.loads((folder(home)/'notification_plan.json').read_text(encoding='utf-8')) == first


@pytest.mark.parametrize('artifact', ['verdicts', 'proposals', 'manifest', 'result'])
def test_missing_saved_input_rejected(setup, artifact):
    home, execute = setup
    execute()
    (folder(home)/f'{artifact}.json').unlink()
    with pytest.raises((RunError, ValueError)):
        prepare(home)
    assert not (folder(home)/'notification_plan.json').exists()


def test_missing_second_vote_rejected(setup):
    home, execute = setup
    execute()
    path = folder(home)/'verdicts.json'
    votes = json.loads(path.read_text(encoding='utf-8'))
    votes['buy1'].pop()
    path.write_text(json.dumps(votes), encoding='utf-8')
    with pytest.raises((RunError, ValueError)):
        prepare(home)


def test_changed_ledger_proposal_rejected(setup, monkeypatch):
    home, execute = setup
    execute()
    original = Ledger.proposal
    def altered(self, pid):
        p = original(self, pid)
        p['limit_price'] = 999
        return p
    monkeypatch.setattr(Ledger, 'proposal', altered)
    with pytest.raises((RunError, ValueError)):
        prepare(home)


def test_non_mock_home_rejected(setup):
    home, execute = setup
    execute()
    (home/'mock-runner.json').unlink()
    with pytest.raises((RunError, ValueError)):
        prepare(home)


def test_empty_candidates_do_not_prepare_candidate(setup):
    home, execute = setup
    execute([])
    plan = prepare(home, contexts={})
    assert plan['plans'] == []
    assert plan['delivery'] == 'NOT_SENT'


def test_incomplete_vote_has_no_candidate_card(setup):
    from test_runner import proposal, mock_verdicts
    home, execute = setup
    votes = mock_verdicts([proposal()], 'run1', NOW)
    votes['buy1'].pop()
    execute(votes=votes)
    plan = prepare(home)
    assert plan['plans'] == []
    assert plan['delivery'] == 'NOT_SENT'


def test_context_secret_never_saved(setup, monkeypatch):
    home, execute = setup
    execute()
    secret = 'notification-test-secret-9284736'
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', secret)
    contexts = copy.deepcopy(CONTEXTS)
    contexts['buy1']['name'] = secret
    plan = prepare(home, contexts=contexts)
    assert secret not in json.dumps(plan)
    assert secret not in (folder(home)/'notification_plan.json').read_text(encoding='utf-8')


def test_approved_candidate_requires_explicit_context(setup):
    home, execute = setup
    execute()
    with pytest.raises((RunError, ValueError)):
        prepare(home, contexts={})


def test_saved_plan_cannot_be_edited_and_reused(setup):
    home, execute = setup
    execute()
    prepare(home)
    path = folder(home)/'notification_plan.json'
    plan = json.loads(path.read_text(encoding='utf-8'))
    plan['plans'][0]['message']['altText'] = '改変済み'
    path.write_text(json.dumps(plan), encoding='utf-8')
    with pytest.raises((RunError, ValueError)):
        prepare(home)


def test_result_and_gate_simultaneous_edit_rejected(setup):
    home, execute = setup
    execute()
    result_path = folder(home)/'result.json'
    result = json.loads(result_path.read_text(encoding='utf-8'))
    result['candidates'][0]['gate']['reason_codes'].append('forged')
    result_path.write_text(json.dumps(result), encoding='utf-8')
    (folder(home)/'gate_results.json').write_text(json.dumps(result['candidates']), encoding='utf-8')
    with pytest.raises((RunError, ValueError)):
        prepare(home)


def test_vote_reason_edit_rejected(setup):
    home, execute = setup
    execute()
    path = folder(home)/'verdicts.json'
    votes = json.loads(path.read_text(encoding='utf-8'))
    votes['buy1'][0]['reason'] = '偽の承認理由'
    path.write_text(json.dumps(votes), encoding='utf-8')
    with pytest.raises((RunError, ValueError)):
        prepare(home)


def test_legacy_run_without_artifact_hashes_rejected(setup):
    home, execute = setup
    execute()
    path = folder(home)/'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest.pop('saved_artifact_hashes')
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with sqlite3.connect(home/'orchestration.sqlite') as con:
        con.execute('UPDATE runs SET manifest=? WHERE id=?', [json.dumps(manifest), 'run1'])
    with pytest.raises((RunError, ValueError)):
        prepare(home)
