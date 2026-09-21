"""Reject malformed CLI arguments without echoing accidental secret values."""
import pytest
from aitrader import managed_control


@pytest.mark.parametrize('extra', [
    ['--action', 'secret-not-a-valid-action'],
    ['--unexpected-option', 'secret-not-a-valid-action'],
])
def test_parser_rejection_does_not_echo_argument_values(monkeypatch, capsys, extra):
    called = []
    monkeypatch.setattr(managed_control, 'apply_managed_control',
                        lambda *a, **k: called.append(True))
    with pytest.raises(SystemExit) as error:
        managed_control.main(['--home', 'unused', '--settings', 'unused.json',
                              '--action', 'STOP', '--now', '2026-09-11T07:00:00+09:00',
                              *extra])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert not called
    assert output.out == ''
    assert 'secret-not-a-valid-action' not in output.err
    assert '--help' in output.err
