"""CLI boundaries for the dedicated strict-input-v1 offline inspection mode."""
import hashlib
import json

from aitrader.strict_input_cli import main


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _bundle():
    payload = {
        "prices": [{"code": "6857", "price_at": "2026-09-12T06:45:00+09:00",
                    "selected_basis": "RAW",
                    "raw": {"open": 1000, "high": 1050, "low": 990,
                            "close": 1020, "volume": 120000},
                    "adjusted": None, "adjustment_contract": None}],
        "publications": [{"code": "6857", "publication_at": "2026-09-12T06:30:00+09:00",
                          "estimated": False}],
        "lots": [{"code": "6857", "lot_size": 100}],
        "events": [{"code": "6857", "next_earnings_at": None,
                    "margin_regulated": False}],
    }
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return {
        "mode": "strict_input_v1", "source_origin": "offline_fixture",
        "as_of": "2026-09-12T07:00:00+09:00", "expected_codes": ["6857"],
        "source_documents": [{"id": "fixture-doc", "sha256": digest, "payload": payload}],
        "sources": {group: {"6857": {"document_id": "fixture-doc",
                                       "pointer": f"/{group}/0"}}
                    for group in ("prices", "publications", "lots", "events")},
    }


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_valid_fixture_returns_offline_metadata_only(tmp_path, capsys):
    source = tmp_path / "strict-input.json"
    _write(source, _bundle())
    before = source.read_bytes()

    assert main(["--input", str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result == {
        "mode": "strict_input_v1", "status": "VERIFIED_OFFLINE_INPUT",
        "source_origin": "offline_fixture", "as_of": "2026-09-12T07:00:00+09:00",
        "expected_code_count": 1, "source_document_count": 1, "reason_codes": [],
        "ready_for_live": False, "current_signal": False, "read_only": True,
    }
    assert source.read_bytes() == before


def test_structurally_readable_but_incomplete_fixture_exits_two(tmp_path, capsys):
    source = tmp_path / "incomplete.json"
    value = _bundle()
    del value["sources"]["prices"]
    _write(source, value)

    assert main(["--input", str(source)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"]
    assert result["read_only"] is True
    assert result["ready_for_live"] is False
    assert result["current_signal"] is False


def test_invalid_json_oversize_and_missing_input_use_fixed_error(tmp_path, capsys):
    secret = "SECRET_STRICT_INPUT_8421"
    malformed = tmp_path / f"{secret}.json"
    malformed.write_text('{"private":"' + secret, encoding="utf-8")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * (1024 * 1024) + b"}")

    for argv in (["--input", str(malformed)], ["--input", str(oversized)], []):
        assert main(argv) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "専用入力の検査に失敗しました。入力形式と原本の対応を確認してください。\n"
        assert secret not in captured.err
        assert str(malformed) not in captured.err


def test_inspection_does_not_create_or_change_existing_trading_database(tmp_path, capsys):
    source = tmp_path / "strict-input.json"
    _write(source, _bundle())
    database = tmp_path / "ledger.sqlite"
    database.write_bytes(b"existing-database-sentinel")
    before = (database.read_bytes(), database.stat().st_mtime_ns)

    assert main(["--input", str(source)]) == 0
    capsys.readouterr()
    assert (database.read_bytes(), database.stat().st_mtime_ns) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["ledger.sqlite", "strict-input.json"]
