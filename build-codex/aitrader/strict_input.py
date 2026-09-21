"""Pure, offline validation for the isolated strict-input-v1 bundle."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any


MODE = "strict_input_v1"
ORIGIN = "offline_fixture"
GROUPS = ("prices", "publications", "lots", "events")
_HEX = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _resolve(payload: Any, pointer: Any) -> Any:
    if not isinstance(pointer, str):
        raise ValueError
    if pointer == "":
        return payload
    if not pointer.startswith("/"):
        raise ValueError
    current = payload
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if "~" in raw.replace("~0", "").replace("~1", ""):
            raise ValueError
        if isinstance(current, dict):
            if token not in current:
                raise ValueError
            current = current[token]
        elif isinstance(current, list):
            if not token.isascii() or not token.isdigit() or (len(token) > 1 and token[0] == "0"):
                raise ValueError
            index = int(token)
            if index >= len(current):
                raise ValueError
            current = current[index]
        else:
            raise ValueError
    return current


def _finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _price(record: Any, code: str, as_of: datetime, reasons: set[str]) -> None:
    keys = {"code", "price_at", "selected_basis", "raw", "adjusted",
            "adjustment_contract"}
    if not isinstance(record, dict) or set(record) != keys or record.get("code") != code:
        reasons.add("SOURCE_RECORD_INVALID")
        return
    price_at = _aware(record["price_at"])
    if price_at is None:
        reasons.add("SOURCE_RECORD_INVALID")
    elif price_at > as_of:
        reasons.add("PRICE_AFTER_AS_OF")
    basis = record.get("selected_basis")
    raw = record.get("raw")
    adjusted = record.get("adjusted")
    fields = {"open", "high", "low", "close", "volume"}
    if not isinstance(raw, dict) or set(raw) != fields or not all(
            _finite_number(raw[name]) for name in fields):
        reasons.add("SOURCE_RECORD_INVALID")
    if adjusted is not None and (not isinstance(adjusted, dict) or set(adjusted) != fields):
        reasons.add("PRICE_ADJUSTMENT_INCOMPLETE")
    elif isinstance(adjusted, dict) and not all(_finite_number(adjusted[name]) for name in fields):
        reasons.add("PRICE_ADJUSTMENT_INCOMPLETE")
    contract = record.get("adjustment_contract")
    if basis not in ("RAW", "ADJUSTED"):
        reasons.add("PRICE_BASIS_INVALID")
    elif basis == "RAW":
        if adjusted is not None or contract is not None:
            reasons.add("PRICE_BASIS_MIXED")
    elif adjusted is None:
        reasons.add("PRICE_ADJUSTMENT_INCOMPLETE")
    elif not isinstance(contract, str) or not contract.strip():
        reasons.add("PRICE_BASIS_INVALID")


def _publication(record: Any, code: str, as_of: datetime, reasons: set[str]) -> None:
    if (not isinstance(record, dict) or
            set(record) != {"code", "publication_at", "estimated"} or
            record.get("code") != code):
        reasons.add("SOURCE_RECORD_INVALID")
        return
    if record.get("estimated") is not False:
        reasons.add("PUBLICATION_ESTIMATED")
    published = _aware(record.get("publication_at"))
    if published is None:
        reasons.add("SOURCE_RECORD_INVALID")
    elif published > as_of:
        reasons.add("PUBLICATION_AFTER_AS_OF")


def _lot(record: Any, code: str, reasons: set[str]) -> None:
    if (not isinstance(record, dict) or set(record) != {"code", "lot_size"} or
            record.get("code") != code):
        reasons.add("SOURCE_RECORD_INVALID")
        return
    lot = record.get("lot_size")
    if type(lot) is not int or lot <= 0:
        reasons.add("LOT_INVALID")


def _event(record: Any, code: str, reasons: set[str]) -> None:
    if (not isinstance(record, dict) or
            set(record) != {"code", "next_earnings_at", "margin_regulated"} or
            record.get("code") != code):
        reasons.add("SOURCE_RECORD_INVALID")
        return
    earnings = record.get("next_earnings_at", "UNKNOWN")
    regulated = record.get("margin_regulated", "UNKNOWN")
    if earnings == "UNKNOWN" or regulated == "UNKNOWN" or type(regulated) is not bool:
        reasons.add("EVENT_UNKNOWN")
    elif earnings is not None and _aware(earnings) is None:
        reasons.add("SOURCE_RECORD_INVALID")


def inspect_strict_input(bundle: object) -> dict:
    """Validate an in-memory bundle without I/O and return non-sensitive metadata."""
    reasons: set[str] = set()
    data = bundle if isinstance(bundle, dict) else {}
    if not isinstance(bundle, dict):
        reasons.add("INVALID_BUNDLE")
    elif set(data) != {"mode", "source_origin", "as_of", "expected_codes",
                       "source_documents", "sources"}:
        reasons.add("INVALID_BUNDLE")
    if data.get("mode") != MODE:
        reasons.add("INVALID_MODE")
    if data.get("source_origin") != ORIGIN:
        reasons.add("INVALID_ORIGIN")
    as_of = _aware(data.get("as_of"))
    if as_of is None:
        reasons.add("INVALID_AS_OF")

    expected_raw = data.get("expected_codes")
    expected: list[str] = []
    if (not isinstance(expected_raw, list) or not expected_raw or
            any(not isinstance(code, str) or not code.strip() or code != code.strip()
                for code in expected_raw) or len(set(expected_raw)) != len(expected_raw)):
        reasons.add("INVALID_EXPECTED_CODES")
    else:
        expected = expected_raw

    documents_raw = data.get("source_documents")
    documents: dict[str, Any] = {}
    if not isinstance(documents_raw, list) or not documents_raw:
        reasons.add("INVALID_DOCUMENTS")
    else:
        for document in documents_raw:
            if (not isinstance(document, dict) or set(document) != {"id", "sha256", "payload"} or
                    not isinstance(document.get("id"), str) or not document["id"].strip() or
                    document["id"] in documents or not isinstance(document.get("sha256"), str) or
                    not _HEX.fullmatch(document["sha256"])):
                reasons.add("INVALID_DOCUMENTS")
                continue
            try:
                actual = hashlib.sha256(_canonical(document["payload"])).hexdigest()
            except (TypeError, ValueError, OverflowError, RecursionError):
                reasons.add("INVALID_DOCUMENTS")
                continue
            documents[document["id"]] = document["payload"]
            if actual != document["sha256"]:
                reasons.add("DOCUMENT_HASH_MISMATCH")

    sources = data.get("sources")
    if not isinstance(sources, dict) or set(sources) != set(GROUPS):
        reasons.add("INVALID_SOURCES")
        sources = {}
    validators = {"prices": _price, "publications": _publication,
                  "lots": _lot, "events": _event}
    used_references: set[tuple[str, str]] = set()
    for group in GROUPS:
        selections = sources.get(group)
        if not isinstance(selections, dict):
            reasons.add("INVALID_SOURCES")
            continue
        if set(selections) != set(expected):
            reasons.add("SOURCE_SET_MISMATCH")
        for code in expected:
            reference = selections.get(code)
            if (not isinstance(reference, dict) or
                    set(reference) != {"document_id", "pointer"} or
                    not isinstance(reference.get("document_id"), str) or
                    reference.get("document_id") not in documents or
                    not isinstance(reference.get("pointer"), str)):
                reasons.add("SOURCE_REFERENCE_INVALID")
                continue
            identity = (reference["document_id"], reference["pointer"])
            if identity in used_references:
                reasons.add("SOURCE_REFERENCE_DUPLICATE")
                continue
            used_references.add(identity)
            try:
                record = _resolve(documents[reference["document_id"]], reference["pointer"])
            except (ValueError, TypeError, OverflowError):
                reasons.add("SOURCE_REFERENCE_INVALID")
                continue
            if as_of is None:
                continue
            if group == "prices":
                _price(record, code, as_of, reasons)
            elif group == "publications":
                _publication(record, code, as_of, reasons)
            else:
                validators[group](record, code, reasons)

    return {
        "mode": MODE,
        "status": "DATA_INCOMPLETE" if reasons else "VERIFIED_OFFLINE_INPUT",
        "source_origin": ORIGIN,
        "as_of": as_of.isoformat() if as_of is not None else None,
        "expected_code_count": len(expected),
        "source_document_count": len(documents),
        "reason_codes": sorted(reasons),
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }
