"""Pure offline inspection of a fixture J-Quants V2 page chain."""
from __future__ import annotations

from typing import Any

from .jquants_v2_contract import inspect_v2_page
from .strict_input import _canonical


MODE = "v2_page_chain_fixture_v1"
_DATASETS = frozenset(("prices", "margin", "master", "topix", "calendar"))


def inspect_v2_page_chain(bundle: object) -> dict:
    """Inspect receipt-order continuation linkage without performing any I/O."""
    reasons: set[str] = set()
    data = bundle if isinstance(bundle, dict) else {}
    if not isinstance(bundle, dict):
        reasons.add("INVALID_BUNDLE")
    elif set(data) != {"mode", "dataset", "pages"}:
        reasons.add("INVALID_BUNDLE")

    if data.get("mode") != MODE:
        reasons.add("INVALID_MODE")

    raw_dataset = data.get("dataset")
    dataset = raw_dataset if isinstance(raw_dataset, str) and raw_dataset in _DATASETS else None
    if dataset is None:
        reasons.add("INVALID_DATASET")

    pages = data.get("pages")
    page_count = len(pages) if isinstance(pages, list) else 0
    row_count = 0
    if not isinstance(pages, list) or not pages:
        reasons.add("INVALID_PAGES")
        pages = []

    first_query: bytes | None = None
    previous_token: str | None = None
    previous_was_terminal = False
    seen_tokens: set[str] = set()

    for index, page in enumerate(pages):
        if not isinstance(page, dict) or set(page) != {
            "query", "request_token", "response"
        }:
            reasons.add("INVALID_PAGE")
            if isinstance(page, dict) and dataset is not None and "response" in page:
                try:
                    summary = inspect_v2_page(dataset, page["response"])
                except (TypeError, ValueError, OverflowError, RecursionError):
                    reasons.add("INVALID_RESPONSE")
                else:
                    row_count += summary["row_count"]
            continue

        query = page.get("query")
        if not isinstance(query, dict) or "pagination_key" in query:
            reasons.add("INVALID_QUERY")
        else:
            try:
                encoded_query = _canonical(query)
            except (TypeError, ValueError, OverflowError, RecursionError):
                reasons.add("INVALID_QUERY")
            else:
                if first_query is None:
                    first_query = encoded_query
                elif encoded_query != first_query:
                    reasons.add("QUERY_CHANGED")

        request_token = page.get("request_token")
        if index == 0:
            if request_token is not None:
                reasons.add("REQUEST_TOKEN_MISMATCH")
        elif request_token != previous_token:
            reasons.add("REQUEST_TOKEN_MISMATCH")
        if index > 0 and previous_was_terminal:
            reasons.add("EARLY_TERMINAL")

        response = page.get("response")
        if dataset is not None:
            try:
                summary = inspect_v2_page(dataset, response)
            except (TypeError, ValueError, OverflowError, RecursionError):
                reasons.add("INVALID_RESPONSE")
            else:
                row_count += summary["row_count"]

        token_present = isinstance(response, dict) and "pagination_key" in response
        token = response.get("pagination_key") if isinstance(response, dict) else None
        token_valid = token_present and isinstance(token, str) and token != ""
        if token_present and not token_valid:
            reasons.add("INVALID_CONTINUATION_TOKEN")
        if token_valid:
            if token in seen_tokens:
                reasons.add("CONTINUATION_CYCLE")
            seen_tokens.add(token)

        is_final = index == len(pages) - 1
        if is_final:
            if token_present:
                if token_valid:
                    reasons.add("MISSING_TERMINAL")
        elif not token_present:
            reasons.add("EARLY_TERMINAL")

        # Only a valid response token can authorize the next request. Invalid
        # response shapes still contribute their independently checked token
        # semantics, but never make the chain verified.
        previous_token = token if token_valid else None
        previous_was_terminal = not token_present

    return {
        "mode": MODE,
        "dataset": dataset,
        "status": "DATA_INCOMPLETE" if reasons else "VERIFIED_OFFLINE_PAGE_CHAIN",
        "page_count": page_count,
        "row_count": row_count,
        "reason_codes": sorted(reasons),
        "fixture_only": True,
        "consistent_snapshot_verified": False,
        "ready_for_live": False,
        "current_signal": False,
        "read_only": True,
    }
