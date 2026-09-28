"""Read-only derivation of EXIT lots from the public Ledger API."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9), 'Asia/Tokyo')


@dataclass(frozen=True)
class HoldingDerivationResult:
    holdings: list[dict]
    reason_codes: list[str]
    observed_seq: int | None
    candidate_ids: tuple[str, ...] = ()
    excluded: tuple[dict, ...] = ()


def _get(value: Any, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _dt(value, label):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} requires an ISO datetime') from exc
    if result.tzinfo is None:
        raise ValueError(f'{label} requires a timezone')
    return result


def _scaled_decimal(qty, since, adjustments, code):
    value = Decimal(qty)
    split_adjustments = [a for a in adjustments if a.get('kind', 'SPLIT') == 'SPLIT']
    for adjustment in sorted(split_adjustments, key=lambda a: _dt(a['at'], 'adjustment.at')):
        if adjustment.get('code') == code and _dt(adjustment['at'], 'adjustment.at') > since:
            ratio = Decimal(str(adjustment.get('ratio')))
            if not ratio.is_finite() or ratio <= 0:
                raise ValueError('invalid SPLIT ratio')
            value *= ratio
    return value


def _scaled(qty, since, adjustments, code):
    value = _scaled_decimal(qty, since, adjustments, code)
    if value != value.to_integral_value():
        raise ArithmeticError('SPLIT_UNRESOLVED')
    return int(value)


def _snapshot_orders(snapshot):
    orders = []
    for index, item in enumerate(snapshot.get('open_orders') or [], 1):
        row = dict(item)
        row['proposal_id'] = row.get('proposal_id') or f'external-{index}'
        orders.append(row)
    return orders


def _journal_candidates(path):
    path = Path(path).resolve()
    uri = path.as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as con:
        rows = con.execute('SELECT pid,state FROM candidates').fetchall()
    return {str(pid): str(state) for pid, state in rows}


def derive_holdings(ledger, orchestration_path, snapshot, adjustments, as_of,
                    *, max_attempts=3) -> HoldingDerivationResult:
    """Derive current BUY lots without accessing Ledger private state.

    Snapshot and SPLIT adjustments are explicit caller-owned inputs.  Only the
    runner ``candidates`` table is read from orchestration.sqlite, in read-only
    mode.  A changing ledger is retried up to ``max_attempts``.
    """
    if snapshot is None:
        return HoldingDerivationResult([], ['SNAPSHOT_MISSING'], None)
    if not isinstance(snapshot, dict) or not isinstance(adjustments, list):
        raise ValueError('snapshot must be a dict and adjustments must be a list')
    if not isinstance(as_of, (date, datetime)):
        raise ValueError('as_of must be date or datetime')
    observed_at = as_of if isinstance(as_of, datetime) else datetime.combine(as_of, datetime.max.time(), JST)
    try:
        candidates = _journal_candidates(orchestration_path)
    except (OSError, sqlite3.Error):
        return HoldingDerivationResult([], ['JOURNAL_UNREADABLE'], None)
    snapshot_orders = _snapshot_orders(snapshot)
    candidate_ids = set(candidates) | {o['proposal_id'] for o in snapshot_orders}
    if any(state == 'INTENT' for state in candidates.values()):
        return HoldingDerivationResult([], ['RUN_INCOMPLETE'], ledger.seq(), tuple(sorted(candidate_ids)))

    for _ in range(max_attempts):
        seq_before = ledger.seq()
        view = ledger.view()
        unconfirmed = set(ledger.unconfirmed(observed_at))
        proposals, notices = {}, {}
        missing = False
        for pid in sorted(candidate_ids):
            try:
                proposals[pid] = ledger.proposal(pid)
                notices[pid] = ledger.notice(pid)
            except KeyError:
                # REJECTED candidates never create a Ledger notice.  An
                # APPROVED candidate without one is inconsistent.
                if candidates.get(pid) != 'REJECTED':
                    missing = True
        seq_after = ledger.seq()
        if seq_before != seq_after:
            continue
        if missing or not unconfirmed <= candidate_ids:
            return HoldingDerivationResult([], ['LEDGER_INCONSISTENT'], seq_after, tuple(sorted(candidate_ids)))
        snapshot_ids = {o['proposal_id'] for o in snapshot_orders}
        external_ids = {pid for pid, p in proposals.items() if p.get('exec_condition') == 'EXTERNAL'}
        if external_ids != snapshot_ids or any(proposals[pid].get('exec_condition') != 'EXTERNAL' for pid in snapshot_ids):
            return HoldingDerivationResult([], ['LEDGER_INCONSISTENT'], seq_after, tuple(sorted(candidate_ids)))
        known_sell_reservations, known_buy_reservations = {}, {}
        for pid, proposal in proposals.items():
            code = str(proposal.get('code'))
            notice = notices[pid]
            if proposal.get('side') == 'SELL':
                known_sell_reservations[code] = (known_sell_reservations.get(code, 0)
                                                  + notice.get('reserved_shares', 0))
            elif proposal.get('side') == 'BUY':
                known_buy_reservations[code] = (known_buy_reservations.get(code, 0)
                                                 + notice.get('reserve', 0))
        sell_codes = set(known_sell_reservations) | set(view.reserved_shares)
        buy_codes = set(known_buy_reservations) | set(view.reserved_positions)
        if (any(known_sell_reservations.get(code, 0) != view.reserved_shares.get(code, 0)
                for code in sell_codes)
                or any(known_buy_reservations.get(code, 0) != view.reserved_positions.get(code, 0)
                       for code in buy_codes)):
            return HoldingDerivationResult([], ['LEDGER_INCONSISTENT'], seq_after,
                                           tuple(sorted(candidate_ids)))
        try:
            holdings, excluded = _derive(view, proposals, notices, unconfirmed, snapshot, adjustments, seq_after)
        except (ValueError, DecimalException):
            return HoldingDerivationResult([], ['LEDGER_INCONSISTENT'], seq_after, tuple(sorted(candidate_ids)))
        return HoldingDerivationResult(holdings, [], seq_after, tuple(sorted(candidate_ids)), tuple(excluded))
    return HoldingDerivationResult([], ['SEQ_CHANGED'], None, tuple(sorted(candidate_ids)))


def _derive(view, proposals, notices, unconfirmed, snapshot, adjustments, seq):
    snapshot_at = _dt(snapshot.get('at'), 'snapshot.at')
    current = {code: int(_get(pos, 'qty')) for code, pos in view.positions.items()}
    expected_decimal, initial_decimal = {}, {}
    for pos in snapshot.get('positions') or []:
        code = str(pos['code'])
        value = _scaled_decimal(int(pos['qty']), snapshot_at, adjustments, code)
        initial_decimal[code] = initial_decimal.get(code, Decimal(0)) + value
        expected_decimal[code] = expected_decimal.get(code, Decimal(0)) + value
    buys, sells = [], []
    for pid, proposal in proposals.items():
        code, side = str(proposal['code']), proposal['side']
        live_fills = [f for f in notices[pid].get('fills', []) if not f.get('reversed')]
        transformed = []
        for fill in live_fills:
            at = _dt(fill.get('at'), 'fill.at')
            qty = _scaled_decimal(int(fill['qty']), at, adjustments, code)
            transformed.append(qty)
            expected_decimal[code] = expected_decimal.get(code, Decimal(0)) + (qty if side == 'BUY' else -qty)
        if side == 'BUY' and live_fills:
            buys.append({'pid': pid, 'proposal': proposal, 'notice': notices[pid], 'fills': live_fills,
                         'entry': min(_dt(f['at'], 'fill.at') for f in live_fills)})
        elif side == 'SELL' and notices[pid].get('filled_qty', 0):
            sells.append({'pid': pid, 'proposal': proposal, 'notice': notices[pid], 'fills': live_fills})
    codes = set(expected_decimal) | set(current)
    if any(expected_decimal.get(code, Decimal(0)) != Decimal(current.get(code, 0)) for code in codes):
        raise ValueError('balance mismatch')

    initial = dict(initial_decimal)

    lots = []
    for item in buys:
        code = str(item['proposal']['code'])
        qty = sum((_scaled_decimal(int(f['qty']), _dt(f['at'], 'fill.at'), adjustments, code)
                   for f in item['fills']), Decimal(0))
        lots.append({'code': code, 'pid': item['pid'], 'strategy': item['proposal']['strategy'],
                     'version': item['proposal']['strategy_version'], 'entry': item['entry'], 'remaining': qty})
    lots.sort(key=lambda x: (x['code'], x['entry'].astimezone(JST).date(), x['pid']))
    unattributed = dict(initial)
    allocation_remaining = {
        item['pid']: [int(a['qty']) for a in item['proposal'].get('events', {}).get('exit_lots', [])]
        for item in sells
    }
    sell_events = sorted(
        ((item, fill) for item in sells for fill in item['fills']),
        key=lambda pair: (_dt(pair[1]['at'], 'fill.at'), pair[0]['pid'], str(pair[1].get('event_id', ''))),
    )
    for item, fill in sell_events:
        proposal = item['proposal']
        code = str(proposal['code'])
        allocations = proposal.get('events', {}).get('exit_lots')
        if allocations:
            raw_remaining = int(fill['qty'])
            fill_at = _dt(fill['at'], 'fill.at')
            remaining_by_lot = allocation_remaining[item['pid']]
            for index, allocation in enumerate(allocations):
                raw_take = min(raw_remaining, remaining_by_lot[index])
                if not raw_take:
                    continue
                lot = next((x for x in lots if x['code'] == code and x['pid'] == allocation['source_proposal_id']), None)
                take = _scaled_decimal(raw_take, fill_at, adjustments, code)
                if lot is None or take > lot['remaining']:
                    raise ValueError('invalid exit_lots')
                lot['remaining'] -= take
                remaining_by_lot[index] -= raw_take
                raw_remaining -= raw_take
                if not raw_remaining:
                    break
            if raw_remaining:
                raise ValueError('filled SELL exceeds exit_lots')
        else:
            remaining = _scaled_decimal(int(fill['qty']), _dt(fill['at'], 'fill.at'), adjustments, code)
            take = min(remaining, unattributed.get(code, 0)); unattributed[code] = unattributed.get(code, 0) - take
            remaining -= take
            for lot in [x for x in lots if x['code'] == code]:
                take = min(remaining, lot['remaining']); lot['remaining'] -= take; remaining -= take
                if not remaining: break
            if remaining:
                raise ValueError('sell exceeds attributed quantities')

    open_sell_codes = set()
    for pid, proposal in proposals.items():
        notice = notices[pid]
        known_open = (proposal.get('side') == 'SELL'
                      and notice.get('trade_state') in ('UNCONFIRMED', 'ORDERED', 'PARTIAL')
                      and notice.get('notice_state') != 'REJECTED')
        if known_open or pid in unconfirmed:
            if proposal.get('side') == 'SELL':
                open_sell_codes.add(str(proposal['code']))
    unresolved = {
        code for code in codes
        if initial.get(code, Decimal(0)) != initial.get(code, Decimal(0)).to_integral_value()
        or any(lot['code'] == code and lot['remaining'] != lot['remaining'].to_integral_value() for lot in lots)
    }
    result = []
    for lot in lots:
        if lot['remaining'] <= 0 or lot['code'] in unresolved: continue
        code = lot['code']
        held = current.get(code, 0)
        result.append({'code': code, 'observed_seq': seq, 'source_proposal_id': lot['pid'],
                       'strategy': lot['strategy'], 'strategy_version': lot['version'],
                       'entry_date': lot['entry'].astimezone(JST).date(), 'lot_qty': int(lot['remaining']),
                       'held_qty': held, 'reserved_shares': int(view.reserved_shares.get(code, 0)),
                       'unattributed_qty': int(unattributed.get(code, 0)),
                       'open_sell_notice': code in open_sell_codes})
    excluded = []
    for code in sorted(unresolved):
        pids = sorted(pid for pid, p in proposals.items() if p.get('side') == 'BUY' and str(p.get('code')) == code)
        for pid in pids or [None]:
            excluded.append({'code': code, 'source_proposal_id': pid, 'reason_code': 'SPLIT_UNRESOLVED'})
    return result, excluded
