"""Bounded offline storage for history/validity binding v2 bundles."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from .db import default_home, _runtime_path_guard
from .history_validity_binding_v2 import (
    MODE as API_MODE,
    ORIGIN,
    inspect_history_validity_binding,
)
from .packet_cli import _is_link, _mapping, _unique_object


MODE = "evidence_bundle_store_v1"
STORE_DIRECTORY = "evidence_bundles"
RECORD_SUFFIX = ".bundle.json"
MAX_BUNDLE_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024
PUT_READ_ATTEMPTS = 3
PUT_READ_RETRY_SECONDS = 0.005

_ID = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_RECORD_KEYS = {
    "store_format", "bundle_sha256", "bundle_bytes", "bundle_base64",
    "api_mode", "output", "stored_at",
}
_NO_PRECOMPUTED_OUTPUT = object()


class _ReplaceFailed(Exception):
    """The final replace failed after the temporary record was complete."""


def _result(*, read_only, status="DATA_INCOMPLETE", reason=None,
            bundle_sha256=None, bundle_bytes=None, output=None,
            stored_at=None, record_path=None):
    return {
        "mode": MODE,
        "source_origin": ORIGIN,
        "status": status,
        "reason_codes": [] if reason is None else [reason],
        "bundle_sha256": bundle_sha256,
        "bundle_bytes": bundle_bytes,
        "bundle_status": None if output is None else output["status"],
        "bundle_reason_codes": [] if output is None else output["reason_codes"],
        "stored_at": stored_at,
        "record_path": record_path,
        "ready_for_live": False,
        "current_signal": False,
        "read_only": read_only,
    }


def _store_directory(home):
    root = default_home() if home is None else Path(home)
    return _runtime_path_guard(root / STORE_DIRECTORY)


def _read_regular_bytes(path, limit):
    """Read one stable, non-link regular file without resolving its path."""
    path = Path(path).absolute()
    for component in reversed((path, *path.parents)):
        try:
            observed = component.lstat()
        except FileNotFoundError:
            continue
        if _is_link(component, observed):
            raise ValueError("link/reparse point is not permitted")
    before = path.lstat()
    if (_is_link(path, before) or not stat.S_ISREG(before.st_mode)
            or before.st_size > limit):
        raise ValueError("bounded regular file required")
    with path.open("rb") as stream:
        opened_before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened_before.st_mode)
                or (opened_before.st_dev, opened_before.st_ino,
                    opened_before.st_mode)
                != (before.st_dev, before.st_ino, before.st_mode)):
            raise ValueError("file changed while being observed")
        body = stream.read(limit + 1)
        opened_after = os.fstat(stream.fileno())
    after = path.lstat()
    if (len(body) > limit or _is_link(path, after)
            or len(body) != before.st_size
            or (after.st_dev, after.st_ino, after.st_mode, after.st_size,
                after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_mode, before.st_size,
                before.st_mtime_ns)
            or (opened_after.st_dev, opened_after.st_ino,
                opened_after.st_mode, opened_after.st_size,
                opened_after.st_mtime_ns)
            != (opened_before.st_dev, opened_before.st_ino,
                opened_before.st_mode, opened_before.st_size,
                opened_before.st_mtime_ns)):
        raise ValueError("file changed while being observed")
    return body


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def _mapping_bytes(body):
    value = json.loads(
        body.decode("utf-8-sig"), object_pairs_hook=_unique_object,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {token}")),
    )
    if not isinstance(value, dict):
        raise ValueError("top-level JSON value must be an object")
    _finite(value)
    return value


def _snapshot_mapping(directory, body):
    """Parse exactly ``body`` through the shared closed mapping reader."""
    descriptor = None
    snapshot = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=".bundle-input-", suffix=".tmp", dir=directory)
        snapshot = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        return _mapping(snapshot)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if snapshot is not None:
            try:
                snapshot.unlink()
            except OSError:
                pass


def _valid_timestamp(value):
    if type(value) is not str:
        return False
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return instant.tzinfo is not None and instant.utcoffset() == timezone.utc.utcoffset(instant)
    except (TypeError, ValueError, OverflowError):
        return False


def _typed_equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (set(left) == set(right)
                and all(_typed_equal(left[key], right[key]) for key in left))
    if isinstance(left, list):
        return (len(left) == len(right)
                and all(_typed_equal(a, b) for a, b in zip(left, right)))
    return left == right


def _record_path(identifier):
    return f"{STORE_DIRECTORY}/{identifier}{RECORD_SUFFIX}"


def _verify(directory, identifier, expected_output=_NO_PRECOMPUTED_OUTPUT):
    """Internal verify returning decoded bytes for collision checking."""
    known = identifier if _ID.fullmatch(identifier) is not None else None
    if known is None:
        return _result(read_only=True, reason="RECORD_UNREADABLE"), None
    path = directory / f"{identifier}{RECORD_SUFFIX}"
    try:
        body = _read_regular_bytes(path, MAX_RECORD_BYTES)
    except FileNotFoundError:
        return _result(
            read_only=True, reason="RECORD_NOT_FOUND", bundle_sha256=known), None
    except (OSError, TypeError, ValueError):
        return _result(
            read_only=True, reason="RECORD_UNREADABLE", bundle_sha256=known), None
    try:
        record = _mapping_bytes(body)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError,
            OverflowError, RecursionError):
        return _result(
            read_only=True, reason="RECORD_UNREADABLE", bundle_sha256=known), None
    if set(record) != _RECORD_KEYS or record.get("store_format") != MODE:
        return _result(
            read_only=True, reason="UNSUPPORTED_FORMAT", bundle_sha256=known), None
    encoded = record.get("bundle_base64")
    size = record.get("bundle_bytes")
    field_hash = record.get("bundle_sha256")
    if (type(encoded) is not str or type(size) is not int or isinstance(size, bool)
            or size < 0 or size > MAX_BUNDLE_BYTES
            or type(field_hash) is not str or _ID.fullmatch(field_hash) is None
            or not _valid_timestamp(record.get("stored_at"))):
        return _result(
            read_only=True, reason="RECORD_CORRUPT", bundle_sha256=known), None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return _result(
            read_only=True, reason="RECORD_CORRUPT", bundle_sha256=known), None
    digest = hashlib.sha256(raw).hexdigest()
    if (base64.b64encode(raw).decode("ascii") != encoded
            or len(raw) != size or digest != identifier or field_hash != identifier):
        return _result(
            read_only=True, reason="RECORD_CORRUPT",
            bundle_sha256=known, bundle_bytes=size), None
    try:
        bundle = _mapping_bytes(raw)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError,
            OverflowError, RecursionError):
        return _result(
            read_only=True, reason="RECORD_CORRUPT",
            bundle_sha256=known, bundle_bytes=size), None
    if record.get("api_mode") != API_MODE:
        return _result(
            read_only=True, reason="UNSUPPORTED_FORMAT",
            bundle_sha256=known, bundle_bytes=size), raw
    saved_output = record.get("output")
    output = (inspect_history_validity_binding(bundle)
              if expected_output is _NO_PRECOMPUTED_OUTPUT
              else expected_output)
    common = dict(
        read_only=True, bundle_sha256=known, bundle_bytes=size, output=output,
        stored_at=record["stored_at"], record_path=_record_path(identifier),
    )
    if not _typed_equal(output, saved_output):
        return _result(reason="OUTPUT_MISMATCH", **common), raw
    return _result(status="REPRODUCED", **common), raw


def _verify_for_put(directory, identifier,
                    expected_output=_NO_PRECOMPUTED_OUTPUT):
    """Retry only a transient record read failure during put."""
    for attempt in range(PUT_READ_ATTEMPTS):
        checked, saved_raw = _verify(directory, identifier, expected_output)
        if checked["reason_codes"] != ["RECORD_UNREADABLE"]:
            return checked, saved_raw
        if attempt + 1 < PUT_READ_ATTEMPTS:
            time.sleep(PUT_READ_RETRY_SECONDS)
    return checked, saved_raw


def verify(bundle_sha256, home=None):
    """Re-evaluate one stored record without changing storage."""
    if type(bundle_sha256) is not str or _ID.fullmatch(bundle_sha256) is None:
        return _result(read_only=True, reason="RECORD_UNREADABLE")
    try:
        directory = _store_directory(home)
    except (OSError, TypeError, ValueError):
        return _result(
            read_only=True, reason="STORE_PATH_INVALID",
            bundle_sha256=bundle_sha256)
    return _verify(directory, bundle_sha256)[0]


def _write_record(directory, identifier, record):
    rendered = (json.dumps(
        record, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":")) + "\n").encode("utf-8")
    if len(rendered) > MAX_RECORD_BYTES:
        raise ValueError("record exceeds 2 MiB")
    descriptor = None
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f"{identifier}{RECORD_SUFFIX}.", suffix=".tmp", dir=directory)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, directory / f"{identifier}{RECORD_SUFFIX}")
        except Exception as error:
            raise _ReplaceFailed() from error
    except BaseException:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise


def put(input_path, home=None):
    """Store the exact bytes of one bounded offline bundle."""
    try:
        directory = _store_directory(home)
    except (OSError, TypeError, ValueError):
        return _result(read_only=False, reason="STORE_PATH_INVALID")
    try:
        raw = _read_regular_bytes(input_path, MAX_BUNDLE_BYTES)
    except (OSError, TypeError, ValueError):
        return _result(read_only=False, reason="INPUT_UNREADABLE")
    identifier = hashlib.sha256(raw).hexdigest()
    size = len(raw)
    try:
        parsed_raw = _mapping_bytes(raw)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError,
            OverflowError, RecursionError):
        return _result(
            read_only=False, reason="INPUT_UNREADABLE",
            bundle_sha256=identifier, bundle_bytes=size)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        directory = _runtime_path_guard(directory)
    except (OSError, TypeError, ValueError):
        return _result(
            read_only=False, reason="WRITE_FAILED",
            bundle_sha256=identifier, bundle_bytes=size)
    try:
        bundle = _snapshot_mapping(directory, raw)
    except (OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError,
            OverflowError, RecursionError):
        return _result(
            read_only=False, reason="WRITE_FAILED",
            bundle_sha256=identifier, bundle_bytes=size)
    if not _typed_equal(bundle, parsed_raw):
        return _result(
            read_only=False, reason="INPUT_UNREADABLE",
            bundle_sha256=identifier, bundle_bytes=size)
    final = directory / f"{identifier}{RECORD_SUFFIX}"
    try:
        final.lstat()
        exists = True
    except FileNotFoundError:
        exists = False
    except OSError:
        return _result(
            read_only=False, reason="RECORD_CORRUPT",
            bundle_sha256=identifier, bundle_bytes=size)
    if exists:
        checked, saved_raw = _verify_for_put(directory, identifier)
        if checked["status"] != "REPRODUCED" or saved_raw != raw:
            return _result(
                read_only=False, reason="RECORD_CORRUPT",
                bundle_sha256=identifier, bundle_bytes=size)
        return _result(
            read_only=False, status="NO_OP", bundle_sha256=identifier,
            bundle_bytes=size,
            output={
                "status": checked["bundle_status"],
                "reason_codes": checked["bundle_reason_codes"],
            }, stored_at=checked["stored_at"],
            record_path=_record_path(identifier))
    output = inspect_history_validity_binding(bundle)
    stored_at = datetime.now(timezone.utc).isoformat()
    record = {
        "store_format": MODE,
        "bundle_sha256": identifier,
        "bundle_bytes": size,
        "bundle_base64": base64.b64encode(raw).decode("ascii"),
        "api_mode": API_MODE,
        "output": output,
        "stored_at": stored_at,
    }
    try:
        _write_record(directory, identifier, record)
    except _ReplaceFailed:
        checked, saved_raw = _verify_for_put(
            directory, identifier, expected_output=output)
        if checked["status"] == "REPRODUCED" and saved_raw == raw:
            return _result(
                read_only=False, status="NO_OP", bundle_sha256=identifier,
                bundle_bytes=size, output=output,
                stored_at=checked["stored_at"],
                record_path=_record_path(identifier))
        return _result(
            read_only=False, reason="WRITE_FAILED",
            bundle_sha256=identifier, bundle_bytes=size, output=output)
    except Exception:
        return _result(
            read_only=False, reason="WRITE_FAILED",
            bundle_sha256=identifier, bundle_bytes=size, output=output)
    return _result(
        read_only=False, status="STORED", bundle_sha256=identifier,
        bundle_bytes=size, output=output, stored_at=stored_at,
        record_path=_record_path(identifier))
