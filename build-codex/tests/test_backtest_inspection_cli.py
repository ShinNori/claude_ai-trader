import json

import pytest

from aitrader import backtest_inspection_cli as cli


@pytest.mark.parametrize('status,code', [('OBSERVED', 0), ('INCOMPLETE', 2),
                                      ('REVIEW_REQUIRED', 2), ('UNKNOWN', 2)])
def test_cli_returns_observation_status_without_writing(tmp_path, monkeypatch, capsys, status, code):
    folder = tmp_path/'results'
    def inspect(value):
        assert value == folder
        return {'status': status, 'read_only': True, 'generation_verified': False,
                'automatic_resume_allowed': False, 'ready_for_live': False,
                'observation_atomic': False}
    monkeypatch.setattr(cli, 'inspect_backtest_results', inspect)
    assert cli.main(['--results', str(folder)]) == code
    output = capsys.readouterr()
    assert json.loads(output.out)['status'] == status
    assert output.err == ''
    assert not folder.exists()


def test_cli_argument_error_does_not_echo_unknown_value(capsys):
    with pytest.raises(SystemExit) as failure:
        cli.main(['--unexpected', 'private-value'])
    assert failure.value.code == 2
    output = capsys.readouterr()
    assert 'private-value' not in output.err
    assert output.out == ''
