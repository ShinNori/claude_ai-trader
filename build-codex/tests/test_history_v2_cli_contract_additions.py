"""Additional v2 CLI checks derived from the published contract proposal."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import aitrader.evidence_history_fixture_v2_cli as history_cli
import aitrader.history_validity_binding_v2_cli as binding_cli


ROOT = Path(__file__).parents[1]


def _run(module: str, source: Path):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", module, "--input", str(source)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _utc(value: str) -> str:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_history_utc_spelling_preserves_cli_digest(tmp_path):
    original = json.loads(
        (ROOT / "examples/evidence_history_valid.json").read_text(encoding="utf-8")
    )
    original["mode"] = "evidence_history_fixture_v2"
    respelled = deepcopy(original)
    respelled["decision_at"] = _utc(respelled["decision_at"])
    for entry in respelled["entries"]:
        entry["observed_at"] = _utc(entry["observed_at"])
        entry["recorded_at"] = _utc(entry["recorded_at"])

    outputs = []
    for name, value in (("original", original), ("utc", respelled)):
        source = tmp_path / f"{name}.json"
        source.write_text(json.dumps(value), encoding="utf-8")
        completed = _run("aitrader.evidence_history_fixture_v2_cli", source)
        assert completed.returncode == 0
        assert completed.stderr == ""
        outputs.append(json.loads(completed.stdout))

    assert outputs[0]["selection_sha256"] == outputs[1]["selection_sha256"]
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize(
    "module,example,reason",
    [
        (
            "aitrader.evidence_history_fixture_v2_cli",
            "evidence_history_valid.json",
            "INVALID_MODE",
        ),
        (
            "aitrader.history_validity_binding_v2_cli",
            "history_validity_binding_valid.json",
            "INVALID_BUNDLE",
        ),
    ],
)
def test_v1_example_reaches_v2_api_and_is_rejected_as_json(
    module, example, reason
):
    completed = _run(module, ROOT / "examples" / example)
    assert completed.returncode == 2
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["reason_codes"] == [reason]
    assert completed.stdout.count("\n") == 1


@pytest.mark.parametrize("cli", [history_cli, binding_cli])
def test_non_mapping_top_level_uses_fixed_error_without_api_call(
    tmp_path, capsys, monkeypatch, cli
):
    api_name = (
        "inspect_evidence_history"
        if cli is history_cli
        else "inspect_history_validity_binding"
    )
    monkeypatch.setattr(
        cli, api_name, lambda value: pytest.fail(f"API called with {value!r}")
    )
    source = tmp_path / "list.json"
    source.write_text("[]", encoding="utf-8")
    assert cli.main(["--input", str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == cli._ERROR + "\n"


@pytest.mark.parametrize("cli", [history_cli, binding_cli])
def test_symlink_input_uses_fixed_error_without_api_call(
    tmp_path, capsys, monkeypatch, cli
):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    source = tmp_path / "link.json"
    try:
        source.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable on this Windows host: {exc}")
    api_name = (
        "inspect_evidence_history"
        if cli is history_cli
        else "inspect_history_validity_binding"
    )
    monkeypatch.setattr(
        cli, api_name, lambda value: pytest.fail(f"API called with {value!r}")
    )
    assert cli.main(["--input", str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == cli._ERROR + "\n"
