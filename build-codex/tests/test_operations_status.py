"""Independent bounded, read-only checks for the operations status card."""
import hashlib
import json
import shutil
import socket
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aitrader.api import load_synthetic
from aitrader.managed_stop import apply_managed_control
from aitrader.operations_status import build_operations_status
from aitrader_ops.models import JST
from aitrader_ops.notify import Notifier


NOW = datetime(2026, 8, 31, 7, 10, tzinfo=JST)
SETTINGS = {
    'broker': {'link_template': 'https://www.rakuten-sec.co.jp/web/market/search/{code}'},
    'line': {'allowed_user_id': 'synthetic-pipeline-recipient', 'monthly_budget': 100},
}


@pytest.fixture(scope='module')
def new_history(tmp_path_factory):
    from aitrader.daily_rehearsal import run_daily_rehearsal

    source = tmp_path_factory.mktemp('operations-status-market')
    load_synthetic(source, seed=42)
    market = source/'market.duckdb'
    root = tmp_path_factory.mktemp('operations-status-homes')
    sequence = 0

    def create(label):
        nonlocal sequence
        sequence += 1
        home = root/f'{sequence}-{label}'
        def forbidden(*args, **kwargs):
            pytest.fail('operations fixture attempted external I/O or delivery')
        with pytest.MonkeyPatch.context() as patch:
            from aitrader import daily_rehearsal
            patch.setattr(daily_rehearsal, 'load_synthetic',
                          lambda target, seed=42: shutil.copy2(
                              market, target/'market.duckdb'))
            patch.setattr(subprocess, 'Popen', forbidden)
            patch.setattr(socket, 'create_connection', forbidden)
            patch.setattr(socket.socket, 'connect', forbidden)
            patch.setattr(Notifier, 'flush', forbidden)
            summary = run_daily_rehearsal(home, managed_stop=True)
        assert summary['managed_stop'] is True
        return home
    return create


@pytest.fixture(scope='module')
def managed_history(new_history):
    return new_history('base')


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('operations status attempted external I/O or delivery')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(Notifier, 'flush', forbidden)


def _hashes(home):
    return {p.relative_to(home).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in home.rglob('*') if p.is_file()}


def _assert_fixed(result):
    assert result['read_only'] is True
    assert result['current_signal'] is False
    assert result['auto_resume'] is False
    assert result['real_sent'] is False
    assert result['observed_at'] == NOW.isoformat()
    assert result['observed_at_source'] == 'explicit_input'


def _all_keys(value):
    if isinstance(value, dict):
        return set(value).union(*(map(_all_keys, value.values())))
    if isinstance(value, list):
        return set().union(*(map(_all_keys, value))) if value else set()
    return set()


def test_verified_history_and_clear_stop_are_bounded_and_read_only(managed_history):
    before = _hashes(managed_history)
    first = build_operations_status(managed_history, now=NOW)
    second = build_operations_status(managed_history, now=NOW)
    assert first == second
    _assert_fixed(first)
    assert first['status'] == 'CLEAR'
    assert first['history']['status'] == 'VERIFIED_HISTORY'
    assert first['history']['summary']['managed_stop'] is True
    assert first['progress']['status'] == 'COMPLETED'
    assert first['stop']['status'] == 'CLEAR'
    assert first['stop']['managed'] is True
    assert _hashes(managed_history) == before
    keys = _all_keys(first)
    assert not {'code', 'limit_price', 'stop_price', 'price', 'proposals',
                'candidates', 'queue', 'plan'}.intersection(keys)
    text = json.dumps(first, ensure_ascii=False)
    assert '6857' not in text


def test_stop_after_seal_is_current_even_when_history_no_longer_matches(
        new_history):
    home = new_history('stopped')
    apply_managed_control(home, action='STOP', now=NOW, settings=SETTINGS)
    result = build_operations_status(home, now=NOW)
    _assert_fixed(result)
    assert result['status'] == 'STOPPED'
    assert result['stop']['status'] == 'STOPPED'
    assert result['stop']['effective_stop'] is True
    assert result['history']['status'] == 'NEEDS_RECONCILIATION'
    assert 'summary' not in result['history']


@pytest.mark.parametrize('status', ['RUNNING', 'FAILED'])
def test_valid_nonterminal_progress_is_displayed_without_error_text(
        new_history, status):
    home = new_history(status.lower())
    path = home/'daily_progress.json'
    progress = json.loads(path.read_text(encoding='utf-8'))
    progress['status'] = status
    progress.pop('result_status', None)
    if status == 'FAILED':
        progress.update(failed_stage='review', error_type='SyntheticFailure',
                        partial_state_may_exist=True, auto_resume=False,
                        error='secret detail must not appear')
    path.write_text(json.dumps(progress), encoding='utf-8')
    result = build_operations_status(home, now=NOW)
    assert result['status'] == 'NEEDS_RECONCILIATION'
    assert result['progress']['status'] == status
    assert result['progress']['known'] is True
    if status == 'FAILED':
        assert result['progress']['failed_stage'] == 'review'
        assert result['progress']['error_type'] == 'SyntheticFailure'
    assert 'secret detail' not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize('missing,reason', [
    ('managed-stop-clock.json', 'MANAGED_POLICY_INVALID'),
    ('notification.sqlite', 'STATE_DB_MISSING'),
])
def test_missing_stop_state_is_unknown_and_not_recreated(
        new_history, missing, reason):
    home = new_history('missing-' + missing.replace('.', '-'))
    target = home/missing
    target.unlink()
    before = _hashes(home)
    result = build_operations_status(home, now=NOW)
    _assert_fixed(result)
    assert result['status'] == 'UNKNOWN'
    assert result['stop']['known'] is False
    assert result['stop']['effective_stop'] is True
    assert reason in result['stop']['reasons']
    assert not target.exists()
    assert _hashes(home) == before


@pytest.mark.parametrize('damage', ['link', 'invalid-json'])
def test_unsafe_or_invalid_progress_is_unknown(new_history,
                                               monkeypatch, damage):
    from aitrader import operations_status

    home = new_history('progress-' + damage)
    progress = home/'daily_progress.json'
    if damage == 'invalid-json':
        progress.write_text('{invalid', encoding='utf-8')
    else:
        original = operations_status._is_link
        monkeypatch.setattr(operations_status, '_is_link',
                            lambda path: path == progress or original(path))
    result = build_operations_status(home, now=NOW)
    assert result['status'] == 'UNKNOWN'
    assert result['progress']['known'] is False
    assert 'summary' not in result['history'] or not {
        'code', 'price', 'candidates'}.intersection(_all_keys(result['history']))


def test_missing_home_is_not_created(tmp_path):
    home = tmp_path/'missing-home'
    result = build_operations_status(home, now=NOW)
    _assert_fixed(result)
    assert result['status'] == 'UNKNOWN'
    assert result['progress']['known'] is False
    assert result['stop']['effective_stop'] is True
    assert 'summary' not in result['history']
    assert not home.exists()


def test_history_tamper_hides_saved_summary(new_history):
    home = new_history('history-tamper')
    path = home/'daily_rehearsal.json'
    path.write_bytes(path.read_bytes() + b'changed')
    result = build_operations_status(home, now=NOW)
    assert result['status'] == 'NEEDS_RECONCILIATION'
    assert result['history']['status'] == 'NEEDS_RECONCILIATION'
    assert 'summary' not in result['history']


@pytest.mark.parametrize('field,bad_value', [
    ('status', ['COMPLETED']),
    ('current_stage', {'name': 'finalize'}),
    ('failed_stage', ['review']),
])
def test_progress_discriminator_types_cannot_be_confused(
        new_history, field, bad_value):
    home = new_history('bad-progress-' + field)
    path = home/'daily_progress.json'
    progress = json.loads(path.read_text(encoding='utf-8'))
    if field == 'failed_stage':
        progress.update(status='FAILED', failed_stage=bad_value,
                        error_type='SyntheticFailure', partial_state_may_exist=True,
                        auto_resume=False)
        progress.pop('result_status', None)
    else:
        progress[field] = bad_value
    path.write_text(json.dumps(progress), encoding='utf-8')
    result = build_operations_status(home, now=NOW)
    assert result['status'] == 'UNKNOWN'
    assert result['progress']['known'] is False
    assert result['progress']['reasons'] == ['PROGRESS_INVALID']


@pytest.mark.parametrize('field,value', [
    ('partial_state_may_exist', False), ('auto_resume', True),
    ('partial_state_may_exist', None),
])
def test_failed_progress_requires_exact_safety_flags(new_history, field, value):
    home = new_history('failed-safety-' + field + '-' + str(value))
    path = home/'daily_progress.json'
    progress = json.loads(path.read_text(encoding='utf-8'))
    progress.update(status='FAILED', current_stage='review', failed_stage='review',
                    error_type='SyntheticFailure', partial_state_may_exist=True,
                    auto_resume=False)
    progress.pop('result_status', None)
    progress[field] = value
    path.write_text(json.dumps(progress), encoding='utf-8')
    result = build_operations_status(home, now=NOW)
    assert result['status'] == 'UNKNOWN'
    assert result['progress']['known'] is False


def test_completed_progress_requires_finalize_stage(new_history):
    home = new_history('completed-wrong-stage')
    path = home/'daily_progress.json'
    progress = json.loads(path.read_text(encoding='utf-8'))
    progress['current_stage'] = 'review'
    path.write_text(json.dumps(progress), encoding='utf-8')
    result = build_operations_status(home, now=NOW)
    assert result['status'] == 'UNKNOWN'
    assert result['progress']['reasons'] == ['PROGRESS_INVALID']


@pytest.mark.parametrize('scenario', [['normal'], {'name': 'normal'}])
def test_unverified_bad_scenario_never_leaks_summary(new_history, scenario):
    home = new_history('bad-summary-' + type(scenario).__name__)
    path = home/'daily_rehearsal.json'
    summary = json.loads(path.read_text(encoding='utf-8'))
    summary['scenario'] = scenario
    summary['code'] = '6857'
    summary['limit_price'] = 1234
    path.write_text(json.dumps(summary), encoding='utf-8')
    result = build_operations_status(home, now=NOW)
    assert result['history']['status'] == 'NEEDS_RECONCILIATION'
    assert 'summary' not in result['history']
    text = json.dumps(result, ensure_ascii=False)
    assert '6857' not in text and '1234' not in text


def test_progress_change_during_composition_is_detected(new_history, monkeypatch):
    from aitrader import operations_status

    home = new_history('composition-race')
    path = home/'daily_progress.json'
    original = operations_status.inspect_daily_rehearsal
    injected = False

    def change_after_history(target):
        nonlocal injected
        result = original(target)
        progress = json.loads(path.read_text(encoding='utf-8'))
        progress['checkpoint_seq'] += 1
        path.write_text(json.dumps(progress), encoding='utf-8')
        injected = True
        return result

    monkeypatch.setattr(operations_status, 'inspect_daily_rehearsal', change_after_history)
    result = build_operations_status(home, now=NOW)
    assert injected is True
    assert result['status'] == 'UNKNOWN'
    assert result['progress']['reasons'] == ['PROGRESS_CHANGED_DURING_COMPOSITION']
    assert result['history']['reason'] == 'PROGRESS_CHANGED_DURING_COMPOSITION'
    assert 'summary' not in result['history']


def test_dropbox_home_is_rejected_before_any_file_read(monkeypatch):
    target = Path(__file__).resolve().parents[2]
    reads = []

    def forbidden_read(path):
        reads.append(path)
        pytest.fail('Dropbox target was read')

    monkeypatch.setattr(Path, 'read_bytes', forbidden_read)
    result = build_operations_status(target, now=NOW)
    assert result['status'] == 'UNKNOWN'
    assert 'DROPBOX_HOME_REJECTED' in result['progress']['reasons']
    assert reads == []


def test_linked_parent_is_rejected_before_child_reads(tmp_path, monkeypatch):
    from aitrader import operations_status

    parent = tmp_path/'linked-parent'
    target = parent/'child'
    original_link = operations_status._is_link
    reads = []
    monkeypatch.setattr(operations_status, '_is_link',
                        lambda path: path == parent or original_link(path))
    monkeypatch.setattr(Path, 'read_bytes',
                        lambda path: reads.append(path) or pytest.fail('linked child was read'))
    result = build_operations_status(target, now=NOW)
    assert result['status'] == 'UNKNOWN'
    assert 'UNSAFE_HOME_LINK' in result['progress']['reasons']
    assert reads == []


def test_future_execution_day_hides_summary_against_explicit_observation(
        managed_history):
    observed = NOW - timedelta(days=1)
    result = build_operations_status(managed_history, now=observed)
    assert result['observed_at'] == observed.isoformat()
    assert result['observed_at_source'] == 'explicit_input'
    assert result['history'] == {
        'status': 'NEEDS_RECONCILIATION', 'reason': 'HISTORY_FROM_FUTURE'}
    assert 'summary' not in result['history']
