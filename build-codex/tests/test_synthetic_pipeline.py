"""One real synthetic market build shared by independent pipeline checks."""
import json
import socket
import subprocess
from contextlib import closing

import pytest

from test_runner import RunError
from aitrader.runner import normalize_proposal, digest
from aitrader_ops.models import compute_packet_hash
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier


@pytest.fixture(scope='module')
def pipeline(tmp_path_factory):
    from aitrader.synthetic_pipeline import run_synthetic_pipeline
    home = tmp_path_factory.mktemp('synthetic-pipeline')/'home'
    with pytest.MonkeyPatch.context() as patch:
        def forbidden(*args, **kwargs):
            pytest.fail('synthetic pipeline attempted external I/O or delivery')
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(socket, 'create_connection', forbidden)
        patch.setattr(socket.socket, 'connect', forbidden)
        patch.setattr(Notifier, 'flush', forbidden)
        summary = run_synthetic_pipeline(home, seed=42)
    folder = home/'runs'/summary['execution_day']/summary['run_id']
    return home, summary, folder


def test_generated_proposals_and_review_ids_are_bound(pipeline):
    home, summary, folder = pipeline
    proposals = json.loads((folder/'proposals.json').read_text(encoding='utf-8'))
    generated = json.loads((home/'generated_proposals.json').read_text(encoding='utf-8'))
    assert {p['proposal_id']: p['packet_hash'] for p in generated} == {
        p['proposal_id']: p['packet_hash'] for p in proposals}
    assert len(generated) == summary['generated_count']
    assert digest(generated) == summary['generated_hash']
    votes = json.loads((folder/'verdicts.json').read_text(encoding='utf-8'))
    result = json.loads((folder/'result.json').read_text(encoding='utf-8'))
    assert proposals
    ids = {p['proposal_id'] for p in proposals}
    assert len(ids) == len(proposals)
    assert ids == set(votes) == {c['proposal_id'] for c in result['candidates']}
    for raw in proposals:
        proposal = normalize_proposal(raw)
        assert proposal.packet_hash == compute_packet_hash(proposal)
        assert proposal.code != 'DEMO'
        assert str(proposal.as_of) == '2026-08-28'
        assert len(votes[proposal.proposal_id]) == 2
        assert {v['judge'] for v in votes[proposal.proposal_id]} == {'claude', 'codex'}
        assert all(v['decision'] == 'APPROVE' and v['packet_hash'] == proposal.packet_hash
                   for v in votes[proposal.proposal_id])


def test_gate_reservations_match_only_approved_candidates(pipeline):
    home, summary, folder = pipeline
    result = json.loads((folder/'result.json').read_text(encoding='utf-8'))
    approved = [c for c in result['candidates'] if c['status'] == 'APPROVED']
    assert 0 < len(approved) <= 2
    assert any(c['status'] != 'APPROVED' for c in result['candidates'])
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        assert ledger.reserved() == sum(c['gate']['reserve_amount'] for c in approved)
        for candidate in approved:
            assert ledger.notice(candidate['proposal_id'])['notice_state'] == 'APPROVED'


def test_real_generation_ends_with_unsent_queue(pipeline):
    home, summary, folder = pipeline
    assert summary['mode'] == 'mock' and summary['delivery'] == 'NOT_SENT'
    plan = json.loads((folder/'notification_plan.json').read_text(encoding='utf-8'))
    receipt = json.loads((folder/'notification_queue.json').read_text(encoding='utf-8'))
    assert receipt['delivery'] == 'NOT_SENT' and receipt['sent_by_this_call'] is False
    assert {c['key'] for c in plan['plans']} == {c['key'] for c in receipt['entries']}
    assert all(entry['state'] == 'PENDING' for entry in receipt['entries'])
    assert not (folder/'mock_delivery.json').exists()


def test_existing_home_rejected_without_touching_marker(tmp_path):
    from aitrader.synthetic_pipeline import run_synthetic_pipeline
    home = tmp_path/'existing'
    home.mkdir()
    marker = home/'keep.txt'
    marker.write_text('preserve', encoding='utf-8')
    with pytest.raises((RunError, ValueError)):
        run_synthetic_pipeline(home)
    assert list(home.iterdir()) == [marker]
    assert marker.read_text(encoding='utf-8') == 'preserve'
