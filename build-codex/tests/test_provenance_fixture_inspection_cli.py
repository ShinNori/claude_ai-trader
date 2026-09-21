"""CLI integration for read-only provenance fixture inspection."""
import hashlib
import json
import shutil
import socket
import subprocess

import pytest

from aitrader.db import connect, init
from aitrader.provenance_fixture_cli import main
from test_provenance_fixture_isolation import ingest


@pytest.fixture(scope="module")
def recorded_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("recorded-lineage") / "home"
    ingest(home)
    return home


def _files(home):
    return {
        path.relative_to(home).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
        )
        for path in home.rglob("*") if path.is_file()
    }


def _forbid_external(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("inspection attempted external process or network access")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_verified_inspection_is_bounded_and_byte_unchanged(
        recorded_home, monkeypatch, capsys):
    _forbid_external(monkeypatch)
    before = _files(recorded_home)

    assert main(["inspect", "--home", str(recorded_home)]) == 0

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["status"] == "VERIFIED_FIXTURE"
    assert result["fixture_only"] is True
    assert result["read_only"] is True
    assert result["current_signal"] is False
    assert result["ready_for_live"] is False
    assert result["automatic_resume_allowed"] is False
    assert _files(recorded_home) == before


def test_corrupt_fixture_is_safe_json_exit_two_and_unchanged(
        tmp_path, recorded_home, monkeypatch, capsys):
    _forbid_external(monkeypatch)
    home = tmp_path / "corrupt"
    shutil.copytree(recorded_home, home)
    with connect(home) as database:
        database.execute("UPDATE prices_daily SET close=close+1")
    before = _files(home)

    assert main(["inspect", "--home", str(home)]) == 2

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["status"] == "UNKNOWN"
    assert result["tables"] == {}
    assert result["current_signal"] is False
    assert result["ready_for_live"] is False
    assert _files(home) == before


def test_non_fixture_home_is_safe_json_exit_two_and_unchanged(
        tmp_path, monkeypatch, capsys):
    _forbid_external(monkeypatch)
    home = tmp_path / "ordinary"
    init(home)
    before = _files(home)

    assert main(["inspect", "--home", str(home)]) == 2

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["status"] == "UNKNOWN"
    assert result["fixture_only"] is True
    assert result["current_signal"] is False
    assert result["ready_for_live"] is False
    assert _files(home) == before


@pytest.mark.parametrize("argv,secret", [
    (["inspect"], ""),
    (["inspect", "--home", "SECRET_PATH_9281", "SECRET_EXTRA_8172"],
     "SECRET"),
])
def test_invalid_inspect_arguments_use_fixed_nonleaking_error(
        tmp_path, capsys, argv, secret):
    before = set(tmp_path.iterdir())

    assert main(argv) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "仮データ専用の市場来歴操作に失敗しました。"
        "入力と読取診断を確認してください。\n"
    )
    assert not secret or secret not in captured.err
    assert set(tmp_path.iterdir()) == before
