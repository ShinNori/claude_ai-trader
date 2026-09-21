"""Independent boundaries for standalone HTML report regeneration."""
import json
import os
from pathlib import Path

import pytest

import aitrader.report as report


def _folder(tmp_path, summary=None):
    folder = tmp_path / 'margin_bucket_long_v1'
    folder.mkdir()
    value = summary if summary is not None else {
        'strategy': 'margin_bucket_long',
        'final_equity': 10_000_000,
        'yearly': {'2026': {'return': 0.1, 'trades': 2}},
    }
    (folder / 'summary.json').write_text(
        json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return folder


def _lock(folder):
    return folder.parent / ('.' + folder.name + '.publish.lock')


def test_legacy_summary_and_yearly_render_with_html_escaping(tmp_path):
    folder = _folder(tmp_path, {
        '<strategy>': '<script>alert(1)</script>',
        'yearly': {'<2026>': {'note': '</pre><script>x</script>'}},
    })

    report.render(folder)

    body = (folder / 'report.html').read_text(encoding='utf-8')
    assert body.startswith('<!doctype html>')
    assert '&lt;strategy&gt;' in body
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in body
    assert '&lt;2026&gt;' in body
    assert '<script>' not in body
    assert not _lock(folder).exists()
    assert list(folder.glob('.report.*.tmp')) == []


def test_render_failure_keeps_old_html_and_releases_own_lock(tmp_path, monkeypatch):
    folder = _folder(tmp_path)
    old = b'previous complete report'
    (folder / 'report.html').write_bytes(old)
    monkeypatch.setattr(report, '_html', lambda value: (_ for _ in ()).throw(RuntimeError('boom')))

    with pytest.raises(RuntimeError, match='boom'):
        report.render(folder)

    assert (folder / 'report.html').read_bytes() == old
    assert not _lock(folder).exists()


def test_replace_failure_keeps_old_html_and_removes_unique_temp(tmp_path, monkeypatch):
    folder = _folder(tmp_path)
    old = b'previous complete report'
    (folder / 'report.html').write_bytes(old)
    monkeypatch.setattr(report.os, 'replace',
                        lambda *args: (_ for _ in ()).throw(OSError('replace failed')))

    with pytest.raises(OSError, match='replace failed'):
        report.render(folder)

    assert (folder / 'report.html').read_bytes() == old
    assert list(folder.glob('.report.*.tmp')) == []
    assert not _lock(folder).exists()


def test_existing_lock_is_preserved_and_report_is_unchanged(tmp_path):
    folder = _folder(tmp_path)
    old = b'previous complete report'
    (folder / 'report.html').write_bytes(old)
    lock = _lock(folder)
    lock.write_bytes(b'existing owner')

    with pytest.raises(ValueError, match='既に実行中または要確認'):
        report.render(folder)

    assert lock.read_bytes() == b'existing owner'
    assert (folder / 'report.html').read_bytes() == old


@pytest.mark.parametrize('unsafe_part', ['parent', 'summary', 'report'])
def test_parent_or_target_reparse_is_rejected_before_read_or_write(
        tmp_path, monkeypatch, unsafe_part):
    folder = _folder(tmp_path)
    old = b'previous complete report'
    (folder / 'report.html').write_bytes(old)
    unsafe = {'parent': folder.parent, 'summary': folder / 'summary.json',
              'report': folder / 'report.html'}[unsafe_part]
    original = report.os.path.isjunction
    observed = False

    def injected(path):
        nonlocal observed
        if Path(path) == unsafe:
            observed = True
            return True
        return original(path)

    monkeypatch.setattr(report.os.path, 'isjunction', injected)
    monkeypatch.setattr(report.tempfile, 'mkstemp',
                        lambda *a, **k: pytest.fail('unsafe path reached writer'))

    with pytest.raises(ValueError, match='安全に確認'):
        report.render(folder)

    assert observed
    assert (folder / 'report.html').read_bytes() == old
    assert not _lock(folder).exists()


def test_summary_change_during_read_is_rejected_before_publication(tmp_path, monkeypatch):
    folder = _folder(tmp_path)
    old = b'previous complete report'
    (folder / 'report.html').write_bytes(old)
    summary = folder / 'summary.json'
    original_open = Path.open
    changed = False

    class ChangingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, *args):
            nonlocal changed
            value = self.stream.read(*args)
            info = summary.stat()
            os.utime(summary, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            changed = True
            return value

    def changing_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if Path(path) == summary and args and args[0] == 'rb':
            return ChangingStream(stream)
        return stream

    monkeypatch.setattr(Path, 'open', changing_open)

    with pytest.raises(ValueError, match='観測中に変化'):
        report.render(folder)

    assert changed
    assert (folder / 'report.html').read_bytes() == old
    assert list(folder.glob('.report.*.tmp')) == []


@pytest.mark.parametrize('summary', [
    [],
    {'strategy': 'x'},
    {'yearly': []},
    {'yearly': {}, 'value': float('nan')},
    {'yearly': {}, 'value': float('inf')},
])
def test_invalid_shape_or_nonfinite_number_keeps_old_html(tmp_path, summary):
    folder = _folder(tmp_path, summary)
    old = b'previous complete report'
    (folder / 'report.html').write_bytes(old)

    with pytest.raises(ValueError, match='形式を確認'):
        report.render(folder)

    assert (folder / 'report.html').read_bytes() == old
    assert not _lock(folder).exists()


def test_oversized_summary_is_rejected_without_replacing_html(tmp_path):
    folder = _folder(tmp_path)
    old = b'previous complete report'
    (folder / 'report.html').write_bytes(old)
    (folder / 'summary.json').write_bytes(b' ' * (report._MAX_SUMMARY_BYTES + 1))

    with pytest.raises(ValueError, match='大きすぎます'):
        report.render(folder)

    assert (folder / 'report.html').read_bytes() == old
    assert not _lock(folder).exists()
