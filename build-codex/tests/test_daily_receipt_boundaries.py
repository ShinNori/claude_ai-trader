"""Adversarial boundaries for read-only daily history receipts."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from aitrader.daily_receipt import inspect_daily_rehearsal, seal_daily_rehearsal
from aitrader.runner import RunError


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _entry(path, body):
    return {'path': path, 'size': len(body),
            'sha256': hashlib.sha256(body).hexdigest()}


def sealed_history(tmp_path):
    home = tmp_path/'sealed'
    home.mkdir()
    summary = {'mode': 'mock', 'source': 'synthetic', 'virtual_clock': True,
               'real_sent': False, 'delivery': 'NOT_SENT', 'status': 'NO_SIGNAL'}
    progress = {'status': 'COMPLETED', 'current_stage': 'finalize',
                'checkpoint_seq': 4, 'mode': 'mock', 'source': 'synthetic',
                'virtual_clock': True, 'real_sent': False,
                'result_status': 'NO_SIGNAL'}
    summary_body = _bytes(summary)
    marker_body = _bytes({'mode': 'mock', 'version': 1})
    (home/'daily_rehearsal.json').write_bytes(summary_body)
    (home/'mock-runner.json').write_bytes(marker_body)
    (home/'daily_progress.json').write_bytes(_bytes(progress))
    receipt = {'version': 1, 'mode': 'mock',
               'expected_completed_checkpoint_seq': 4,
               'files': [_entry('daily_rehearsal.json', summary_body),
                         _entry('mock-runner.json', marker_body)]}
    (home/'daily_receipt.json').write_bytes(_bytes(receipt))
    return home, summary, receipt


def assert_pending(result):
    assert result['status'] == 'NEEDS_RECONCILIATION'
    assert result['auto_resume'] is False
    assert result['repaired'] is False
    assert result['current_signal'] is False
    assert 'saved_summary' not in result


def test_valid_minimal_sealed_history_is_read_only(tmp_path):
    home, summary, _ = sealed_history(tmp_path)
    before = {p.relative_to(home).as_posix(): p.read_bytes()
              for p in home.rglob('*') if p.is_file()}

    result = inspect_daily_rehearsal(home)

    assert result['status'] == 'VERIFIED_HISTORY'
    assert result['saved_summary'] == summary
    assert result['auto_resume'] is False and result['current_signal'] is False
    assert {p.relative_to(home).as_posix(): p.read_bytes()
            for p in home.rglob('*') if p.is_file()} == before


def test_real_normal_rehearsal_seals_and_verifies_one_history(tmp_path):
    from aitrader.daily_rehearsal import run_daily_rehearsal

    home = tmp_path/'real-normal'
    summary = run_daily_rehearsal(home, scenario='normal', seed=42)
    result = inspect_daily_rehearsal(home)

    assert result['status'] == 'VERIFIED_HISTORY'
    assert result['saved_summary'] == summary
    assert result['auto_resume'] is False
    assert result['repaired'] is False
    assert result['current_signal'] is False


@pytest.mark.parametrize('bad_path', [
    '../outside.json', '..\\outside.json', '/outside.json',
    'C:/outside.json', 'C:\\outside.json', 'dir:stream',
])
def test_receipt_paths_never_select_files_outside_snapshot(tmp_path, monkeypatch, bad_path):
    home, _, receipt = sealed_history(tmp_path)
    outside = tmp_path/'outside.json'
    outside.write_text('do not read', encoding='utf-8')
    receipt['files'] = [_entry(bad_path, outside.read_bytes())]
    (home/'daily_receipt.json').write_bytes(_bytes(receipt))
    original = Path.read_bytes
    reads = []

    def observed(path):
        reads.append(path.resolve())
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', observed)
    result = inspect_daily_rehearsal(home)

    assert_pending(result)
    assert outside.resolve() not in reads
    assert all(path.is_relative_to(home.resolve()) for path in reads)


def test_duplicate_receipt_entry_is_not_accepted(tmp_path):
    home, _, receipt = sealed_history(tmp_path)
    receipt['files'].append(dict(receipt['files'][0]))
    (home/'daily_receipt.json').write_bytes(_bytes(receipt))

    assert_pending(inspect_daily_rehearsal(home))


@pytest.mark.parametrize('field,value', [
    ('expected_completed_checkpoint_seq', True),
    ('version', True),
])
def test_bool_is_not_accepted_as_receipt_integer(tmp_path, field, value):
    home, _, receipt = sealed_history(tmp_path)
    receipt[field] = value
    (home/'daily_receipt.json').write_bytes(_bytes(receipt))

    result = inspect_daily_rehearsal(home)
    assert_pending(result)
    assert result['reason'] == 'INVALID_RECEIPT'


def test_bool_file_size_is_not_accepted_as_one_byte(tmp_path):
    home, _, receipt = sealed_history(tmp_path)
    (home/'daily_rehearsal.json').write_bytes(b'x')
    receipt['files'] = [{'path': 'daily_rehearsal.json', 'size': True,
                         'sha256': hashlib.sha256(b'x').hexdigest()}]
    (home/'daily_receipt.json').write_bytes(_bytes(receipt))

    result = inspect_daily_rehearsal(home)
    assert_pending(result)
    assert result['reason'] == 'INVALID_RECEIPT'


def test_bool_completed_progress_seq_is_not_accepted(tmp_path):
    home, _, receipt = sealed_history(tmp_path)
    receipt['expected_completed_checkpoint_seq'] = True
    progress = json.loads((home/'daily_progress.json').read_text(encoding='utf-8'))
    progress['checkpoint_seq'] = True
    (home/'daily_progress.json').write_bytes(_bytes(progress))
    (home/'daily_receipt.json').write_bytes(_bytes(receipt))

    assert_pending(inspect_daily_rehearsal(home))


@pytest.mark.parametrize('seq', [0, -1])
def test_nonpositive_completed_seq_is_rejected_even_when_progress_matches(tmp_path, seq):
    home, _, receipt = sealed_history(tmp_path)
    receipt['expected_completed_checkpoint_seq'] = seq
    progress = json.loads((home/'daily_progress.json').read_text(encoding='utf-8'))
    progress['checkpoint_seq'] = seq
    (home/'daily_progress.json').write_bytes(_bytes(progress))
    (home/'daily_receipt.json').write_bytes(_bytes(receipt))

    assert_pending(inspect_daily_rehearsal(home))


def test_progress_result_status_must_match_saved_summary(tmp_path):
    home, _, _ = sealed_history(tmp_path)
    progress = json.loads((home/'daily_progress.json').read_text(encoding='utf-8'))
    progress['result_status'] = 'APPROVED'
    (home/'daily_progress.json').write_bytes(_bytes(progress))

    assert_pending(inspect_daily_rehearsal(home))


def test_missing_summary_status_and_progress_result_status_do_not_match_as_none(tmp_path):
    home, _, receipt = sealed_history(tmp_path)
    summary = json.loads((home/'daily_rehearsal.json').read_text(encoding='utf-8'))
    summary.pop('status')
    summary_body = _bytes(summary)
    (home/'daily_rehearsal.json').write_bytes(summary_body)
    receipt['files'] = [_entry('daily_rehearsal.json', summary_body)]
    (home/'daily_receipt.json').write_bytes(_bytes(receipt))
    progress = json.loads((home/'daily_progress.json').read_text(encoding='utf-8'))
    progress.pop('result_status')
    (home/'daily_progress.json').write_bytes(_bytes(progress))

    assert_pending(inspect_daily_rehearsal(home))


def test_duplicate_json_object_key_is_rejected(tmp_path):
    home, _, receipt = sealed_history(tmp_path)
    files = json.dumps(receipt['files'], ensure_ascii=False, separators=(',', ':'))
    duplicate = ('{"version":1,"version":1,"mode":"mock",'
                 '"expected_completed_checkpoint_seq":4,"files":' + files + '}')
    (home/'daily_receipt.json').write_text(duplicate, encoding='utf-8')

    assert_pending(inspect_daily_rehearsal(home))


@pytest.mark.parametrize('name', ['daily_receipt.json', 'daily_progress.json',
                                  'daily_rehearsal.json'])
def test_broken_json_never_exposes_saved_summary(tmp_path, name):
    home, _, _ = sealed_history(tmp_path)
    (home/name).write_bytes(b'{broken')

    assert_pending(inspect_daily_rehearsal(home))


def test_symlink_inside_home_is_rejected_without_reading_target(tmp_path, monkeypatch):
    home, _, _ = sealed_history(tmp_path)
    outside = tmp_path/'outside-secret'
    outside.write_text('secret', encoding='utf-8')
    link = home/'linked-secret'
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'symlink creation unavailable: {exc}')
    original = Path.read_bytes
    reads = []

    def observed(path):
        reads.append(path.resolve())
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', observed)
    result = inspect_daily_rehearsal(home)

    assert_pending(result)
    assert outside.resolve() not in reads


@pytest.mark.parametrize('protected_name', ['daily_receipt.json', 'daily_progress.json'])
def test_receipt_or_progress_symlink_is_rejected_before_target_read(
        tmp_path, monkeypatch, protected_name):
    home, _, _ = sealed_history(tmp_path)
    protected = home/protected_name
    protected.unlink()
    outside = tmp_path/('outside-'+protected_name)
    outside.write_bytes(b'{"secret":"must not be read"}')
    try:
        os.symlink(outside, protected)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'symlink creation unavailable: {exc}')
    original = Path.read_bytes
    reads = []

    def observed(path):
        reads.append(path.resolve())
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', observed)
    result = inspect_daily_rehearsal(home)

    assert_pending(result)
    assert outside.resolve() not in reads


def test_reparse_attribute_on_receipt_blocks_read_without_link_privilege(
        tmp_path, monkeypatch):
    from types import SimpleNamespace
    import stat

    home, _, _ = sealed_history(tmp_path)
    receipt = (home/'daily_receipt.json').resolve()
    original_lstat = Path.lstat
    original_read = Path.read_bytes
    reads = []

    def marked_lstat(path):
        value = original_lstat(path)
        if path.absolute() == receipt:
            return SimpleNamespace(st_mode=value.st_mode,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return value

    def observed_read(path):
        reads.append(path.absolute())
        return original_read(path)

    monkeypatch.setattr(Path, 'lstat', marked_lstat)
    monkeypatch.setattr(Path, 'read_bytes', observed_read)
    result = inspect_daily_rehearsal(home)

    assert_pending(result)
    assert receipt not in reads


def test_change_between_complete_snapshots_is_detected(tmp_path, monkeypatch):
    from aitrader import daily_receipt

    home, _, _ = sealed_history(tmp_path)
    original = daily_receipt._snapshot
    calls = 0

    def changing_snapshot(target):
        nonlocal calls
        result = original(target)
        calls += 1
        if calls == 1:
            (target/'daily_rehearsal.json').write_text('{"changed":true}', encoding='utf-8')
        return result

    monkeypatch.setattr(daily_receipt, '_snapshot', changing_snapshot)
    result = inspect_daily_rehearsal(home)

    assert_pending(result)
    assert result['reason'] == 'CHANGED_DURING_INSPECTION'


def running_home(tmp_path, *, status='RUNNING', stage='finalize', seq=3):
    home = tmp_path/f'{status}-{stage}-{seq}'
    home.mkdir()
    progress = {'status': status, 'current_stage': stage, 'checkpoint_seq': seq,
                'mode': 'mock', 'source': 'synthetic', 'virtual_clock': True,
                'real_sent': False}
    (home/'daily_progress.json').write_bytes(_bytes(progress))
    (home/'mock-runner.json').write_bytes(_bytes({'mode': 'mock', 'version': 1}))
    (home/'daily_rehearsal.json').write_bytes(_bytes({
        'mode': 'mock', 'source': 'synthetic', 'virtual_clock': True,
        'real_sent': False, 'delivery': 'NOT_SENT'}))
    return home


def test_seal_accepts_only_running_finalize_and_next_exact_seq(tmp_path):
    home = running_home(tmp_path)

    receipt = seal_daily_rehearsal(home, expected_progress_seq=4)

    assert receipt['expected_completed_checkpoint_seq'] == 4
    assert (home/'daily_receipt.json').is_file()


@pytest.mark.parametrize('status,stage,seq,expected', [
    ('COMPLETED', 'finalize', 3, 4),
    ('FAILED', 'finalize', 3, 4),
    ('RUNNING', 'review', 3, 4),
    ('RUNNING', 'finalize', 3, 3),
    ('RUNNING', 'finalize', 3, 5),
    ('RUNNING', 'finalize', 3, True),
])
def test_invalid_progress_or_expected_seq_cannot_be_sealed(
        tmp_path, status, stage, seq, expected):
    home = running_home(tmp_path, status=status, stage=stage, seq=seq)

    with pytest.raises(RunError):
        seal_daily_rehearsal(home, expected_progress_seq=expected)
    assert not (home/'daily_receipt.json').exists()


def test_existing_receipt_cannot_be_overwritten_after_history_changes(tmp_path):
    home = running_home(tmp_path)
    seal_daily_rehearsal(home, expected_progress_seq=4)
    original = (home/'daily_receipt.json').read_bytes()
    (home/'ledger.sqlite').write_bytes(b'changed database bytes')

    with pytest.raises(RunError, match='既に存在'):
        seal_daily_rehearsal(home, expected_progress_seq=4)
    assert (home/'daily_receipt.json').read_bytes() == original


def test_seal_rejects_bool_marker_version(tmp_path):
    home = running_home(tmp_path)
    (home/'mock-runner.json').write_bytes(_bytes({'mode': 'mock', 'version': True}))

    with pytest.raises(RunError):
        seal_daily_rehearsal(home, expected_progress_seq=4)
    assert not (home/'daily_receipt.json').exists()


def test_seal_direct_call_rejects_dropbox_path(tmp_path):
    parent = tmp_path/'Dropbox-boundary'
    parent.mkdir()
    home = running_home(parent)

    with pytest.raises(RunError, match='Dropbox'):
        seal_daily_rehearsal(home, expected_progress_seq=4)
    assert not (home/'daily_receipt.json').exists()


@pytest.mark.parametrize('name', ['left.tmp', 'ledger.sqlite-wal',
                                  'ledger.sqlite-shm', 'extra.wal', 'extra.shm'])
def test_seal_rejects_temporary_and_database_sidecars(tmp_path, name):
    home = running_home(tmp_path)
    (home/name).write_bytes(b'ambiguous')

    with pytest.raises(RunError):
        seal_daily_rehearsal(home, expected_progress_seq=4)
    assert not (home/'daily_receipt.json').exists()


def test_seal_rejects_link_without_reading_target(tmp_path):
    home = running_home(tmp_path)
    outside = tmp_path/'outside-seal-target'
    outside.write_bytes(b'must not be sealed')
    try:
        os.symlink(outside, home/'linked-file')
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'symlink creation unavailable: {exc}')

    with pytest.raises(RunError):
        seal_daily_rehearsal(home, expected_progress_seq=4)
    assert not (home/'daily_receipt.json').exists()
