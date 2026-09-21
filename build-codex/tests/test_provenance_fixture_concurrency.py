"""A real second Python process cannot join an in-progress fixture ingest."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from aitrader.db import connect


FIRST_WRITER = r'''
import json, sys, time
from datetime import date, datetime, timezone
from pathlib import Path
import aitrader.provenance_fixture as module

home, fixture_path, ready, release = map(Path, sys.argv[1:])
fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
original = module._insert_rows
paused = False
def pause(con, table, rows):
    global paused
    if not paused:
        paused = True
        ready.write_text('transaction-open', encoding='utf-8')
        deadline = time.monotonic() + 12
        while not release.exists():
            if time.monotonic() > deadline:
                raise RuntimeError('barrier timeout')
            time.sleep(.025)
    return original(con, table, rows)
module._insert_rows = pause
result = module.ingest_legacy_fixture_with_provenance(
    home, start=date(2026,8,31), end=date(2026,8,31),
    recorded_at=datetime(2026,9,1,12,tzinfo=timezone.utc), fixture=fixture)
print(json.dumps(result, sort_keys=True))
'''


def _fixture():
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


def _cli(home, fixture_path):
    return [sys.executable, "-m", "aitrader.provenance_fixture_cli", "ingest",
            "--home", str(home), "--fixture", str(fixture_path),
            "--from", "2026-08-31", "--to", "2026-08-31",
            "--recorded-at", "2026-09-01T12:00:00+00:00"]


def test_concurrent_cli_fails_closed_then_retries_as_no_op(tmp_path):
    secret = "SECRET_CONCURRENT_FIXTURE_7341"
    script = tmp_path / "first_writer.py"
    script.write_text(FIRST_WRITER, encoding="utf-8")
    fixture_path = tmp_path / f"{secret}.json"
    fixture_path.write_text(json.dumps(_fixture()), encoding="utf-8")
    home = tmp_path / "home"
    ready, release = tmp_path / "ready", tmp_path / "release"
    package = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(package) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    first = subprocess.Popen(
        [sys.executable, str(script), str(home), str(fixture_path),
         str(ready), str(release)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    first_stdout = first_stderr = b""
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(.025)
        assert ready.exists(), "first writer did not reach the transaction barrier"

        second = subprocess.run(
            _cli(home, fixture_path), env=env, capture_output=True, timeout=10
        )
        assert second.returncode == 2
        assert second.stdout == b""
        decoded_error = second.stderr.decode("utf-8", errors="replace")
        assert decoded_error.startswith("仮データ専用")
        assert secret not in decoded_error
        assert str(fixture_path) not in decoded_error
    finally:
        release.write_text("continue", encoding="utf-8")
        try:
            first_stdout, first_stderr = first.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            first.kill()
            first_stdout, first_stderr = first.communicate()
            raise AssertionError("first writer did not terminate after barrier release")

    assert first.returncode == 0, first_stderr.decode("utf-8", errors="replace")
    completed = json.loads(first_stdout)
    assert completed["result"] == "COMPLETED"

    retry = subprocess.run(_cli(home, fixture_path), env=env,
                           capture_output=True, timeout=10)
    assert retry.returncode == 0, retry.stderr.decode("utf-8", errors="replace")
    no_op = json.loads(retry.stdout)
    assert no_op["result"] == "NO_OP"
    assert no_op["run_id"] == completed["run_id"]
    with connect(home) as con:
        assert con.execute("SELECT count(*) FROM market_ingest_runs").fetchone()[0] == 1
        assert con.execute(
            "SELECT count(DISTINCT run_id) FROM market_row_provenance"
        ).fetchone()[0] == 1

