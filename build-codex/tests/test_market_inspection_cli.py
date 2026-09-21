from datetime import date
import json
from pathlib import Path

import pytest

from aitrader import market_inspection_cli as cli


@pytest.mark.parametrize('status,code', [('INSPECTED', 0), ('UNKNOWN', 2)])
def test_cli_is_stdout_only_and_keeps_non_signal_flags(monkeypatch, capsys, tmp_path, status, code):
    seen = []
    report = {'status': status, 'current_signal': False, 'ready_for_live': False}
    def inspect(home, *, as_of):
        seen.append((home, as_of))
        return report
    monkeypatch.setattr(cli, 'inspect_market_inputs', inspect)
    home = tmp_path/'absent'
    assert cli.main(['--home', str(home), '--as-of', '2026-08-31']) == code
    assert seen == [(home, date(2026, 8, 31))]
    assert json.loads(capsys.readouterr().out) == report
    assert not home.exists()


def test_invalid_date_never_inspects_or_echoes_input(monkeypatch, capsys):
    monkeypatch.setattr(cli, 'inspect_market_inputs', lambda *a, **k: pytest.fail('unexpected inspect'))
    with pytest.raises(SystemExit) as error:
        cli.main(['--home', '.', '--as-of', 'sensitive-invalid-value'])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'sensitive-invalid-value' not in captured.err
