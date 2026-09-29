"""Strict managed-v1 recovery helpers for interrupted mock runner candidates."""
from __future__ import annotations

import copy
from decimal import Decimal

from aitrader_ops.models import LedgerView, Verdict, reserve_amount


class ResumeRefused(ValueError):
    """A fail-closed recovery decision with a stable reason code."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def refuse(code):
    raise ResumeRefused(code)


def validate_verdicts(proposal, verdicts, *, run_id, execution_day, now, deadline,
                      aware):
    """Return the two current mock approvals or refuse without mutating state."""
    received = []
    invalid = False
    for verdict in verdicts or []:
        try:
            at = aware(verdict.received_at)
            valid = (isinstance(verdict, Verdict)
                     and verdict.run_id == run_id
                     and verdict.model == 'mock'
                     and verdict.cli_version == 'mock'
                     and verdict.proposal_id == proposal.proposal_id
                     and verdict.packet_hash == proposal.packet_hash
                     and verdict.decision == 'APPROVE'
                     and at.date() == execution_day
                     and at <= now
                     and at < deadline)
        except (AttributeError, TypeError, ValueError):
            valid = False
        if valid:
            received.append(verdict)
        else:
            invalid = True
    judges = [v.judge for v in received]
    if invalid or any(j not in ('claude', 'codex') for j in judges):
        refuse('REVIEW_INVALID')
    if sorted(judges) != ['claude', 'codex']:
        refuse('REVIEW_INCOMPLETE')
    return received


def owned_notice(ledger, journal, proposal, proposals, *, run_id, manifest,
                 now, digest, normalize_proposal):
    """Verify Tier-A ownership evidence E1-E6 from public ledger reads."""
    try:
        stored = normalize_proposal(ledger.proposal(proposal.proposal_id))
        notice = ledger.notice(proposal.proposal_id)
        fee_margin = Decimal(ledger.policy()['fee_margin'])
        created = notice['history'][0]
        started_at = manifest['started_at']
        from .runner import aware
        t0 = aware(created[1])
        started = aware(started_at)
    except (KeyError, IndexError, TypeError, ValueError, ArithmeticError):
        refuse('OWNERSHIP_UNPROVEN')
    state = notice.get('notice_state')
    history = notice.get('history')
    expected_history = 1 if state == 'CREATED' else 2 if state == 'APPROVED' else -1
    history_valid = (isinstance(history, list)
                     and len(history) == expected_history
                     and created[0] == 'CREATED'
                     and started <= t0 <= now)
    if state == 'APPROVED' and history_valid:
        try:
            approved = history[1]
            t1 = aware(approved[1])
            history_valid = approved[0] == 'APPROVED' and t0 <= t1 <= now
        except (IndexError, TypeError, ValueError):
            history_valid = False
    expected_reserve = (reserve_amount(proposal.qty, proposal.limit_price, fee_margin)
                        if proposal.side == 'BUY' else 0)
    expected_shares = proposal.qty if proposal.side == 'SELL' else 0
    valid = (digest(stored) == digest(proposal)
             and history_valid
             and notice.get('filled_qty') == 0
             and notice.get('fills') == []
             and notice.get('trade_state') == 'UNCONFIRMED'
             and notice.get('reserve') == expected_reserve
             and notice.get('reserved_shares') == expected_shares)
    if not valid:
        refuse('OWNERSHIP_UNPROVEN')
    for other in proposals:
        if other.proposal_id == proposal.proposal_id or other.code != proposal.code:
            continue
        row = journal.execute(
            "SELECT state,owner FROM candidates WHERE pid=?",
            [other.proposal_id]).fetchone()
        if row == ('INTENT', run_id):
            try:
                ledger.notice(other.proposal_id)
            except KeyError:
                pass
            else:
                refuse('OWNERSHIP_UNPROVEN')
    return state, notice, t0


def reservation_excluded_view(view, notice, proposal):
    """Copy a LedgerView with only this candidate's reservation removed."""
    reserved_positions = dict(view.reserved_positions)
    reserved_shares = dict(view.reserved_shares)
    own_cash = notice['reserve']
    own_shares = notice['reserved_shares']
    if (not isinstance(own_cash, int) or own_cash < 0
            or not isinstance(own_shares, int) or own_shares < 0
            or view.reserved < own_cash):
        refuse('RESERVATION_MISMATCH')
    if own_cash:
        current = reserved_positions.get(proposal.code, 0)
        if current < own_cash:
            refuse('RESERVATION_MISMATCH')
        remaining = current-own_cash
        if remaining:
            reserved_positions[proposal.code] = remaining
        else:
            reserved_positions.pop(proposal.code, None)
    if own_shares:
        current = reserved_shares.get(proposal.code, 0)
        if current < own_shares:
            refuse('RESERVATION_MISMATCH')
        remaining = current-own_shares
        if remaining:
            reserved_shares[proposal.code] = remaining
        else:
            reserved_shares.pop(proposal.code, None)
    return LedgerView(cash=view.cash, reserved=view.reserved-own_cash,
                      available=view.available+own_cash,
                      positions=dict(view.positions),
                      reserved_positions=reserved_positions,
                      daily_pnl=view.daily_pnl,
                      day_start_equity=view.day_start_equity,
                      reserved_shares=reserved_shares)


def require_sellable(proposal, view):
    if proposal.side != 'SELL':
        return
    position = view.positions.get(proposal.code)
    held = position.qty if position is not None else 0
    if held-int(view.reserved_shares.get(proposal.code, 0)) < proposal.qty:
        refuse('INSUFFICIENT_SHARES')


def resumed_item(prior_result, gate, *, at, from_state, created_at,
                 seq_after, gate_reevaluated, plain):
    item = copy.deepcopy(prior_result)
    resume = {'at': at.isoformat(), 'from_state': from_state,
              'gate_reevaluated': gate_reevaluated,
              'original_created_at': created_at.isoformat()}
    if gate_reevaluated:
        resume['original_gate'] = copy.deepcopy(item['gate'])
        item['gate'] = plain(gate)
    item['resume'] = resume
    item['ledger_seq'] = seq_after
    return item


def resume_manifest(manifest, proposal_id, from_state, *, at, seq_before,
                    seq_after):
    updated = copy.deepcopy(manifest)
    updated.setdefault('resumes', []).append({
        'at': at.isoformat(), 'from_states': {proposal_id: from_state},
        'seq_before': seq_before, 'seq_after': seq_after})
    return updated


def finalize_resume(journal, proposal, execution_day, item, manifest, *, encoded):
    """Atomically write outbox, candidate result, and durable run manifest."""
    body = dict(key=f'{execution_day}:{proposal.proposal_id}:{proposal.packet_hash}:CANDIDATE',
                mode='mock', delivery='NOT_SENT', kind='CANDIDATE',
                proposal=None,
                message='模擬候補です。実際の注文には使用しないでください。')
    from .runner import plain
    body['proposal'] = plain(proposal)
    journal.execute('BEGIN')
    try:
        journal.execute('INSERT INTO outbox VALUES(?,?)',
                        [body['key'], encoded(body)])
        journal.execute("UPDATE candidates SET state='APPROVED',result=? WHERE pid=?",
                        [encoded(item), proposal.proposal_id])
        journal.execute('UPDATE runs SET manifest=? WHERE id=?',
                        [encoded(manifest), manifest['run_id']])
        journal.execute('COMMIT')
    except BaseException:
        try:
            journal.execute('ROLLBACK')
        except BaseException:
            pass
        raise
    return body
