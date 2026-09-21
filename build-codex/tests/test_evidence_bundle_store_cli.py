"""CLI contract tests for offline evidence bundle storage."""
from __future__ import annotations

import json
from pathlib import Path

import aitrader.evidence_bundle_store_cli as cli


ROOT = Path(__file__).parents[1]
E1 = ROOT / "examples" / "history_validity_binding_v2_valid.json"
V1 = ROOT / "examples" / "history_validity_binding_valid.json"
ERROR = '人工束を保存・再読できません。保存先と入力形式を確認してください。\n'


def test_cli_put_and_verify_success_emit_one_json_line(tmp_path, capsys):
    home = tmp_path / "runtime"
    assert cli.main(["put", "--input", str(E1), "--home", str(home)]) == 0
    put_result = json.loads(capsys.readouterr().out)
    assert put_result["status"] == "STORED"
    assert cli.main([
        "verify", "--id", put_result["bundle_sha256"], "--home", str(home)
    ]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out)["status"] == "REPRODUCED"


def test_cli_inner_data_incomplete_is_stored_with_exit_zero(tmp_path, capsys):
    result = cli.main([
        "put", "--input", str(V1), "--home", str(tmp_path / "runtime")
    ])
    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    value = json.loads(captured.out)
    assert value["status"] == "STORED"
    assert value["bundle_status"] == "DATA_INCOMPLETE"


def test_cli_api_data_incomplete_emits_json_and_exit_two(tmp_path, capsys):
    result = cli.main([
        "put", "--input", str(tmp_path / "missing.json"),
        "--home", str(tmp_path / "runtime")
    ])
    captured = capsys.readouterr()
    assert result == 2
    assert captured.err == ""
    assert json.loads(captured.out)["reason_codes"] == ["INPUT_UNREADABLE"]


def test_cli_invalid_id_emits_json_and_exit_two(tmp_path, capsys):
    assert cli.main(["verify", "--id", "BAD", "--home", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["reason_codes"] == ["RECORD_UNREADABLE"]


def test_cli_argument_error_uses_fixed_stderr(tmp_path, capsys):
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR


def test_cli_unexpected_exception_uses_fixed_stderr(capsys, monkeypatch):
    monkeypatch.setattr(cli, "put", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))
    assert cli.main(["put", "--input", "unused"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ERROR
