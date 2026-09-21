"""Offline adversarial checks for the recorded-review adapter."""
from copy import deepcopy
from datetime import timedelta, timezone
import socket
import subprocess

import pytest

from test_runner import setup, NOW, DAY, proposal
from test_review_runner import inputs, call
from test_judges import record
from aitrader.review_runner import run_reviewed_mock
from aitrader.runner import RunError, Valuation
from aitrader_ops.ledger import Ledger
import aitrader_ops.judges as judges


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Network and external processes are forbidden')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)


def balance(home):
    ledger = Ledger(home / 'ledger.sqlite')
    try:
        return ledger.seq(), ledger.reserved()
    finally:
        ledger.close()


@pytest.mark.parametrize('change', [
    'unknown_proposal', 'responses_list', 'records_list', 'extra_field',
    'missing_record', 'missing_time', 'before_start', 'naive_time',
])
def test_invalid_envelope_has_no_side_effect(setup, change):
    home, _ = setup
    p, responses = inputs()
    before = balance(home)
    if change == 'unknown_proposal':
        responses['not-a-candidate'] = {}
    elif change == 'responses_list':
        responses = []
    elif change == 'records_list':
        responses[p.proposal_id] = []
    else:
        envelope = responses[p.proposal_id]['codex']
        if change == 'extra_field': envelope['approved'] = True
        elif change == 'missing_record': envelope.pop('record')
        elif change == 'missing_time': envelope.pop('received_at')
        elif change == 'before_start': envelope['received_at'] = NOW-timedelta(seconds=2)
        elif change == 'naive_time': envelope['received_at'] = NOW.replace(tzinfo=None)
    with pytest.raises(RunError):
        call(home, p, responses)
    assert balance(home) == before
    assert not (home / 'runs').exists()


def test_later_candidate_invalidates_before_first_review_logs(setup):
    home, _ = setup
    p, responses = inputs()
    other = proposal('second', code='7203')
    responses[other.proposal_id] = {'codex': {'record': {}, 'received_at': NOW+timedelta(seconds=1)}}
    before = balance(home)
    with pytest.raises(RunError):
        run_reviewed_mock(home, 'edges', DAY, [p, other], responses, NOW,
            Valuation(1000000, 1000000), started_at=NOW-timedelta(seconds=1), market_context={})
    assert not (home / 'runs').exists()
    assert balance(home) == before


@pytest.mark.parametrize('change', ['duplicate', 'start_future', 'start_yesterday', 'now_tomorrow'])
def test_session_boundaries_reject_before_logging(setup, change):
    home, _ = setup
    p, responses = inputs()
    ps, start, now = [p], NOW-timedelta(seconds=1), NOW
    if change == 'duplicate': ps.append(p)
    elif change == 'start_future': start = NOW+timedelta(seconds=1)
    elif change == 'start_yesterday': start -= timedelta(days=1)
    else: now += timedelta(days=1)
    before = balance(home)
    with pytest.raises(RunError):
        run_reviewed_mock(home, 'edges', DAY, ps, responses, now,
            Valuation(1000000, 1000000), started_at=start, market_context={})
    assert not (home / 'runs').exists()
    assert balance(home) == before


@pytest.mark.parametrize('position', [1, 2])
def test_review_log_failure_never_reaches_reservation(setup, monkeypatch, position):
    home, _ = setup
    p, responses = inputs()
    before = balance(home)
    original = judges._log
    calls = []
    def failing_log(*args, **kwargs):
        calls.append(1)
        if len(calls) == position:
            raise OSError('simulated full disk')
        return original(*args, **kwargs)
    monkeypatch.setattr(judges, '_log', failing_log)
    with pytest.raises(OSError, match='full disk'):
        call(home, p, responses)
    assert balance(home) == before
    assert not (home / 'orchestration.sqlite').exists()


def test_inputs_preserved_and_equivalent_timezone_accepted(setup):
    home, _ = setup
    p, responses = inputs()
    for envelope in responses[p.proposal_id].values():
        envelope['received_at'] = NOW.astimezone(timezone.utc)
    snapshot = deepcopy(responses)
    assert call(home, p, responses)['candidates'][0]['status'] == 'APPROVED'
    assert responses == snapshot


def test_changed_decision_same_run_cannot_change_saved_result(setup):
    home, _ = setup
    p, responses = inputs()
    first = call(home, p, responses)
    before = balance(home)
    _, rejected = inputs('REJECT')
    with pytest.raises(RunError, match='入力変更'):
        call(home, p, rejected)
    assert balance(home) == before
    assert call(home, p, responses) == first


@pytest.mark.parametrize('change', ['context', 'started_at'])
def test_recorded_session_identity_is_immutable(setup, change):
    home, _ = setup
    p, responses = inputs()
    options = dict(started_at=NOW-timedelta(seconds=1), market_context={})
    def execute():
        return run_reviewed_mock(home, 'edges', DAY, [p], responses, NOW,
            Valuation(1000000, 1000000), **options)
    execute()
    before = balance(home)
    if change == 'context': options['market_context'] = {'news': 'changed context'}
    else: options['started_at'] -= timedelta(seconds=1)
    with pytest.raises(RunError, match='入力変更'):
        execute()
    assert balance(home) == before


def test_before_review_window_cannot_approve(setup):
    home, _ = setup
    p, responses = inputs()
    start = NOW.replace(hour=6, minute=59)
    for envelope in responses[p.proposal_id].values():
        envelope['received_at'] = start+timedelta(seconds=30)
    before = balance(home)
    # An invalid session may reject as an input error or produce a withheld result.
    try:
        result = run_reviewed_mock(home, 'edges', DAY, [p], responses, NOW,
            Valuation(1000000, 1000000), started_at=start, market_context={})
    except RunError:
        pass
    else:
        assert all(item['status'] != 'APPROVED' for item in result['candidates'])
    assert balance(home)[1] == before[1]


def test_record_secret_is_masked_in_all_runner_artifacts(setup, monkeypatch):
    home, _ = setup
    p, responses = inputs()
    secret = 'edge-fixture-secret-never-real-9988'
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', secret)
    for judge, envelope in responses[p.proposal_id].items():
        answer = dict(proposal_id=p.proposal_id, packet_hash=p.packet_hash,
            decision='APPROVE', risks=[], reason='prefix '+secret+' suffix', confidence=None)
        envelope['record'] = record(judge, answer)
    call(home, p, responses)
    artifacts = list((home / 'runs').rglob('*.json')) + list((home / 'runs').rglob('*.jsonl'))
    assert artifacts
    for artifact in artifacts:
        assert secret not in artifact.read_text(encoding='utf-8'), artifact.name
