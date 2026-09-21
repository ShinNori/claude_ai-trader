"""Synthetic workflows fail closed on source or generation uncertainty."""
import json
import socket
import subprocess
from contextlib import closing

import pytest

from aitrader.db import init, connect
import aitrader.daily_rehearsal
import aitrader.synthetic_pipeline
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier


def install_minimal_market(home, mode='synthetic'):
    from aitrader.daily_rehearsal import AS_OF, NORMAL_DAY, HOLIDAY_DAY
    init(home)
    with connect(home) as database:
        database.execute('INSERT INTO listed VALUES (?,?,?,?,?)',
                         ['6857', 'Fixture', 'prime', '10', '2020-01-06'])
        database.execute('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)',
                         ['6857', AS_OF, 1000.])
        database.executemany('INSERT INTO calendar VALUES (?,?)',
                             [(AS_OF, True), (NORMAL_DAY, True), (HOLIDAY_DAY, False)])
        if mode is not None:
            database.execute("INSERT INTO provenance VALUES ('data_mode',?)", [mode])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pipeline attempted external I/O or delivery')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(Notifier, 'flush', forbidden)


def reserved(home):
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        return ledger.reserved()


@pytest.mark.parametrize('mode', ['jquants', 'mystery', None])
def test_synthetic_pipeline_rejects_wrong_or_unknown_market_source_before_generation(
        tmp_path, monkeypatch, mode):
    import aitrader.synthetic_pipeline as pipeline
    home = tmp_path/f'pipeline-{mode}'
    monkeypatch.setattr(pipeline, 'load_synthetic',
                        lambda target, seed=42: install_minimal_market(target, mode))
    monkeypatch.setattr(pipeline, 'generate',
                        lambda *a, **k: pytest.fail('generation reached with uncertain source'))
    with pytest.raises((ValueError, pipeline.RunError)):
        pipeline.run_synthetic_pipeline(home, seed=42)
    assert not (home/'generated_proposals.json').exists()
    assert not (home/'synthetic_pipeline_summary.json').exists()
    assert not (home/'notification.sqlite').exists()
    assert not (home/'orchestration.sqlite').exists()
    assert reserved(home) == 0


@pytest.mark.parametrize('mode', ['jquants', 'mystery', None])
def test_daily_rehearsal_rejects_wrong_or_unknown_source_without_no_signal(
        tmp_path, monkeypatch, mode):
    import aitrader.daily_rehearsal as daily
    home = tmp_path/f'daily-{mode}'
    monkeypatch.setattr(daily, 'load_synthetic',
                        lambda target, seed=42: install_minimal_market(target, mode))
    monkeypatch.setattr(daily, 'generate',
                        lambda *a, **k: pytest.fail('generation reached with uncertain source'))
    with pytest.raises((ValueError, daily.RunError)):
        daily.run_daily_rehearsal(home)
    progress = json.loads((home/'daily_progress.json').read_text(encoding='utf-8'))
    assert progress['status'] == 'FAILED'
    assert progress['current_stage'] == progress['failed_stage'] == 'acquire_validate'
    assert progress['error_type'] != 'NO_SIGNAL'
    assert 'result_status' not in progress
    assert not (home/'daily_rehearsal.json').exists()
    assert not (home/'notification.sqlite').exists()
    assert not (home/'orchestration.sqlite').exists()
    assert reserved(home) == 0


@pytest.mark.parametrize('workflow', ['pipeline', 'daily'])
def test_generation_exception_is_not_converted_to_no_signal_or_queue_work(
        tmp_path, monkeypatch, workflow):
    secret = 'generation-source-detail'
    if workflow == 'pipeline':
        import aitrader.synthetic_pipeline as module
        home = tmp_path/'pipeline-generate-failure'
        invoke = lambda: module.run_synthetic_pipeline(home, seed=42)
    else:
        import aitrader.daily_rehearsal as module
        home = tmp_path/'daily-generate-failure'
        invoke = lambda: module.run_daily_rehearsal(home)
    monkeypatch.setattr(module, 'load_synthetic',
                        lambda target, seed=42: install_minimal_market(target, 'synthetic'))
    monkeypatch.setattr(module, 'generate',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(secret)))
    monkeypatch.setattr(module, 'run_reviewed_mock',
                        lambda *a, **k: pytest.fail('review ran after generation failure'))
    monkeypatch.setattr(module, 'prepare_notifications',
                        lambda *a, **k: pytest.fail('prepare ran after generation failure'))
    monkeypatch.setattr(module, 'enqueue_prepared_notifications',
                        lambda *a, **k: pytest.fail('enqueue ran after generation failure'))
    with pytest.raises(RuntimeError, match=secret):
        invoke()
    assert not (home/'notification.sqlite').exists()
    assert not (home/'orchestration.sqlite').exists()
    assert reserved(home) == 0
    summaries = (home/'synthetic_pipeline_summary.json', home/'daily_rehearsal.json')
    assert not any(path.exists() for path in summaries)
    if workflow == 'daily':
        progress = json.loads((home/'daily_progress.json').read_text(encoding='utf-8'))
        assert progress['status'] == 'FAILED'
        assert progress['current_stage'] == progress['failed_stage'] == 'generate'
        assert progress['error_type'] == 'RuntimeError'
        assert 'result_status' not in progress
        assert secret not in (home/'daily_progress.json').read_text(encoding='utf-8')
