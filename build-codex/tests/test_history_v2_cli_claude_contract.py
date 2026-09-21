"""Claude 第13回 独立反証: v2 CLI 2 本の読取境界・固定 stderr・終了コードのうち既存 4 試験ファイルが触れていない点。

対象: aitrader/evidence_history_fixture_v2_cli.py, aitrader/history_validity_binding_v2_cli.py。
製品・既存試験・例は変更しない。--help の挙動（R13-01）は契約未確定のためここでは固定しない。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import aitrader.evidence_history_fixture_v2_cli as history_cli
import aitrader.history_validity_binding_v2_cli as binding_cli

ROOT = Path(__file__).parents[1]
EXPECTED = json.loads((Path(__file__).with_name("binding_v2_expected.json")).read_text(encoding="utf-8"))


def _history_value():
    value = json.loads((ROOT / "examples/evidence_history_valid.json").read_text(encoding="utf-8"))
    value["mode"] = "evidence_history_fixture_v2"
    return value


def _binding_value():
    return json.loads((ROOT / "examples/history_validity_binding_v2_valid.json").read_text(encoding="utf-8"))


CASES = [
    pytest.param(history_cli, "aitrader.evidence_history_fixture_v2_cli", _history_value,
                 "inspect_evidence_history", "VERIFIED_OFFLINE_HISTORY", id="history"),
    pytest.param(binding_cli, "aitrader.history_validity_binding_v2_cli", _binding_value,
                 "inspect_history_validity_binding", "VERIFIED_OFFLINE_BINDING", id="binding"),
]


def _forbid_api(monkeypatch, cli, api_name):
    monkeypatch.setattr(cli, api_name, lambda value: pytest.fail(f"API called with {value!r}"))


@pytest.mark.parametrize("cli,module,factory,api_name,success", CASES)
def test_utf8_bom_input_is_accepted_and_succeeds(tmp_path, capsys, cli, module, factory, api_name, success):
    source = tmp_path / "bom.json"
    source.write_bytes(b"\xef\xbb\xbf" + json.dumps(factory()).encode("utf-8"))
    assert cli.main(["--input", str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["status"] == success


@pytest.mark.parametrize("cli,module,factory,api_name,success", CASES)
@pytest.mark.parametrize("body", [b"", b"  \r\n", b'{"mode":"\xe9"}', b"\xff\xfe{}", b"null", b'"text"', b"42"])
def test_empty_whitespace_non_utf8_and_non_object_bodies_never_reach_api(
        tmp_path, capsys, monkeypatch, cli, module, factory, api_name, success, body):
    _forbid_api(monkeypatch, cli, api_name)
    source = tmp_path / "body.json"
    source.write_bytes(body)
    assert cli.main(["--input", str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == cli._ERROR + "\n"


@pytest.mark.parametrize("cli,module,factory,api_name,success", CASES)
def test_directory_input_uses_fixed_error_without_api_call(tmp_path, capsys, monkeypatch, cli, module, factory, api_name, success):
    _forbid_api(monkeypatch, cli, api_name)
    directory = tmp_path / "dir.json"
    directory.mkdir()
    assert cli.main(["--input", str(directory)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == cli._ERROR + "\n"


@pytest.mark.parametrize("cli,module,factory,api_name,success", CASES)
@pytest.mark.parametrize("argv", [["--extra"], ["--input"], ["--output", "x"], ["positional"]])
def test_argument_errors_use_fixed_error_and_never_echo_values(
        tmp_path, capsys, monkeypatch, cli, module, factory, api_name, success, argv):
    _forbid_api(monkeypatch, cli, api_name)
    source = tmp_path / "ok.json"
    source.write_text(json.dumps(factory()), encoding="utf-8")
    full = (["--input", str(source)] + argv) if argv != ["--input"] else argv
    assert cli.main(full) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == cli._ERROR + "\n"
    assert "positional" not in captured.err and "--extra" not in captured.err


@pytest.mark.parametrize("cli,module,factory,api_name,success", CASES)
def test_success_line_is_exact_sorted_json_and_input_file_is_untouched(tmp_path, capsys, cli, module, factory, api_name, success):
    source = tmp_path / "in.json"
    payload = json.dumps(factory(), ensure_ascii=False, indent=1).encode("utf-8")
    source.write_bytes(payload)
    before = source.stat()
    assert cli.main(["--input", str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith("\n") and captured.out.count("\n") == 1
    parsed = json.loads(captured.out)
    assert captured.out == json.dumps(parsed, ensure_ascii=False, sort_keys=True) + "\n"
    assert list(parsed) == sorted(parsed)
    assert source.read_bytes() == payload
    assert (source.stat().st_size, source.stat().st_mtime_ns) == (before.st_size, before.st_mtime_ns)


def test_binding_data_incomplete_vector_is_json_exit_two_in_a_real_process(tmp_path):
    value = _binding_value()
    value["period_history"]["decision_at"] = "2026-09-12T06:51:00+09:00"  # E2
    source = tmp_path / "e2.json"
    source.write_text(json.dumps(value), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "aitrader.history_validity_binding_v2_cli", "--input", str(source)],
        cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1"),
        text=True, encoding="utf-8", capture_output=True, check=False)
    assert completed.returncode == 2
    assert completed.stderr == ""
    assert completed.stdout == json.dumps(EXPECTED["E2"], ensure_ascii=False, sort_keys=True) + "\n"


def test_history_cli_rejects_period_history_bundle_as_json_not_error(tmp_path, capsys):
    """期間履歴 v1 束を行履歴 v2 CLI に渡すと API 段の INVALID_MODE（固定 stderr ではない）。"""
    source = tmp_path / "period.json"
    source.write_bytes((ROOT / "examples/period_evidence_history_valid.json").read_bytes())
    assert history_cli.main(["--input", str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["reason_codes"] == ["INVALID_MODE"]
