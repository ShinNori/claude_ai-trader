"""Independent safety tests for the atomic JSON writer."""
import json
import os
from pathlib import Path

import pytest

from aitrader.runner import RunError, write_json


def test_existing_target_unicode_and_secret_redaction(tmp_path, monkeypatch):
    target = tmp_path/'state.json'
    target.write_text('{"old":true}', encoding='utf-8')
    secret = 'writer-secret-value'
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', secret)
    write_json(target, {'日本語': '保存', 'message': f'before {secret} after'})
    raw = target.read_text(encoding='utf-8')
    saved = json.loads(raw)
    assert saved['日本語'] == '保存'
    assert secret not in raw
    assert '\\u65e5' not in raw


def test_stale_fixed_temp_file_is_never_opened_or_changed(tmp_path, monkeypatch):
    target = tmp_path/'state.json'
    stale = target.with_suffix('.json.tmp')
    stale.write_bytes(b'attacker-owned')
    original_open = Path.open

    def guarded_open(self, *args, **kwargs):
        if self == stale:
            pytest.fail('legacy fixed temporary path was opened')
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded_open)
    write_json(target, {'ok': True})
    monkeypatch.undo()
    assert stale.read_bytes() == b'attacker-owned'
    assert json.loads(target.read_text(encoding='utf-8')) == {'ok': True}


def test_stale_fixed_temp_symlink_cannot_write_outside(tmp_path):
    target = tmp_path/'state.json'
    stale = target.with_suffix('.json.tmp')
    outside = tmp_path/'outside.txt'
    outside.write_bytes(b'outside-secret')
    try:
        os.symlink(outside, stale)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'symlink unavailable: {exc}')
    write_json(target, {'ok': True})
    assert outside.read_bytes() == b'outside-secret'
    assert stale.is_symlink()


def test_replace_failure_keeps_old_target_and_owned_unique_temp(tmp_path, monkeypatch):
    target = tmp_path/'state.json'
    target.write_bytes(b'{"old":true}')

    def fail_replace(source, destination):
        assert Path(destination) == target
        assert Path(source).name.startswith('.state.json.')
        assert Path(source).name.endswith('.tmp')
        raise OSError('injected replace failure')

    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(OSError, match='injected replace failure'):
        write_json(target, {'new': True})
    assert target.read_bytes() == b'{"old":true}'
    remnants = list(tmp_path.glob('.state.json.*.tmp'))
    assert len(remnants) == 1
    assert json.loads(remnants[0].read_text(encoding='utf-8')) == {'new': True}


def test_encoding_error_occurs_before_directory_or_temp_creation(tmp_path):
    parent = tmp_path/'not-created'
    with pytest.raises(ValueError):
        write_json(parent/'state.json', {'invalid': float('nan')})
    assert not parent.exists()


@pytest.mark.parametrize('unsafe_part', ['target', 'parent'])
def test_injected_reparse_is_rejected_before_temp_or_write(tmp_path, monkeypatch,
                                                           unsafe_part):
    target = tmp_path/'folder'/'state.json'
    target.parent.mkdir()
    if unsafe_part == 'target':
        target.write_bytes(b'{"old":true}')
    unsafe = target if unsafe_part == 'target' else target.parent
    original = os.path.isjunction
    observed = False

    def junction(path):
        nonlocal observed
        if Path(path) == unsafe:
            observed = True
            return True
        return original(path)

    monkeypatch.setattr(os.path, 'isjunction', junction)
    monkeypatch.setattr('tempfile.mkstemp',
                        lambda *a, **k: pytest.fail('unsafe path reached temp creation'))
    with pytest.raises(RunError, match='JSON保存先を安全に確認できません'):
        write_json(target, {'new': True})
    assert observed
    if target.exists():
        assert target.read_bytes() == b'{"old":true}'


def test_actual_link_target_is_rejected_without_external_write(tmp_path):
    outside = tmp_path/'outside.json'
    outside.write_bytes(b'{"outside":true}')
    target = tmp_path/'state.json'
    try:
        os.symlink(outside, target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'symlink unavailable: {exc}')
    with pytest.raises(RunError):
        write_json(target, {'new': True})
    assert outside.read_bytes() == b'{"outside":true}'
