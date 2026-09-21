"""Independent tamper and read-only checks for sealed daily history."""
import hashlib
import json
import shutil
import socket
import subprocess

import duckdb
import pytest

from aitrader.api import load_synthetic
from aitrader.runner import RunError
from aitrader_ops.notify import Notifier


def _cached_loader(monkeypatch, market):
    from aitrader import daily_rehearsal

    monkeypatch.setattr(daily_rehearsal, 'load_synthetic',
                        lambda home, seed=42: shutil.copy2(market, home/'market.duckdb'))
    return daily_rehearsal


def _tree_hashes(home):
    return {path.relative_to(home).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in home.rglob('*') if path.is_file()}


@pytest.fixture(scope='module')
def sealed_histories(tmp_path_factory):
    source = tmp_path_factory.mktemp('daily-receipt-market')
    load_synthetic(source, seed=42)
    market = source/'market.duckdb'
    root = tmp_path_factory.mktemp('daily-receipt-histories')
    histories = {}
    with pytest.MonkeyPatch.context() as patch:
        daily = _cached_loader(patch, market)

        def forbidden(*args, **kwargs):
            pytest.fail('daily creation attempted external I/O or delivery')

        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(socket, 'create_connection', forbidden)
        patch.setattr(socket.socket, 'connect', forbidden)
        patch.setattr(Notifier, 'flush', forbidden)
        for scenario in ('normal', 'data_missing', 'holiday'):
            home = root/scenario
            summary = daily.run_daily_rehearsal(home, scenario=scenario)
            histories[scenario] = (home, summary)
    return histories


def _inspect_read_only(monkeypatch, home):
    from aitrader.daily_receipt import inspect_daily_rehearsal

    before = _tree_hashes(home) if home.exists() else None

    def forbidden(*args, **kwargs):
        pytest.fail('history inspection attempted I/O beyond file reads')

    monkeypatch.setattr(duckdb, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(Notifier, 'flush', forbidden)
    first = inspect_daily_rehearsal(home)
    second = inspect_daily_rehearsal(home)
    after = _tree_hashes(home) if home.exists() else None
    assert first == second
    assert before == after
    assert first['auto_resume'] is False
    assert first['repaired'] is False
    assert first['current_signal'] is False
    return first


@pytest.mark.parametrize('scenario,expected', [
    ('normal', 'CANDIDATES'),
    ('data_missing', 'DATA_INCOMPLETE'),
    ('holiday', 'NO_SESSION'),
])
def test_sealed_terminal_histories_verify_twice_without_changes(
        sealed_histories, monkeypatch, scenario, expected):
    home, summary = sealed_histories[scenario]
    result = _inspect_read_only(monkeypatch, home)
    assert summary['status'] == expected
    assert result['status'] == 'VERIFIED_HISTORY'
    assert result['saved_summary'] == summary
    progress = json.loads((home/'daily_progress.json').read_text(encoding='utf-8'))
    receipt = json.loads((home/'daily_receipt.json').read_text(encoding='utf-8'))
    assert progress['status'] == 'COMPLETED'
    assert receipt['expected_completed_checkpoint_seq'] == progress['checkpoint_seq']
    indexed = [item['path'] for item in receipt['files']]
    assert indexed == sorted(indexed)
    assert 'daily_receipt.json' not in indexed
    assert 'daily_progress.json' not in indexed
    assert set(indexed) == set(_tree_hashes(home)) - {
        'daily_receipt.json', 'daily_progress.json'}
    for item in receipt['files']:
        body = (home/item['path']).read_bytes()
        assert item['size'] == len(body)
        assert item['sha256'] == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize('relative', [
    'ledger.sqlite', 'daily_rehearsal.json', 'generated_proposals.json',
    'notification.sqlite',
])
def test_changed_sealed_content_hides_summary(tmp_path, sealed_histories,
                                              monkeypatch, relative):
    source, _ = sealed_histories['normal']
    home = tmp_path/relative.replace('.', '-')
    shutil.copytree(source, home)
    target = home/relative
    assert target.exists()
    target.write_bytes(target.read_bytes() + b'changed')
    result = _inspect_read_only(monkeypatch, home)
    assert result['status'] == 'NEEDS_RECONCILIATION'
    assert result['reason'] == 'FILE_SET_OR_CONTENT_MISMATCH'
    assert 'saved_summary' not in result


@pytest.mark.parametrize('change', ['added', 'missing-indexed', 'missing-receipt'])
def test_file_set_changes_need_reconciliation(tmp_path, sealed_histories,
                                              monkeypatch, change):
    source, _ = sealed_histories['normal']
    home = tmp_path/change
    shutil.copytree(source, home)
    if change == 'added':
        (home/'unexpected.txt').write_text('extra', encoding='utf-8')
    elif change == 'missing-indexed':
        (home/'generated_proposals.json').unlink()
    else:
        (home/'daily_receipt.json').unlink()
    result = _inspect_read_only(monkeypatch, home)
    assert result['status'] == 'NEEDS_RECONCILIATION'
    assert 'saved_summary' not in result


@pytest.mark.parametrize('status,seq_delta', [
    ('FAILED', 0), ('RUNNING', 0), ('COMPLETED', 1),
])
def test_progress_state_or_sequence_change_hides_summary(
        tmp_path, sealed_histories, monkeypatch, status, seq_delta):
    source, _ = sealed_histories['normal']
    home = tmp_path/f'{status}-{seq_delta}'
    shutil.copytree(source, home)
    path = home/'daily_progress.json'
    progress = json.loads(path.read_text(encoding='utf-8'))
    progress['status'] = status
    progress['checkpoint_seq'] += seq_delta
    path.write_text(json.dumps(progress), encoding='utf-8')
    result = _inspect_read_only(monkeypatch, home)
    assert result['status'] == 'NEEDS_RECONCILIATION'
    assert result['reason'] == 'PROGRESS_NOT_COMPLETED'
    assert 'saved_summary' not in result


@pytest.mark.parametrize('name', ['market.duckdb.wal', 'notification.sqlite-shm',
                                  'orchestration.sqlite-wal', 'leftover.tmp'])
def test_temporary_database_files_are_unsafe(tmp_path, sealed_histories,
                                             monkeypatch, name):
    source, _ = sealed_histories['normal']
    home = tmp_path/name.replace('.', '-')
    shutil.copytree(source, home)
    (home/name).write_bytes(b'pending database state')
    result = _inspect_read_only(monkeypatch, home)
    assert result['status'] == 'NEEDS_RECONCILIATION'
    assert result['reason'] == 'UNREADABLE_OR_UNSAFE_HISTORY'
    assert 'saved_summary' not in result


def test_missing_home_is_not_created_or_opened(tmp_path, monkeypatch):
    home = tmp_path/'does-not-exist'
    result = _inspect_read_only(monkeypatch, home)
    assert result['status'] == 'NEEDS_RECONCILIATION'
    assert result['reason'] == 'HOME_NOT_FOUND'
    assert 'saved_summary' not in result
    assert not home.exists()


def test_seal_rejects_link_inside_history(tmp_path, sealed_histories, monkeypatch):
    from aitrader import daily_receipt

    source, _ = sealed_histories['normal']
    home = tmp_path/'linked'
    shutil.copytree(source, home)
    (home/'daily_receipt.json').unlink()
    link = home/'linked-summary.json'
    link.write_text('junction target stand-in', encoding='utf-8')
    monkeypatch.setattr(daily_receipt, '_is_link',
                        lambda path: path.name == link.name)
    progress = json.loads((home/'daily_progress.json').read_text(encoding='utf-8'))
    progress['status'] = 'RUNNING'
    progress['checkpoint_seq'] -= 1
    (home/'daily_progress.json').write_text(json.dumps(progress), encoding='utf-8')
    with pytest.raises(RunError) as caught:
        daily_receipt.seal_daily_rehearsal(
            home, expected_progress_seq=progress['checkpoint_seq'] + 1)
    assert 'リンク' in str(caught.value.__cause__) or 'junction' in str(caught.value.__cause__)
    assert not (home/'daily_receipt.json').exists()
