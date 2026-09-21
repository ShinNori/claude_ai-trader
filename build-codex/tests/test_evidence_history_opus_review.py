"""Independent review (Claude Opus, 2026-09-12) of evidence_history_fixture_v1.

These tests are additive regression guards written from the published contract
(build-codex/EVIDENCE_HISTORY_FIXTURE.md) without reusing the module's own
helpers, so a change of internal representation cannot silently satisfy them.
They pin the properties the binding plan depends on. No implementation file and
no existing expectation was modified.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import random

import pytest

from aitrader.evidence_history_fixture import inspect_evidence_history

JST = timezone(timedelta(hours=9))
OK = "VERIFIED_OFFLINE_HISTORY"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def lot_row(size, code="0001"):
    return {"code": code, "lot_size": size}


def event_row(code="0001", regulated=False):
    return {"code": code, "next_earnings_at": None, "margin_regulated": regulated}


def entry(receipt, revision, group, payload, observed, recorded, supersedes=None):
    return {
        "receipt_id": receipt,
        "revision_id": revision,
        "subject": {"group": group, "code": payload["code"]},
        "observed_at": observed,
        "recorded_at": recorded,
        "payload": payload,
        "sha256": digest(payload),
        "supersedes": supersedes,
    }


def bundle(entries, decision_at="2026-09-12T09:00:00+09:00"):
    return {
        "mode": "evidence_history_fixture_v1",
        "source_origin": "offline_fixture",
        "decision_at": decision_at,
        "entries": entries,
    }


def parent(previous):
    return {"revision_id": previous["revision_id"], "sha256": previous["sha256"]}


@pytest.fixture()
def chain():
    """One lot row corrected once, plus an unrelated event row."""
    first = entry("r-1", "v-1", "lots", lot_row(100),
                  "2026-09-12T06:00:00+09:00", "2026-09-12T06:00:00+09:00")
    event = entry("r-2", "v-2", "events", event_row(),
                  "2026-09-12T06:05:00+09:00", "2026-09-12T06:05:00+09:00")
    second = entry("r-3", "v-3", "lots", lot_row(200),
                   "2026-09-12T07:00:00+09:00", "2026-09-12T07:00:00+09:00",
                   parent(first))
    return [first, event, second]


def test_later_correction_never_changes_an_earlier_decision(chain):
    """Deciding at T must equal deciding at T over only the entries recorded by T."""
    decision = "2026-09-12T06:30:00+09:00"
    full = inspect_evidence_history(bundle(chain, decision))
    truncated = inspect_evidence_history(bundle(chain[:2], decision))
    assert full["status"] == truncated["status"] == OK
    assert full["selection_sha256"] == truncated["selection_sha256"]
    assert full["selected_revision_count"] == truncated["selected_revision_count"] == 2
    assert full["future_revision_count"] == 1
    assert truncated["future_revision_count"] == 0


@pytest.mark.parametrize("seed", range(40))
def test_past_replay_invariance_over_generated_histories(seed):
    """Randomised form of the same property across corrections, replays and re-fetches."""
    rng = random.Random(seed)
    codes = ["0001", "0002"]
    groups = ["lots", "events"]
    heads, entries = {}, []
    clock = datetime(2026, 9, 12, 5, 0, tzinfo=JST)
    counter = 0
    for _ in range(rng.randint(4, 14)):
        clock += timedelta(seconds=rng.randint(0, 400))
        if entries and rng.random() < 0.3:
            source = rng.choice(entries)
            replay = deepcopy(source)
            if rng.random() < 0.5:                      # same receipt, exact replay
                entries.append(replay)
            else:                                       # same revision, new receipt
                counter += 1
                replay["receipt_id"] = "rr-%d" % counter
                replay["observed_at"] = clock.isoformat()
                replay["recorded_at"] = clock.isoformat()
                entries.append(replay)
            continue
        subject = (rng.choice(groups), rng.choice(codes))
        payload = (lot_row(rng.randint(1, 9) * 100, subject[1]) if subject[0] == "lots"
                   else event_row(subject[1], bool(rng.getrandbits(1))))
        counter += 1
        previous = heads.get(subject)
        new = entry("rc-%d" % counter, "rv-%d" % counter, subject[0], payload,
                    clock.isoformat(), clock.isoformat(),
                    None if previous is None else parent(previous))
        entries.append(new)
        heads[subject] = new

    stamps = sorted({item["recorded_at"] for item in entries})
    for stamp in stamps:
        limit = datetime.fromisoformat(stamp)
        whole = inspect_evidence_history(bundle(entries, stamp))
        prefix = [item for item in entries
                  if datetime.fromisoformat(item["recorded_at"]) <= limit]
        part = inspect_evidence_history(bundle(prefix, stamp))
        assert whole["status"] == part["status"] == OK
        assert whole["selection_sha256"] == part["selection_sha256"]
        assert whole["selected_revision_count"] == part["selected_revision_count"]


def test_decision_boundary_includes_the_recorded_instant(chain):
    on_time = inspect_evidence_history(bundle(chain, "2026-09-12T07:00:00+09:00"))
    just_before = inspect_evidence_history(
        bundle(chain, "2026-09-12T06:59:59.999999+09:00"))
    assert on_time["selected_revision_count"] == 2
    assert on_time["future_revision_count"] == 0
    assert just_before["future_revision_count"] == 1
    assert on_time["selection_sha256"] != just_before["selection_sha256"]


def test_decision_instant_is_compared_across_timezones(chain):
    jst = inspect_evidence_history(bundle(chain, "2026-09-12T07:00:00+09:00"))
    utc = inspect_evidence_history(bundle(chain, "2026-09-11T22:00:00+00:00"))
    assert jst == utc


def test_reordering_independent_entries_does_not_change_the_result():
    """Entries of different subjects sharing one instant may arrive in any order."""
    stamp = "2026-09-12T06:00:00+09:00"
    lots = entry("r-1", "v-1", "lots", lot_row(100), stamp, stamp)
    events = entry("r-2", "v-2", "events", event_row(), stamp, stamp)
    assert inspect_evidence_history(bundle([lots, events])) == \
        inspect_evidence_history(bundle([events, lots]))


def test_entries_must_arrive_in_nondecreasing_recorded_order(chain):
    """Contract limit: the ordering duty is global, not per subject."""
    result = inspect_evidence_history(bundle([chain[1], chain[0], chain[2]]))
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == ["RECORDED_AT_REVERSED"]


def test_same_value_correction_keeps_a_distinct_revision_identity(chain):
    repeated = entry("r-4", "v-4", "lots", lot_row(200),
                     "2026-09-12T07:30:00+09:00", "2026-09-12T07:30:00+09:00",
                     parent(chain[2]))
    before = inspect_evidence_history(bundle(chain))
    after = inspect_evidence_history(bundle(chain + [repeated]))
    assert after["status"] == OK
    assert after["revision_count"] == before["revision_count"] + 1
    assert after["selection_sha256"] != before["selection_sha256"]


def test_refetched_revision_may_not_claim_a_different_parent(chain):
    rewritten = deepcopy(chain[0])
    rewritten["receipt_id"] = "r-9"
    rewritten["recorded_at"] = "2026-09-12T08:00:00+09:00"
    rewritten["observed_at"] = "2026-09-12T08:00:00+09:00"
    rewritten["supersedes"] = parent(chain[2])
    result = inspect_evidence_history(bundle(chain + [rewritten]))
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == ["REVISION_CONFLICT"]


def test_a_subject_cannot_gain_a_second_root(chain):
    rival = entry("r-9", "v-9", "lots", lot_row(500),
                  "2026-09-12T08:00:00+09:00", "2026-09-12T08:00:00+09:00")
    result = inspect_evidence_history(bundle(chain + [rival]))
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == ["INVALID_SUPERSEDES"]


def test_a_new_subject_cannot_inherit_another_subjects_head(chain):
    borrowed = entry("r-9", "v-9", "lots", lot_row(100, "0002"),
                     "2026-09-12T08:00:00+09:00", "2026-09-12T08:00:00+09:00",
                     parent(chain[2]))
    result = inspect_evidence_history(bundle(chain + [borrowed]))
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == ["INVALID_SUPERSEDES"]


def test_an_existing_subject_cannot_point_at_another_subjects_head(chain):
    crossed = entry("r-9", "v-9", "events", event_row(regulated=True),
                    "2026-09-12T08:00:00+09:00", "2026-09-12T08:00:00+09:00",
                    parent(chain[2]))
    result = inspect_evidence_history(bundle(chain + [crossed]))
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == ["INVALID_SUPERSEDES"]


def test_receipt_replay_that_only_changes_the_clock_is_a_conflict(chain):
    restamped = deepcopy(chain[0])
    restamped["recorded_at"] = "2026-09-12T08:00:00+09:00"
    result = inspect_evidence_history(bundle(chain + [restamped]))
    assert result["status"] == "DATA_INCOMPLETE"
    assert result["reason_codes"] == ["RECEIPT_CONFLICT"]


def test_every_rejection_reports_one_reason_and_no_partial_counts(chain):
    broken = deepcopy(chain)
    broken[2]["supersedes"] = {"revision_id": "unknown", "sha256": "0" * 64}
    result = inspect_evidence_history(bundle(broken))
    assert result["status"] == "DATA_INCOMPLETE"
    assert len(result["reason_codes"]) == 1
    assert result["selection_sha256"] is None
    assert all(result[field] == 0 for field in (
        "receipt_count", "revision_count", "noop_receipt_count",
        "selected_revision_count", "future_revision_count"))


def test_history_only_of_future_revisions_succeeds_with_the_empty_digest(chain):
    """Documented limit: internal consistency alone never proves evidence sufficiency."""
    result = inspect_evidence_history(bundle(chain, "2026-09-12T05:00:00+09:00"))
    assert result["status"] == OK
    assert result["selected_revision_count"] == 0
    assert result["future_revision_count"] == 3
    assert result["selection_sha256"] == hashlib.sha256(b"[]").hexdigest()


def test_payload_hash_matches_the_strict_input_row_representation():
    """Precondition for the binding plan: both sides hash the same canonical bytes."""
    from aitrader.strict_input import _canonical as strict_canonical

    for row in (lot_row(100), event_row()):
        assert digest(row) == hashlib.sha256(strict_canonical(row)).hexdigest()
