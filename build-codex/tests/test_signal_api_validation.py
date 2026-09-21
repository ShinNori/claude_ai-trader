"""Signal API validates strategy names before any database side effect."""
import hashlib
from datetime import date

import pytest

from aitrader import api, packet_cli


AS_OF = date(2026, 8, 28)


def _files(home):
    return {
        path.relative_to(home).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
        )
        for path in home.rglob("*") if path.is_file()
    }


def _run(entry, home, strategy):
    return (api.run_signals(home, strategy, AS_OF) if entry == "api"
            else packet_cli.generate(home, strategy, AS_OF))


@pytest.mark.parametrize("entry", ["api", "packet"])
def test_unknown_strategy_does_not_create_missing_home(tmp_path, monkeypatch, entry):
    home = tmp_path / "absent"
    monkeypatch.setattr(
        api.db, "connect",
        lambda *_args, **_kwargs: pytest.fail("unknown strategy reached db.connect"),
    )
    monkeypatch.setattr(
        packet_cli, "connect",
        lambda *_args, **_kwargs: pytest.fail("unknown strategy reached db.connect"),
    )

    with pytest.raises(ValueError, match="Unknown strategy"):
        _run(entry, home, "unknown-strategy")

    assert not home.exists()


@pytest.mark.parametrize("entry", ["api", "packet"])
def test_unknown_strategy_leaves_existing_database_unchanged(tmp_path, monkeypatch, entry):
    home = tmp_path / "existing"
    api.init_db(home)
    before = _files(home)
    monkeypatch.setattr(
        api.db, "connect",
        lambda *_args, **_kwargs: pytest.fail("unknown strategy reached db.connect"),
    )
    monkeypatch.setattr(
        packet_cli, "connect",
        lambda *_args, **_kwargs: pytest.fail("unknown strategy reached db.connect"),
    )

    with pytest.raises(ValueError, match="Unknown strategy"):
        _run(entry, home, "unknown-strategy")

    assert _files(home) == before


@pytest.mark.parametrize("entry", ["api", "packet"])
def test_known_strategy_retains_public_signal_behavior(tmp_path, entry):
    home = tmp_path / "synthetic"
    api.load_synthetic(home, seed=42)

    result = _run(entry, home, "margin_bucket_long")

    assert isinstance(result, list)
    assert result
    assert all(isinstance(candidate, dict) for candidate in result)
    assert all(candidate["strategy"] == "margin_bucket_long" for candidate in result)
    assert all(candidate["as_of"] == AS_OF for candidate in result)


@pytest.mark.parametrize("entry", ["api", "packet"])
def test_known_strategy_is_constructed_once(tmp_path, monkeypatch, entry):
    home = tmp_path / "synthetic"
    api.load_synthetic(home, seed=42)
    original = api.get_strategy
    calls = []

    def observed(name):
        calls.append(name)
        return original(name)

    monkeypatch.setattr(api, "get_strategy", observed)

    _run(entry, home, "margin_bucket_long")

    assert calls == ["margin_bucket_long"]
