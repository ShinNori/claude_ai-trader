"""CLI contracts for the isolated offline provenance fixture."""
import hashlib
import json
from pathlib import Path

import pytest

import aitrader.packet_cli as packet_cli
from aitrader.db import init
from aitrader.provenance_fixture_cli import main


def fixture():
    return {
        "daily_quotes": [{"Code": "1300", "Date": "2026-08-31",
                          "Open": 100, "High": 110, "Low": 90, "Close": 105,
                          "Volume": 10000, "TurnoverValue": 1000000}],
        "weekly_margin_interest": [{"Code": "1300", "Date": "2026-08-28",
                                     "LongMarginTradeVolume": 100,
                                     "ShortMarginTradeVolume": 50}],
        "info": [{"Code": "1300", "CompanyName": "Fixture"}],
        "topix": [{"Date": "2026-08-31", "Close": 2000}],
        "trading_calendar": [{"Date": "2026-08-31", "HolidayDivision": "1"}],
    }


def write_fixture(path, value=None):
    path.write_text(json.dumps(value or fixture(), ensure_ascii=False), encoding="utf-8")


def args(home, path):
    return ["ingest", "--home", str(home), "--fixture", str(path),
            "--from", "2026-08-31", "--to", "2026-08-31",
            "--recorded-at", "2026-09-01T12:00:00+00:00"]


def test_ingest_no_op_and_inspect_round_trip(tmp_path, capsys):
    home = tmp_path / "home"
    source = tmp_path / "fixture.json"
    write_fixture(source)

    assert main(args(home, source)) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["result"] == "COMPLETED"
    assert completed["fixture_only"] is True
    assert completed["current_signal"] is False
    assert completed["ready_for_live"] is False

    assert main(args(home, source)) == 0
    no_op = json.loads(capsys.readouterr().out)
    assert no_op["result"] == "NO_OP"
    assert no_op["run_id"] == completed["run_id"]

    assert main(["inspect", "--home", str(home)]) == 0
    observed = json.loads(capsys.readouterr().out)
    assert observed["status"] == "VERIFIED_FIXTURE"
    assert observed["fixture_only"] is True
    assert observed["current_signal"] is False
    assert observed["ready_for_live"] is False
    assert observed["automatic_resume_allowed"] is False


def test_missing_inspection_is_json_but_nonzero_and_does_not_create(tmp_path, capsys):
    home = tmp_path / "missing"
    assert main(["inspect", "--home", str(home)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["status"] == "UNKNOWN"
    assert not home.exists()


@pytest.mark.parametrize("body", [
    b'{"daily_quotes":[],"daily_quotes":[]}',
    b'{"nested":{"x":1,"x":2}}',
    b'{"x":NaN}',
    b'{"x":1e999}',
    b'[]',
    b'\xff\xfe',
])
def test_invalid_json_or_shape_fails_before_database_side_effect(
    tmp_path, capsys, body
):
    home = tmp_path / "home"
    source = tmp_path / "fixture.json"
    source.write_bytes(body)
    assert main(args(home, source)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("仮データ専用")
    assert not home.exists()


def test_oversize_fixture_fails_before_home_creation(tmp_path, capsys):
    home = tmp_path / "home"
    source = tmp_path / "fixture.json"
    source.write_bytes(b"{" + b" " * (1024 * 1024) + b"}")
    assert main(args(home, source)) == 2
    assert capsys.readouterr().out == ""
    assert not home.exists()


@pytest.mark.parametrize("bad_option,bad_value", [
    ("--from", "SECRET_BAD_DATE"),
    ("--recorded-at", "SECRET_NAIVE_TIME"),
])
def test_invalid_dates_do_not_leak_argument_values(
    tmp_path, capsys, bad_option, bad_value
):
    home = tmp_path / "home"
    source = tmp_path / "fixture.json"
    write_fixture(source)
    command = args(home, source)
    command[command.index(bad_option) + 1] = bad_value
    assert main(command) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert bad_value not in captured.err
    assert not home.exists()


def test_corrupt_secret_json_and_raw_path_are_not_echoed(tmp_path, capsys):
    secret = "SECRET_TOKEN_998877"
    source = tmp_path / f"{secret}.json"
    source.write_text('{"secret":"' + secret, encoding="utf-8")
    home = tmp_path / "home"
    assert main(args(home, source)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert secret not in captured.err
    assert str(source) not in captured.err


def test_reparse_fixture_is_rejected_before_open_and_home_creation(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    source = tmp_path / "fixture.json"
    write_fixture(source)
    original_link = packet_cli._is_link
    original_open = Path.open

    def injected(path, observed):
        return path == source or original_link(path, observed)

    def monitor(path, *args, **kwargs):
        if path == source:
            raise AssertionError("unsafe fixture must not be opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(packet_cli, "_is_link", injected)
    monkeypatch.setattr(Path, "open", monitor)
    assert main(args(home, source)) == 2
    assert capsys.readouterr().out == ""
    assert not home.exists()


def test_existing_non_fixture_home_is_rejected_byte_for_byte(tmp_path, capsys):
    home = tmp_path / "ordinary"
    init(home)
    database = home / "market.duckdb"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    source = tmp_path / "fixture.json"
    write_fixture(source)
    assert main(args(home, source)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before

