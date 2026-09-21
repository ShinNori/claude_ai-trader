"""Offline CLI tests for V2 page shape inspection."""
import hashlib
import json
import socket

import duckdb
import pytest
import requests

from aitrader.jquants_v2_inspection_cli import main
from test_jquants_v2_contract import ROWS


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("dataset", ROWS)
def test_all_dataset_shapes_succeed_without_changing_input(tmp_path, capsys, dataset):
    source = tmp_path / "page.json"
    _write(source, {"data": [ROWS[dataset]], "pagination_key": "next"})
    before = (hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_mtime_ns)
    assert main(["--dataset", dataset, "--input", str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result == {
        "contract": "v2-shape-only", "dataset": dataset, "row_count": 1,
        "has_more": True, "read_only": True, "ready_for_live": False,
        "fixture_only": True, "current_signal": False,
    }
    assert (hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_mtime_ns) == before


def test_empty_data_is_valid_terminal_page(tmp_path, capsys):
    source = tmp_path / "page.json"
    _write(source, {"data": []})
    assert main(["--dataset", "calendar", "--input", str(source)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["row_count"] == 0
    assert result["has_more"] is False


@pytest.mark.parametrize("body", [
    b"{", b'{"data":[],"data":[]}', b'{"data":NaN}', b"[]",
    json.dumps({"data": [{"Date": "2026-08-31"}]}).encode(),
    json.dumps({"data": [], "pagination_key": 1}).encode(),
])
def test_bad_json_or_shape_has_fixed_error(tmp_path, capsys, body):
    source = tmp_path / "page.json"
    source.write_bytes(body)
    assert main(["--dataset", "prices", "--input", str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "V2仮データのローカル形状検査に失敗しました\n"


def test_oversize_and_nonregular_input_are_rejected(tmp_path, capsys):
    oversized = tmp_path / "large.json"
    oversized.write_bytes(b"{" + b" " * (1024 * 1024) + b"}")
    assert main(["--dataset", "prices", "--input", str(oversized)]) == 2
    assert capsys.readouterr().out == ""
    directory = tmp_path / "directory"
    directory.mkdir()
    assert main(["--dataset", "prices", "--input", str(directory)]) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("argv", [
    [], ["--dataset", "unknown", "--input", "secret"],
    ["--dataset", "prices"], ["--input", "secret"],
])
def test_invalid_arguments_are_fixed_and_quiet(capsys, argv):
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret" not in captured.err
    assert "usage:" not in captured.err


def test_secret_content_and_path_are_not_leaked(tmp_path, capsys):
    secret = "SECRET_V2_CLI_4712"
    source = tmp_path / f"{secret}.json"
    source.write_text('{"data":[{"Date":"' + secret, encoding="utf-8")
    assert main(["--dataset", "calendar", "--input", str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert secret not in captured.err
    assert str(source) not in captured.err


def test_no_network_authentication_or_database_access(tmp_path, capsys, monkeypatch):
    source = tmp_path / "page.json"
    _write(source, {"data": [ROWS["topix"]]})
    def forbidden(*args, **kwargs):
        raise AssertionError("offline inspection attempted external access")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(requests, "Session", forbidden)
    monkeypatch.setattr(duckdb, "connect", forbidden)
    assert main(["--dataset", "topix", "--input", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["dataset"] == "topix"

