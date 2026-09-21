"""Offline shape-only contracts for proposed J-Quants V2 pages."""
import copy
import math
import socket
import traceback
from pathlib import Path

import duckdb
import pytest

from aitrader.jquants_v2_contract import inspect_v2_page


ROWS = {
    "prices": {"Date": "2026-08-31", "Code": "13000", "O": 100,
               "H": 110.5, "L": 90, "C": 105, "Vo": 10000, "Va": 1000000,
               "AdjFactor": 1.0, "AdjO": 100, "AdjH": 110.5, "AdjL": 90,
               "AdjC": 105, "AdjVo": 10000},
    "margin": {"Date": "2026-08-31", "Code": "13000",
               "ShrtVol": 50, "LongVol": 100},
    "master": {"Date": "2026-08-31", "Code": "13000",
               "CoName": "Fixture", "Mkt": "0111"},
    "topix": {"Date": "2026-08-31", "O": 3000, "H": 3010,
              "L": 2990, "C": 3005},
    "calendar": {"Date": "2026-08-31", "HolDiv": "1"},
}


@pytest.mark.parametrize("dataset", ROWS)
def test_normal_page_has_exact_shape_only_summary(dataset):
    result = inspect_v2_page(dataset, {"data": [ROWS[dataset]]})
    assert result == {
        "contract": "v2-shape-only", "dataset": dataset, "row_count": 1,
        "has_more": False, "read_only": True, "ready_for_live": False,
    }


@pytest.mark.parametrize("dataset", ROWS)
@pytest.mark.parametrize("token,has_more", [
    (None, False), ("", False), ("next-page", True),
])
def test_empty_page_and_pagination_contract(dataset, token, has_more):
    payload = {"data": []}
    if token is not None:
        payload["pagination_key"] = token
    result = inspect_v2_page(dataset, payload)
    assert result["row_count"] == 0
    assert result["has_more"] is has_more


@pytest.mark.parametrize(
    "dataset,field",
    [(dataset, field) for dataset, row in ROWS.items() for field in row],
)
def test_every_required_field_is_required(dataset, field):
    row = dict(ROWS[dataset])
    del row[field]
    with pytest.raises(ValueError):
        inspect_v2_page(dataset, {"data": [row]})


@pytest.mark.parametrize("dataset", ROWS)
@pytest.mark.parametrize("bad_date", ["20260831", "2026-8-31", "2026-02-30", 20260831])
def test_date_must_be_canonical_iso_string(dataset, bad_date):
    row = dict(ROWS[dataset], Date=bad_date)
    with pytest.raises(ValueError):
        inspect_v2_page(dataset, {"data": [row]})


@pytest.mark.parametrize("dataset,field", [
    (dataset, field)
    for dataset, fields in {
        "prices": ("O", "Vo", "AdjFactor", "AdjC", "AdjVo"),
        "margin": ("ShrtVol", "LongVol"),
        "topix": ("O", "C"),
    }.items() for field in fields
])
@pytest.mark.parametrize("bad", [True, "1", float("nan"), float("inf"), -float("inf")])
def test_numeric_fields_reject_bool_strings_and_nonfinite(dataset, field, bad):
    row = dict(ROWS[dataset], **{field: bad})
    with pytest.raises(ValueError):
        inspect_v2_page(dataset, {"data": [row]})


@pytest.mark.parametrize("dataset,field", [
    ("prices", "O"), ("margin", "ShrtVol"), ("topix", "C"),
])
def test_numeric_fields_allow_none(dataset, field):
    row = dict(ROWS[dataset], **{field: None})
    assert inspect_v2_page(dataset, {"data": [row]})["row_count"] == 1


@pytest.mark.parametrize("dataset,field,bad", [
    ("prices", "Code", ""), ("prices", "Code", 13000),
    ("margin", "Code", ""), ("master", "Code", False),
    ("master", "CoName", ""), ("master", "CoName", 1),
    ("master", "Mkt", 111), ("calendar", "HolDiv", 1),
])
def test_text_field_types_and_required_nonempty_values(dataset, field, bad):
    row = dict(ROWS[dataset], **{field: bad})
    with pytest.raises(ValueError):
        inspect_v2_page(dataset, {"data": [row]})


@pytest.mark.parametrize("token", [0, False, [], {}])
def test_pagination_token_must_be_string_or_terminal(token):
    with pytest.raises(ValueError):
        inspect_v2_page("calendar", {"data": [], "pagination_key": token})


@pytest.mark.parametrize("dataset,payload", [
    ("unknown", {"data": []}),
    (None, {"data": []}),
    ("prices", []),
    ("prices", {}),
    ("prices", {"data": {}}),
    ("prices", {"data": [1]}),
])
def test_container_and_dataset_types_are_strict(dataset, payload):
    with pytest.raises(ValueError):
        inspect_v2_page(dataset, payload)


@pytest.mark.parametrize("dataset", ROWS)
def test_unknown_payload_and_row_fields_are_allowed_and_input_is_unchanged(dataset):
    payload = {"data": [{**ROWS[dataset], "FutureField": {"x": [1, 2]}}],
               "FutureEnvelope": "allowed", "pagination_key": "next"}
    before = copy.deepcopy(payload)
    assert inspect_v2_page(dataset, payload)["has_more"] is True
    assert payload == before


def test_error_is_fixed_and_does_not_echo_secret():
    secret = "SECRET_V2_PAYLOAD_8821"
    with pytest.raises(ValueError) as caught:
        inspect_v2_page(secret, {"data": [{"secret": secret}]})
    assert str(caught.value) == "V2応答のローカル形状を確認できません"
    assert secret not in str(caught.value)


def test_invalid_date_secret_is_absent_from_formatted_traceback():
    secret = "SECRET_INVALID_DATE_9917"
    row = dict(ROWS["calendar"], Date=secret)
    try:
        inspect_v2_page("calendar", {"data": [row]})
    except ValueError as exc:
        rendered = "".join(traceback.format_exception(exc))
    else:
        raise AssertionError("invalid date was accepted")
    assert secret not in rendered
    assert "During handling" not in rendered


def test_inspection_performs_no_network_file_or_database_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("shape inspection must not perform I/O")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(duckdb, "connect", forbidden)
    for dataset, row in ROWS.items():
        assert inspect_v2_page(dataset, {"data": [row]})["row_count"] == 1
