"""Known unsafe output paths must not leave a newly approved reservation."""
import hashlib
import os

import pytest

from aitrader.runner import RunError
from test_runner import DAY, setup


def snapshot(home):
    return {str(p.relative_to(home)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                      p.stat().st_mtime_ns)
            for p in home.rglob('*') if p.is_file()}


@pytest.mark.parametrize('part', ['day', 'run', 'manifest.json', 'failure.json'])
def test_output_reparse_is_rejected_before_journal_or_reservation(setup, monkeypatch, part):
    home, execute = setup
    day = home/'runs'/DAY.isoformat()
    folder = day/'run1'
    folder.mkdir(parents=True)
    path = day if part == 'day' else folder if part == 'run' else folder/part
    if part.endswith('.json'):
        path.write_text('{}', encoding='utf-8')
    before = snapshot(home)
    original = os.path.isjunction
    monkeypatch.setattr(os.path, 'isjunction', lambda p: p == path or original(p))
    with pytest.raises(RunError, match='成果物保存先'):
        execute()
    assert snapshot(home) == before
    assert not (home/'orchestration.sqlite').exists()


@pytest.mark.parametrize('part', ['run', 'result.json', 'proposals.json'])
def test_wrong_output_type_is_rejected_before_journal_or_reservation(setup, part):
    home, execute = setup
    folder = home/'runs'/DAY.isoformat()/'run1'
    if part == 'run':
        folder.parent.mkdir(parents=True)
        folder.write_text('not a directory', encoding='utf-8')
    else:
        (folder/part).mkdir(parents=True)
    before = snapshot(home)
    with pytest.raises(RunError, match='成果物保存先'):
        execute()
    assert snapshot(home) == before
    assert not (home/'orchestration.sqlite').exists()
