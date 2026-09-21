"""Raw CLI export links must be checked before resolution or copying."""
import json
import sys
from pathlib import Path

import pytest

import aitrader.__main__ as cli


@pytest.mark.parametrize('position', ['parent', 'output'])
def test_raw_output_reparse_is_rejected_before_backtest(tmp_path, monkeypatch, position):
    output = tmp_path/'out'
    output.mkdir()
    unsafe = tmp_path if position == 'parent' else output
    original = cli.os.path.isjunction
    monkeypatch.setattr(cli.os.path, 'isjunction',
                        lambda value: Path(value) == unsafe or original(value))
    monkeypatch.setattr(cli, 'run_backtest', lambda *a: pytest.fail('unsafe export reached calculation'))
    monkeypatch.setattr(sys, 'argv', ['aitrader', 'backtest', '--strategy', 'fixture',
                                    '--from', '2026-09-01', '--to', '2026-09-04', '--out', str(output)])
    with pytest.raises(ValueError, match='出力先'):
        cli.main()
    assert list(output.iterdir()) == []


@pytest.mark.parametrize('position', ['folder', 'summary.json', 'report.html'])
def test_export_target_reparse_is_rejected_before_copy(tmp_path, monkeypatch, position):
    shared = tmp_path/'shared'
    output = shared/'results'
    final = output/'fixture_v1'
    final.mkdir(parents=True)
    for name in ('summary.json', 'report.html'):
        (final/name).write_text('old-'+name, encoding='utf-8')
    unsafe = final if position == 'folder' else final/position
    original = cli.os.path.isjunction
    monkeypatch.setattr(cli.os.path, 'isjunction',
                        lambda value: Path(value) == unsafe or original(value))
    monkeypatch.setattr(cli, '__file__', str(shared/'build-codex'/'aitrader'/'__main__.py'))
    monkeypatch.setattr(cli, 'default_home', lambda: tmp_path/'runtime')
    monkeypatch.setattr(cli, 'run_backtest', lambda *a: {'version': 'v1'})
    monkeypatch.setattr(sys, 'argv', ['aitrader', 'backtest', '--strategy', 'fixture',
                                    '--from', '2026-09-01', '--to', '2026-09-04', '--out', str(output)])
    with pytest.raises(ValueError, match='出力先'):
        cli.main()
    for name in ('summary.json', 'report.html'):
        assert (final/name).read_text(encoding='utf-8') == 'old-'+name


def test_regular_shared_export_keeps_summary_only_contract(tmp_path, monkeypatch, capsys):
    shared = tmp_path/'Dropbox'/'shared'
    output = shared/'results'
    runtime = tmp_path/'runtime'
    monkeypatch.setattr(cli, '__file__', str(shared/'build-codex'/'aitrader'/'__main__.py'))
    monkeypatch.setattr(cli, 'default_home', lambda: runtime)
    calls = []
    def calculate(home, strategy, start, end, target):
        calls.append(target)
        folder = target/'fixture_v1'
        folder.mkdir(parents=True)
        for name in ('summary.json', 'report.html', 'trades.csv', 'equity.csv'):
            (folder/name).write_text('new-'+name, encoding='utf-8')
        (folder/'summary.json').write_text('{"version":"v1"}', encoding='utf-8')
        return {'version': 'v1'}
    monkeypatch.setattr(cli, 'run_backtest', calculate)
    monkeypatch.setattr(sys, 'argv', ['aitrader', 'backtest', '--strategy', 'fixture',
                                    '--from', '2026-09-01', '--to', '2026-09-04', '--out', str(output)])
    cli.main()
    assert calls == [runtime/'results']
    assert {p.name for p in (output/'fixture_v1').iterdir()} == {'summary.json', 'report.html'}
    assert json.loads(capsys.readouterr().out) == {'version': 'v1'}


def test_wrong_type_output_is_rejected(tmp_path):
    output = tmp_path/'not-a-directory'
    output.write_text('retain', encoding='utf-8')
    with pytest.raises(ValueError, match='出力先'):
        cli._validate_export_path(output)
    assert output.read_text(encoding='utf-8') == 'retain'
