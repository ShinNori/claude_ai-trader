"""CSV 約定取込パーサ（Claude 単独ビルド）

`ledger-import-csv` で使う CSV パーサ。UTF-8 (BOM 有無問わず) / CP932 に対応し、
日本語ヘッダ・英語ヘッダの両方を受け付ける。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import JST

_HEADER_MAP = {
    "event_id": "event_id", "約定id": "event_id", "注文番号": "event_id",
    "proposal_id": "proposal_id", "提案id": "proposal_id",
    "code": "code", "銘柄コード": "code", "コード": "code",
    "side": "side", "売買": "side",
    "qty": "qty", "数量": "qty", "約定数量": "qty",
    "price": "price", "単価": "price", "約定単価": "price",
    "fee": "fee", "手数料": "fee",
    "at": "at", "約定日時": "at", "日時": "at",
    "broker_order_id": "broker_order_id", "証券注文id": "broker_order_id", "注文id": "broker_order_id",
}

_SIDE_MAP = {
    "buy": "BUY", "買": "BUY", "買付": "BUY",
    "sell": "SELL", "売": "SELL", "売却": "SELL",
}


@dataclass
class CsvRow:
    event_id: str
    proposal_id: str | None
    code: str
    side: str
    qty: int
    price: float
    fee: float
    at: datetime
    source: str = "csv"
    broker_order_id: str | None = None
    derived_event_id: bool = False


def _clean_number(s: str) -> str:
    return s.strip().replace(",", "").replace("円", "").replace("株", "").strip()


def _parse_at(s: str, default_tz) -> datetime:
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y/%m/%d %H:%M")
        except ValueError as e:
            raise ValueError(f"日時の形式が不正: {s!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp932")


def parse_fills_csv(path: Path, *, default_tz=JST) -> list[CsvRow]:
    import csv
    import io

    text = _read_text(Path(path))
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    cols = []
    for h in header:
        key = _HEADER_MAP.get(h)
        cols.append(key)

    out: list[CsvRow] = []
    for i, raw_row in enumerate(rows[1:], start=2):  # 1-indexed + header
        if not raw_row or all(not c.strip() for c in raw_row):
            continue
        try:
            d = {}
            for key, val in zip(cols, raw_row):
                if key is None:
                    continue
                d[key] = val
            if "code" not in d or not d["code"].strip():
                raise ValueError("code が必須")
            if "side" not in d or not d["side"].strip():
                raise ValueError("side が必須")
            if "qty" not in d or not d["qty"].strip():
                raise ValueError("qty が必須")
            if "price" not in d or not d["price"].strip():
                raise ValueError("price が必須")
            if "at" not in d or not d["at"].strip():
                raise ValueError("at が必須")

            code = d["code"].strip()
            side_raw = d["side"].strip().lower()
            side = _SIDE_MAP.get(side_raw)
            if side is None:
                side = _SIDE_MAP.get(d["side"].strip(), None)
            if side is None:
                raise ValueError(f"side の値が不正: {d['side']!r}")
            qty = int(_clean_number(d["qty"]))
            price = float(_clean_number(d["price"]))
            fee = float(_clean_number(d["fee"])) if d.get("fee", "").strip() else 0.0
            at = _parse_at(d["at"], default_tz)
            broker_order_id = d.get("broker_order_id", "").strip() or None
            proposal_id = d.get("proposal_id", "").strip() or None

            event_id = d.get("event_id", "").strip() or None
            derived = False
            if not event_id:
                basis = f"{code}|{side}|{qty}|{price}|{at.isoformat()}|{broker_order_id or ''}"
                event_id = hashlib.sha1(basis.encode("utf-8")).hexdigest()
                derived = True

            out.append(CsvRow(
                event_id=event_id, proposal_id=proposal_id, code=code, side=side,
                qty=qty, price=price, fee=fee, at=at, source="csv",
                broker_order_id=broker_order_id, derived_event_id=derived,
            ))
        except Exception as e:
            raise ValueError(f"{i}行目が不正: {e}") from e
    return out
