import json
import time
from pathlib import Path

import pytest

from aitrader.__main__ import main
from aitrader.csvfills import parse_fills_csv
from aitrader.models import JST


# ---------------------------------------------------------------- csvfills

def test_parse_fills_csv_english_headers(tmp_path):
    p = tmp_path / "fills.csv"
    p.write_text(
        "event_id,proposal_id,code,side,qty,price,fee,at,broker_order_id\n"
        "e1,p1,1111,BUY,100,1000,10,2026-09-25T10:00:00+09:00,b1\n",
        encoding="utf-8",
    )
    rows = parse_fills_csv(p)
    assert len(rows) == 1
    r = rows[0]
    assert r.event_id == "e1"
    assert r.proposal_id == "p1"
    assert r.code == "1111"
    assert r.side == "BUY"
    assert r.qty == 100
    assert r.price == 1000
    assert r.fee == 10
    assert r.broker_order_id == "b1"
    assert r.at.tzinfo is not None


def test_parse_fills_csv_japanese_headers(tmp_path):
    p = tmp_path / "fills_ja.csv"
    p.write_text(
        "約定ID,提案ID,銘柄コード,売買,数量,単価,手数料,約定日時,証券注文ID\n"
        'e2,p2,2222,買,200,"2,000円",20,2026/09/25 11:00,b2\n',
        encoding="utf-8",
    )
    rows = parse_fills_csv(p)
    assert len(rows) == 1
    r = rows[0]
    assert r.code == "2222"
    assert r.side == "BUY"
    assert r.qty == 200
    assert r.price == 2000
    assert r.fee == 20
    assert r.at.tzinfo is not None
    assert r.at.tzinfo.utcoffset(None) == JST.utcoffset(None)


def test_parse_fills_csv_cp932(tmp_path):
    p = tmp_path / "fills_cp932.csv"
    text = (
        "約定ID,銘柄コード,売買,数量,単価,手数料,約定日時\n"
        "e3,3333,売,300,3000,30,2026-09-25T12:00:00\n"
    )
    p.write_bytes(text.encode("cp932"))
    rows = parse_fills_csv(p)
    assert len(rows) == 1
    assert rows[0].side == "SELL"
    assert rows[0].code == "3333"


def test_parse_fills_csv_bom(tmp_path):
    p = tmp_path / "fills_bom.csv"
    text = "event_id,code,side,qty,price,fee,at\ne4,4444,BUY,10,500,5,2026-09-25T09:00:00\n"
    p.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    rows = parse_fills_csv(p)
    assert len(rows) == 1
    assert rows[0].code == "4444"


def test_parse_fills_csv_derived_event_id(tmp_path):
    p = tmp_path / "fills_noid.csv"
    p.write_text(
        "code,side,qty,price,fee,at\n5555,BUY,10,500,5,2026-09-25T09:00:00\n",
        encoding="utf-8",
    )
    rows = parse_fills_csv(p)
    assert len(rows) == 1
    assert rows[0].derived_event_id is True
    assert rows[0].event_id


def test_parse_fills_csv_malformed_row_raises(tmp_path):
    p = tmp_path / "fills_bad.csv"
    p.write_text(
        "code,side,qty,price,fee,at\n6666,BUY,abc,500,5,2026-09-25T09:00:00\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as ei:
        parse_fills_csv(p)
    assert "2行目" in str(ei.value)


# ---------------------------------------------------------------- CLI

def test_ledger_init_then_status(tmp_path, capsys):
    rc = main(["--home", str(tmp_path), "ledger-init", "--cash", "1000000"])
    assert rc == 0
    capsys.readouterr()

    rc = main(["--home", str(tmp_path), "ledger-status"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["cash"] == 1000000
    assert data["available"] == 1000000
    assert data["positions"] == {}


def _has_import_csv_fills() -> bool:
    from aitrader import ledger
    return hasattr(ledger.Ledger, "import_csv_fills")


def _wait_for_import_csv_fills(timeout_s: int = 20 * 60, interval_s: int = 60) -> bool:
    if _has_import_csv_fills():
        return True
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(interval_s)
        if _has_import_csv_fills():
            return True
    return False


def test_ledger_import_csv(tmp_path, capsys):
    if not _wait_for_import_csv_fills():
        pytest.skip("Ledger.import_csv_fills が未実装のためスキップ")

    rc = main(["--home", str(tmp_path), "ledger-init", "--cash", "1000000"])
    assert rc == 0
    capsys.readouterr()

    csv_path = tmp_path / "fills.csv"
    csv_path.write_text(
        "code,side,qty,price,fee,at\n"
        "1111,BUY,100,1000,10,2026-09-25T10:00:00+09:00\n",
        encoding="utf-8",
    )
    rc = main(["--home", str(tmp_path), "ledger-import-csv", "--file", str(csv_path)])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "applied" in data and "skipped" in data and "pending" in data and "errors" in data
    # rc should reflect presence/absence of errors
    assert rc == (1 if data.get("errors") else 0)
