"""Failure checkpoints for the offline daily rehearsal."""
import json
import shutil
import socket
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from aitrader.api import load_synthetic
from aitrader.runner import RunError
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier


@pytest.fixture(scope='module')
def cached_market(tmp_path_factory):
    """Build the common seed once; each rehearsal still uses its real generator."""
    source = tmp_path_factory.mktemp('daily-progress-market')
    load_synthetic(source, seed=42)
    return source/'market.duckdb'


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('failure rehearsal attempted external I/O or delivery')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(Notifier, 'flush', forbidden)


def _cached_loader(monkeypatch, market):
    from aitrader import daily_rehearsal

    monkeypatch.setattr(daily_rehearsal, 'load_synthetic',
                        lambda home, seed=42: shutil.copy2(market, home/'market.duckdb'))
    return daily_rehearsal


def _read(home, name='daily_progress.json'):
    return json.loads((home/name).read_text(encoding='utf-8'))


def _reserved(home):
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        return ledger.reserved()


def _assert_failed(home, stage, error_type):
    progress = _read(home)
    assert progress['status'] == 'FAILED'
    assert progress['current_stage'] == stage == progress['failed_stage']
    assert progress['error_type'] == error_type
    assert progress['partial_state_may_exist'] is True
    assert progress['auto_resume'] is False
    assert 'error' not in progress
    assert not (home/'daily_rehearsal.json').exists()
    return progress


def test_acquisition_failure_records_stage_without_error_text(tmp_path, monkeypatch):
    from aitrader import daily_rehearsal

    secret = 'must-not-be-persisted'
    monkeypatch.setattr(daily_rehearsal, 'load_synthetic',
                        lambda *a, **k: (_ for _ in ()).throw(LookupError(secret)))
    home = tmp_path/'acquisition-failure'
    with pytest.raises(LookupError, match=secret):
        daily_rehearsal.run_daily_rehearsal(home)
    progress = _assert_failed(home, 'acquire_validate', 'LookupError')
    assert progress['checkpoint_seq'] == 3
    assert secret not in (home/'daily_progress.json').read_text(encoding='utf-8')


def test_generation_failure_is_a_later_monotonic_checkpoint(tmp_path, monkeypatch,
                                                            cached_market):
    daily = _cached_loader(monkeypatch, cached_market)
    monkeypatch.setattr(daily, 'generate',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('generate detail')))
    home = tmp_path/'generation-failure'
    with pytest.raises(RuntimeError, match='generate detail'):
        daily.run_daily_rehearsal(home)
    progress = _assert_failed(home, 'generate', 'RuntimeError')
    assert progress['checkpoint_seq'] == 4


@pytest.mark.parametrize('stage,function_name', [
    ('prepare', 'prepare_notifications'),
    ('enqueue', 'enqueue_prepared_notifications'),
])
def test_post_review_failure_keeps_existing_reservations(tmp_path, monkeypatch,
                                                         cached_market, stage,
                                                         function_name):
    daily = _cached_loader(monkeypatch, cached_market)
    monkeypatch.setattr(daily, function_name,
                        lambda *a, **k: (_ for _ in ()).throw(ArithmeticError(stage)))
    home = tmp_path/f'{stage}-failure'
    with pytest.raises(ArithmeticError, match=stage):
        daily.run_daily_rehearsal(home)
    _assert_failed(home, stage, 'ArithmeticError')
    assert _reserved(home) > 0


def test_final_summary_failure_is_at_finalize_and_keeps_reservations(
        tmp_path, monkeypatch, cached_market):
    daily = _cached_loader(monkeypatch, cached_market)
    original = daily.write_json

    def fail_summary(path, value):
        if path.name == 'daily_rehearsal.json':
            raise OSError('summary detail')
        return original(path, value)

    monkeypatch.setattr(daily, 'write_json', fail_summary)
    home = tmp_path/'summary-failure'
    with pytest.raises(OSError, match='summary detail'):
        daily.run_daily_rehearsal(home)
    _assert_failed(home, 'finalize', 'OSError')
    assert _reserved(home) > 0


def test_failed_checkpoint_write_preserves_original_exception(tmp_path, monkeypatch):
    from aitrader import daily_rehearsal

    original = daily_rehearsal.write_json
    monkeypatch.setattr(daily_rehearsal, 'load_synthetic',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('original failure')))

    def fail_only_failure_record(path, value):
        if path.name == 'daily_progress.json' and value.get('status') == 'FAILED':
            raise OSError('secondary failure')
        return original(path, value)

    monkeypatch.setattr(daily_rehearsal, 'write_json', fail_only_failure_record)
    home = tmp_path/'progress-write-failure'
    with pytest.raises(RuntimeError, match='original failure'):
        daily_rehearsal.run_daily_rehearsal(home)
    progress = _read(home)
    assert progress['status'] == 'RUNNING'
    assert progress['current_stage'] == 'acquire_validate'
    assert 'result_status' not in progress


def test_keyboard_interrupt_leaves_running_checkpoint(tmp_path, monkeypatch,
                                                      cached_market):
    daily = _cached_loader(monkeypatch, cached_market)
    monkeypatch.setattr(daily, 'generate',
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    home = tmp_path/'interrupted'
    with pytest.raises(KeyboardInterrupt):
        daily.run_daily_rehearsal(home)
    progress = _read(home)
    assert progress['status'] == 'RUNNING'
    assert progress['current_stage'] == 'generate'
    assert 'result_status' not in progress
    assert not (home/'daily_rehearsal.json').exists()


@pytest.mark.parametrize('scenario,expected', [
    ('normal', 'CANDIDATES'),
    ('data_missing', 'DATA_INCOMPLETE'),
    ('holiday', 'NO_SESSION'),
])
def test_successful_terminal_progress_matches_summary_and_is_monotonic(
        tmp_path, monkeypatch, cached_market, scenario, expected):
    daily = _cached_loader(monkeypatch, cached_market)
    original = daily.write_json
    observed = []

    def capture(path, value):
        if path.name == 'daily_progress.json':
            observed.append((value['checkpoint_seq'], value['status'],
                             value['current_stage']))
        return original(path, value)

    monkeypatch.setattr(daily, 'write_json', capture)
    home = tmp_path/f'success-{scenario}'
    summary = daily.run_daily_rehearsal(home, scenario=scenario)
    progress = _read(home)
    assert summary['status'] == expected
    assert progress['status'] == 'COMPLETED'
    assert progress['result_status'] == summary['status']
    assert progress['current_stage'] == 'finalize'
    assert 'partial_state_may_exist' not in progress
    assert 'auto_resume' not in progress
    sequence = [item[0] for item in observed]
    assert sequence == list(range(1, len(sequence) + 1))
    assert observed[-1][1:] == ('COMPLETED', 'finalize')


def test_existing_home_and_dropbox_targets_are_rejected_without_writes(tmp_path):
    from aitrader.daily_rehearsal import run_daily_rehearsal

    existing = tmp_path/'existing'
    existing.mkdir()
    marker = existing/'keep.txt'
    marker.write_text('preserve', encoding='utf-8')
    with pytest.raises(RunError):
        run_daily_rehearsal(existing)
    assert list(existing.iterdir()) == [marker]

    dropbox_target = tmp_path/'Dropbox'/'forbidden'
    assert not dropbox_target.exists()
    with pytest.raises(RunError, match='Dropbox'):
        run_daily_rehearsal(dropbox_target)
    assert not dropbox_target.exists()


def test_racing_home_creator_wins_without_rehearsal_side_effects(tmp_path, monkeypatch):
    from aitrader.daily_rehearsal import run_daily_rehearsal

    target = (tmp_path/'mkdir-race').resolve()
    sentinel = target/'winner.txt'
    original_mkdir = Path.mkdir
    raced = False

    def race_mkdir(self, mode=0o777, parents=False, exist_ok=False):
        nonlocal raced
        if self.resolve() == target and not raced:
            raced = True
            original_mkdir(self, mode=mode, parents=parents, exist_ok=False)
            sentinel.write_text('preserve winner', encoding='utf-8')
        return original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, 'mkdir', race_mkdir)
    with pytest.raises(RunError):
        run_daily_rehearsal(target)
    assert raced is True
    assert list(target.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding='utf-8') == 'preserve winner'
