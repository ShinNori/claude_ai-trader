"""The documented byte boundary applies before history inspection."""
from pathlib import Path
import json
import pytest
from aitrader.evidence_history_fixture_cli import main


@pytest.mark.parametrize('excess', [0, 1])
def test_history_cli_one_mib_boundary(tmp_path, capsys, excess):
    raw = (Path(__file__).parents[1] / 'examples/evidence_history_valid.json').read_bytes()
    padded = raw + b' ' * (1048576 + excess - len(raw))
    source = tmp_path / 'history.json'
    source.write_bytes(padded)
    assert main(['--input', str(source)]) == (2 if excess else 0)
    output = capsys.readouterr()
    if excess:
        assert output.out == ''
        assert output.err == '証拠履歴を検査できません。入力形式と訂正の参照を確認してください。\n'
    else:
        assert output.err == ''
        assert json.loads(output.out)['status'] == 'VERIFIED_OFFLINE_HISTORY'
    assert source.read_bytes() == padded
