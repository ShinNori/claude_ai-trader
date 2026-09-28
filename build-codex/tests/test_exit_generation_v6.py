"""EXIT contract v6 counterexamples 1-45 (offline; no delivery or orders)."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest

from aitrader.exit_holdings import derive_holdings
from aitrader.db import connect, init
from aitrader.packet import Proposal, build_exit_proposals, packet_hash
from aitrader.runner import Valuation, initialize_mock, mock_verdicts, run
from aitrader_ops.ledger import Ledger
from aitrader_ops.gate import evaluate
from aitrader_ops.limits import Limits
from aitrader_ops.models import CsvFill, TradeEvent

JST = timezone(timedelta(hours=9))
AS_OF = date(2026, 9, 29)
CALENDAR = [date(2026, 9, 1) + timedelta(days=i) for i in range(61)
            if (date(2026, 9, 1) + timedelta(days=i)).weekday() < 5]


def lot(code='7203', pid='A1-20260901-7203-01', entry=date(2026, 9, 1), qty=100,
        held=100, reserved=0, unattributed=0, open_sell=False, version='v1', seq=42):
    return dict(code=code, observed_seq=seq, source_proposal_id=pid,
                strategy='margin_bucket_long', strategy_version=version,
                entry_date=entry, lot_qty=qty, held_qty=held,
                reserved_shares=reserved, unattributed_qty=unattributed,
                open_sell_notice=open_sell)


def build(rows, *, prices=None, lots=None, calendar=None, as_of=AS_OF):
    codes = {r['code'] for r in rows}
    return build_exit_proposals(rows, as_of, prices if prices is not None else {c: 2000 for c in codes},
                                lots if lots is not None else {c: 100 for c in codes},
                                {c: {'next_earnings_date': None, 'margin_regulated': False} for c in codes},
                                'a' * 64, 'v1', 20,
                                business_days=CALENDAR if calendar is None else calendar)


def test_01_to_05_fixed_due_boundary_holiday_and_calendar_shortage():
    result = build([lot(), lot('6857', 'A1-20260908-6857-01', date(2026, 9, 8)),
                    lot('9984', 'A1-20260901-9984-01', qty=200, held=200, reserved=200)])
    assert [(p.code, p.qty, p.limit_price) for p in result.proposals] == [('7203', 100, 1990.0)]
    assert {(x['code'], x['reason_code']) for x in result.excluded} == {
        ('6857', 'NOT_DUE'), ('9984', 'NO_SELLABLE_SHARES')}
    assert build([lot()], as_of=date(2026, 9, 29)).proposals
    assert build([lot()], as_of=date(2026, 9, 28)).excluded[0]['reason_code'] == 'NOT_DUE'
    holiday = [d for d in CALENDAR if d != date(2026, 9, 15)]
    assert build([lot()], calendar=holiday).excluded[0]['reason_code'] == 'NOT_DUE'
    assert build([lot()], calendar=CALENDAR[:10]).excluded[0]['reason_code'] == 'INSUFFICIENT_CALENDAR'


def test_06_to_10_quantity_aggregation_open_sell_and_ceil_ticks():
    assert build([lot(qty=50, held=50)]).excluded[0]['reason_code'] == 'BELOW_LOT'
    assert build([lot(qty=100, held=200)]).proposals[0].qty == 100
    combined = build([lot(pid='A1-a', qty=100, held=250), lot(pid='A1-b', qty=100, held=250)])
    assert combined.proposals[0].qty == 200
    assert [x['source_proposal_id'] for x in combined.proposals[0].events['exit_lots']] == ['A1-a', 'A1-b']
    assert build([lot(open_sell=True)]).excluded[0]['reason_code'] == 'OPEN_SELL_EXISTS'
    rows = [lot('0999', 'a'), lot('1234', 'b'), lot('6000', 'c')]
    result = build(rows, prices={'0999': 999, '1234': 1234, '6000': 6000})
    assert {p.code: p.limit_price for p in result.proposals} == {'0999': 995.0, '1234': 1230.0, '6000': 5970.0}


@pytest.mark.parametrize('price', [None, 0, -1, float('nan')])
def test_11_missing_or_invalid_price_is_local_exclusion(price):
    result = build([lot(), lot('6857', 'other')], prices={'7203': price, '6857': 2000})
    assert [p.code for p in result.proposals] == ['6857']
    assert result.excluded == [{'code': '7203', 'source_proposal_id': 'A1-20260901-7203-01',
                                'reason_code': 'MISSING_PRICE'}]


def test_12_to_15_unknown_events_hash_scope_determinism_and_input_immutability():
    rows = [lot()]; before = copy.deepcopy(rows); prices = {'7203': 2000}; prices_before = dict(prices)
    a = build_exit_proposals(rows, AS_OF, prices, {'7203': 100}, {}, 'a' * 64, 'v1', 20,
                             business_days=CALENDAR)
    b = build_exit_proposals(list(reversed(rows)), AS_OF, prices, {'7203': 100}, {}, 'a' * 64, 'v1', 20,
                             business_days=CALENDAR)
    assert a.proposals == b.proposals
    assert a.proposals[0].events['next_earnings_date'] == 'UNKNOWN'
    changed = copy.deepcopy(a.proposals[0].events); changed['exit_lots'][0]['source_proposal_id'] = 'changed'
    assert packet_hash(replace(a.proposals[0], events=changed)) == a.proposals[0].packet_hash
    assert rows == before and prices == prices_before


def test_fixed_packet_hash():
    expected = json.loads((Path(__file__).parent / 'fixtures' / 'exit_v6_expected.json').read_text(encoding='utf-8'))
    proposal = build([lot(pid='A1-20260901-7203-01', held=300, unattributed=100),
                      lot(pid='A1-20260901-7203-02', held=300, unattributed=100)]).proposals[0]
    assert proposal.qty == expected['fixed_example']['qty']
    assert proposal.packet_hash == expected['fixed_example']['packet_hash']
    assert {i for group in expected['counterexample_groups'] for i in group} == set(range(1, 46))


def test_16_17_real_gate_and_ledger_accept_generated_exit(tmp_path):
    proposal = build([lot()]).proposals[0]
    view_ledger = Ledger(tmp_path / 'ledger.sqlite')
    view_ledger.init_snapshot(1_000_000, [dict(code='7203', qty=100, avg_price=1000)], [],
                              datetime(2026, 9, 1, 7, tzinfo=JST))
    verdicts = [SimpleNamespace(judge=judge, proposal_id=proposal.proposal_id,
                                packet_hash=proposal.packet_hash, decision='APPROVE',
                                confidence=None, risks=()) for judge in ('claude', 'codex')]
    gate = evaluate(proposal, verdicts, datetime(2026, 9, 30, 7, tzinfo=JST),
                    view_ledger.view(), Limits(), 1_000_000, 1_000_000, 99, True, True,
                    business_days=CALENDAR)
    assert gate.allowed and gate.category == 'EXIT' and gate.reserve_amount == 0
    view_ledger.create_notice(proposal, at=datetime(2026, 9, 30, 7, tzinfo=JST))
    assert view_ledger.view().reserved_shares == {'7203': 100}
    view_ledger.close()


def test_18_to_20_invalid_dates_quantities_duplicates_and_no_initial_lot():
    with pytest.raises(ValueError, match='entry_date'):
        build([lot(entry=date(2026, 9, 30))])
    with pytest.raises(ValueError, match='lot_qty'):
        build([lot(qty=200, held=100)])
    with pytest.raises(ValueError, match='duplicate'):
        build([lot(), lot()])
    assert build([]).proposals == []


def test_24_28_29_30_allocation_observation_version_and_balance_guards():
    result = build([lot(pid='a', qty=100, held=200),
                    lot(pid='b', qty=100, held=200)], lots={'7203': 150})
    assert result.proposals[0].events['exit_lots'] == [
        {'source_proposal_id': 'a', 'qty': 100}, {'source_proposal_id': 'b', 'qty': 50}]
    with pytest.raises(ValueError, match='MIXED_OBSERVATION'):
        build([lot(pid='a'), lot(pid='b', seq=43)])
    mixed = build([lot(pid='a', version='v1', held=200), lot(pid='b', version='v2', held=200)])
    assert {x['reason_code'] for x in mixed.excluded} == {'MIXED_STRATEGY_VERSION'}
    with pytest.raises(ValueError, match='exceed'):
        build([lot(pid='a', qty=100, held=150), lot(pid='b', qty=100, held=150)])


def _proposal(pid, side='BUY', code='7203', qty=100, *, events=None, external=False):
    p = Proposal(pid, '', code, side, qty, 100, 1000.0, 'EXTERNAL' if external else 'OPENING_LIMIT',
                 'CASH', 'margin_bucket_long', 'v1', date(2026, 9, 1), 'a' * 64, 'v1',
                 datetime(2026, 10, 1, 8, 59, tzinfo=JST), '', events or {})
    return Proposal(**{**p.__dict__, 'packet_hash': packet_hash(p)})


def _journal(path, rows=()):
    with closing(sqlite3.connect(path)) as con:
        con.execute('CREATE TABLE candidates(pid TEXT PRIMARY KEY, hash TEXT, day TEXT, side TEXT, state TEXT, result TEXT, owner TEXT)')
        con.executemany('INSERT INTO candidates VALUES(?,?,?,?,?,?,?)',
                        [(pid, '', '2026-09-29', side, state, '', '') for pid, side, state in rows])
        con.commit()


def test_21_23_31_32_34_37_39_40_45_real_ledger_bundle(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.sqlite')
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger.init_snapshot(1_000_000, [], [], at)
    buy = _proposal('buy')
    ledger.create_notice(buy, at=at)
    assert ledger.report(TradeEvent('f1', 'buy', 'PARTIAL', 60, 1000, 0, at, 'manual')).applied
    assert ledger.report(TradeEvent('f2', 'buy', 'FILLED', 40, 1000, 0, at + timedelta(days=1), 'manual')).applied
    journal = tmp_path / 'orchestration.sqlite'; _journal(journal, [('buy', 'BUY', 'APPROVED')])
    snapshot = {'positions': [], 'open_orders': [], 'at': at.isoformat()}
    derived = derive_holdings(ledger, journal, snapshot, [], AS_OF)
    assert derived.reason_codes == [] and derived.holdings[0]['entry_date'] == date(2026, 9, 1)
    assert derived.holdings[0]['lot_qty'] == 100 and derived.candidate_ids == ('buy',)
    assert derive_holdings(ledger, journal, None, [], AS_OF).reason_codes == ['SNAPSHOT_MISSING']
    missing = tmp_path / 'missing.sqlite'; _journal(missing)
    assert derive_holdings(ledger, missing, snapshot, [], AS_OF).reason_codes == ['LEDGER_INCONSISTENT']
    ledger.adjust('SPLIT', None, '7203', 2, at + timedelta(days=9), 'split')
    split = [{'code': '7203', 'ratio': 2, 'at': (at + timedelta(days=9)).isoformat()}]
    assert derive_holdings(ledger, journal, snapshot, split, AS_OF).holdings[0]['lot_qty'] == 200
    assert build(derive_holdings(ledger, journal, snapshot, split, AS_OF).holdings).proposals[0].qty == 200
    assert derive_holdings(ledger, journal, snapshot, [], AS_OF).reason_codes == ['LEDGER_INCONSISTENT']
    ledger.close()


def test_33_reverse_split_leaves_below_lot(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.sqlite'); at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger.init_snapshot(1_000_000, [], [], at); buy = _proposal('buy')
    ledger.create_notice(buy, at=at); assert ledger.report(TradeEvent('f', 'buy', 'FILLED', 100, 1000, 0, at, 'manual')).applied
    ledger.adjust('SPLIT', None, '7203', .5, at + timedelta(days=1), 'reverse')
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy', 'BUY', 'APPROVED')])
    snapshot = {'positions': [], 'open_orders': [], 'at': at.isoformat()}
    holdings = derive_holdings(ledger, journal, snapshot,
                               [{'code': '7203', 'ratio': .5, 'at': (at + timedelta(days=1)).isoformat()}], AS_OF).holdings
    assert build(holdings).excluded[0]['reason_code'] == 'BELOW_LOT'
    ledger.close()


def test_37_38_intent_stops_before_ledger_read(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.sqlite'); at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger.init_snapshot(1_000_000, [], [], at)
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('pending', 'BUY', 'INTENT')])
    result = derive_holdings(ledger, journal, {'positions': [], 'open_orders': [], 'at': at.isoformat()}, [], AS_OF)
    assert result.reason_codes == ['RUN_INCOMPLETE']
    ledger.close()


def test_38_intent_with_real_notice_still_stops(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite'); ledger.init_snapshot(1_000_000, [], [], at)
    ledger.create_notice(_proposal('pending'), at=at)
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('pending', 'BUY', 'INTENT')])
    result = derive_holdings(ledger, journal, {'positions': [], 'open_orders': [], 'at': at.isoformat()}, [], AS_OF)
    assert result.reason_codes == ['RUN_INCOMPLETE']
    ledger.close()


def test_39_unconfirmed_notice_outside_candidates_is_inconsistent(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite')
    ledger.init_snapshot(1_000_000, [dict(code='7203', qty=100, avg_price=900)], [], at)
    ledger.create_notice(_proposal('unknown-sell', 'SELL'), at=at)
    ledger.set_notice_state('unknown-sell', 'APPROVED', at)
    ledger.set_notice_state('unknown-sell', 'SENT', at)
    journal = tmp_path / 'j.sqlite'; _journal(journal)
    snapshot = {'positions': [dict(code='7203', qty=100)], 'open_orders': [], 'at': at.isoformat()}
    assert derive_holdings(ledger, journal, snapshot, [], AS_OF).reason_codes == ['LEDGER_INCONSISTENT']
    ledger.close()


def test_40_real_runner_candidates_cover_both_created_notices(tmp_path):
    day = date(2026, 9, 29); now = datetime(2026, 9, 29, 7, 10, tzinfo=JST)
    home = tmp_path / 'mock'; initialize_mock(home, 1_000_000, [], now - timedelta(days=1)); init(home)
    with connect(home) as con:
        for offset in range(-10, 31):
            d = day + timedelta(days=offset)
            con.execute('INSERT INTO calendar VALUES(?,?)', [d, d.weekday() < 5])
        con.executemany('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)',
                        [('7203', day - timedelta(days=1), 1000.), ('6857', day - timedelta(days=1), 1000.)])
    proposals = []
    for pid, code in [('buy-1', '7203'), ('buy-2', '6857')]:
        p = replace(_proposal(pid, code=code,
                              events={'next_earnings_date': None, 'margin_regulated': False}),
                    as_of=day - timedelta(days=1), expires_at=datetime.combine(day, datetime.min.time(), JST).replace(hour=8, minute=59),
                    packet_hash='')
        proposals.append(replace(p, packet_hash=packet_hash(p)))
    verdicts = mock_verdicts(proposals, 'exit-v6', now)
    result = run(home, 'exit-v6', day, proposals, verdicts, now, Valuation(1_000_000, 1_000_000))
    assert [r['status'] for r in result['candidates']] == ['APPROVED', 'APPROVED']
    with closing(sqlite3.connect(home / 'orchestration.sqlite')) as con:
        candidate_ids = {row[0] for row in con.execute('SELECT pid FROM candidates')}
    with closing(Ledger(home / 'ledger.sqlite')) as ledger:
        assert candidate_ids == {'buy-1', 'buy-2'}
        assert all(ledger.proposal(pid)['proposal_id'] == pid for pid in candidate_ids)


def test_45_missing_snapshot_changes_neither_ledger_nor_journal(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite'); ledger.init_snapshot(1_000_000, [], [], at)
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('rejected', 'BUY', 'REJECTED')])
    seq = ledger.seq(); before = hashlib.sha256(journal.read_bytes()).hexdigest()
    with closing(sqlite3.connect(journal)) as con:
        rows = con.execute('SELECT * FROM candidates').fetchall()
    result = derive_holdings(ledger, journal, None, [], AS_OF)
    with closing(sqlite3.connect(journal)) as con:
        after_rows = con.execute('SELECT * FROM candidates').fetchall()
    assert result.reason_codes == ['SNAPSHOT_MISSING']
    assert ledger.seq() == seq and rows == after_rows
    assert hashlib.sha256(journal.read_bytes()).hexdigest() == before
    ledger.close()


def test_41_42_44_external_sell_is_matched_by_snapshot_input(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST); order = dict(proposal_id=None, code='7203', side='SELL', qty=100,
                                                            limit_price=1000, lot_size=100)
    ledger = Ledger(tmp_path / 'ledger.sqlite'); ledger.init_snapshot(1_000_000,
        [dict(code='7203', qty=200, avg_price=900)], [order], at)
    journal = tmp_path / 'j.sqlite'; _journal(journal)
    snapshot = {'positions': [dict(code='7203', qty=200)], 'open_orders': [order], 'at': at.isoformat()}
    result = derive_holdings(ledger, journal, snapshot, [], AS_OF)
    assert result.reason_codes == [] and result.holdings == []
    altered = copy.deepcopy(snapshot); altered['open_orders'] = []
    assert derive_holdings(ledger, journal, altered, [], AS_OF).reason_codes == ['LEDGER_INCONSISTENT']
    ledger.close()


def test_42_open_external_sell_excludes_its_code_but_not_other_codes(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    order = dict(proposal_id=None, code='7203', side='SELL', qty=100, limit_price=1000, lot_size=100)
    ledger = Ledger(tmp_path / 'ledger.sqlite')
    ledger.init_snapshot(1_000_000, [dict(code='7203', qty=100, avg_price=900)], [order], at)
    for pid, code in [('buy-7203', '7203'), ('buy-6857', '6857')]:
        p = _proposal(pid, code=code); ledger.create_notice(p, at=at)
        assert ledger.report(TradeEvent('fill-' + pid, pid, 'FILLED', 100, 1000, 0, at, 'manual')).applied
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy-7203', 'BUY', 'APPROVED'),
                                                        ('buy-6857', 'BUY', 'APPROVED')])
    snapshot = {'positions': [dict(code='7203', qty=100)], 'open_orders': [order], 'at': at.isoformat()}
    holdings = derive_holdings(ledger, journal, snapshot, [], AS_OF).holdings
    result = build(holdings)
    assert [p.code for p in result.proposals] == ['6857']
    assert {(x['code'], x['reason_code']) for x in result.excluded} == {('7203', 'OPEN_SELL_EXISTS')}
    ledger.close()


def test_22_23_buy_correction_and_pending_apply_use_fill_at(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite'); ledger.init_snapshot(1_000_000, [], [], at)
    buy = _proposal('buy'); ledger.create_notice(buy, at=at)
    assert ledger.report(TradeEvent('old', 'buy', 'FILLED', 100, 1000, 0, at, 'manual')).applied
    replacement_at = at + timedelta(days=2)
    assert ledger.report(TradeEvent('new', 'buy', 'CORRECTION', 100, 1000, 0,
                                    replacement_at, 'manual', replaces_event_id='old')).applied
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy', 'BUY', 'APPROVED')])
    snapshot = {'positions': [], 'open_orders': [], 'at': at.isoformat()}
    assert derive_holdings(ledger, journal, snapshot, [], AS_OF).holdings[0]['entry_date'] == replacement_at.date()
    ledger.close()

    pending = Ledger(tmp_path / 'pending.sqlite'); pending.init_snapshot(1_000_000, [], [], at)
    row = CsvFill('csv', None, '7203', 'BUY', 100, 1000, 0, at, broker_order_id='broker')
    assert pending.import_csv_fills([row]).pending == ['csv']
    pending.create_notice(buy, at=at + timedelta(days=1))
    pending.resolve_pending('csv', 'buy', at + timedelta(days=2), 'APPLY')
    journal2 = tmp_path / 'j2.sqlite'; _journal(journal2, [('buy', 'BUY', 'APPROVED')])
    assert derive_holdings(pending, journal2, snapshot, [], AS_OF).holdings[0]['entry_date'] == at.date()
    pending.close()


def test_25_linked_sell_consumes_exit_lots_front_to_back(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite'); ledger.init_snapshot(1_000_000, [], [], at)
    for pid in ('buy-a', 'buy-b'):
        p = _proposal(pid); ledger.create_notice(p, at=at)
        assert ledger.report(TradeEvent('fill-' + pid, pid, 'FILLED', 100, 1000, 0, at, 'manual')).applied
    sell = _proposal('sell', 'SELL', qty=200, events={'exit_lots': [
        {'source_proposal_id': 'buy-a', 'qty': 100}, {'source_proposal_id': 'buy-b', 'qty': 100}]})
    ledger.create_notice(sell, at=at + timedelta(days=21))
    ledger.set_notice_state('sell', 'APPROVED', at + timedelta(days=21))
    ledger.set_notice_state('sell', 'SENT', at + timedelta(days=21))
    assert ledger.report(TradeEvent('sell-fill', 'sell', 'PARTIAL', 150, 1000, 0,
                                    at + timedelta(days=21), 'manual')).applied
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy-a', 'BUY', 'APPROVED'),
        ('buy-b', 'BUY', 'APPROVED'), ('sell', 'SELL', 'APPROVED')])
    snapshot = {'positions': [], 'open_orders': [], 'at': at.isoformat()}
    result = derive_holdings(ledger, journal, snapshot, [], AS_OF)
    assert [(h['source_proposal_id'], h['lot_qty'], h['open_sell_notice']) for h in result.holdings] == [
        ('buy-b', 50, True)]
    ledger.close()


@pytest.mark.parametrize(('sell_qty', 'expected_lot'), [(100, 100), (150, 50)])
def test_26_27_unlinked_sell_consumes_unattributed_then_oldest_lot(tmp_path, sell_qty, expected_lot):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite')
    ledger.init_snapshot(1_000_000, [dict(code='7203', qty=100, avg_price=900)], [], at)
    buy = _proposal('buy'); ledger.create_notice(buy, at=at)
    assert ledger.report(TradeEvent('buy-fill', 'buy', 'FILLED', 100, 1000, 0, at, 'manual')).applied
    sell = _proposal('sell', 'SELL', qty=sell_qty); ledger.create_notice(sell, at=at + timedelta(days=1))
    assert ledger.report(TradeEvent('sell-fill', 'sell', 'FILLED', sell_qty, 1000, 0,
                                    at + timedelta(days=1), 'manual')).applied
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy', 'BUY', 'APPROVED'), ('sell', 'SELL', 'APPROVED')])
    snapshot = {'positions': [dict(code='7203', qty=100)], 'open_orders': [], 'at': at.isoformat()}
    result = derive_holdings(ledger, journal, snapshot, [], AS_OF)
    assert result.holdings[0]['lot_qty'] == expected_lot
    assert result.holdings[0]['unattributed_qty'] == 0
    ledger.close()


def test_35_seq_change_retries_three_times_then_stops(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    real = Ledger(tmp_path / 'ledger.sqlite'); real.init_snapshot(1_000_000, [], [], at)
    journal = tmp_path / 'j.sqlite'; _journal(journal)
    class Changing:
        def __init__(self, ledger): self.ledger, self.value = ledger, 0
        def seq(self): self.value += 1; return self.value
        def __getattr__(self, name): return getattr(self.ledger, name)
    result = derive_holdings(Changing(real), journal,
        {'positions': [], 'open_orders': [], 'at': at.isoformat()}, [], AS_OF)
    assert result.reason_codes == ['SEQ_CHANGED']
    real.close()


def test_36_sell_price_correction_keeps_quantity_and_quantity_change_is_rejected(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite')
    ledger.init_snapshot(1_000_000, [], [], at)
    buy = _proposal('buy', qty=200); ledger.create_notice(buy, at=at)
    assert ledger.report(TradeEvent('buy-fill', 'buy', 'FILLED', 200, 900, 0, at, 'manual')).applied
    sell = _proposal('sell', 'SELL', qty=200,
                     events={'exit_lots': [{'source_proposal_id': 'buy', 'qty': 200}]})
    ledger.create_notice(sell, at=at)
    assert ledger.report(TradeEvent('fill', 'sell', 'PARTIAL', 100, 1000, 0, at, 'manual')).applied
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy', 'BUY', 'APPROVED'),
                                                        ('sell', 'SELL', 'APPROVED')])
    snapshot = {'positions': [], 'open_orders': [], 'at': at.isoformat()}
    before = derive_holdings(ledger, journal, snapshot, [], AS_OF)
    assert [(h['source_proposal_id'], h['lot_qty']) for h in before.holdings] == [('buy', 100)]
    assert ledger.report(TradeEvent('price-fix', 'sell', 'CORRECTION', 100, 990, 0,
                                    at + timedelta(hours=1), 'manual', replaces_event_id='fill')).applied
    after_price = derive_holdings(ledger, journal, snapshot, [], AS_OF)
    stable = lambda rows: [{k: v for k, v in row.items() if k != 'observed_seq'} for row in rows]
    assert stable(after_price.holdings) == stable(before.holdings)
    rejected = ledger.report(TradeEvent('qty-fix', 'sell', 'CORRECTION', 50, 990, 0,
                                        at + timedelta(hours=2), 'manual', replaces_event_id='price-fix'))
    assert not rejected.applied and 'SELL数量' in rejected.error
    assert ledger.notice('sell')['filled_qty'] == 100
    assert stable(derive_holdings(ledger, journal, snapshot, [], AS_OF).holdings) == stable(before.holdings)
    ledger.close()


def test_43_completed_external_sell_consumes_snapshot_before_buy_lot(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    order = dict(proposal_id=None, code='7203', side='SELL', qty=100, limit_price=1000, lot_size=100)
    ledger = Ledger(tmp_path / 'ledger.sqlite')
    ledger.init_snapshot(1_000_000, [dict(code='7203', qty=100, avg_price=900)], [order], at)
    assert ledger.report(TradeEvent('external-fill', 'external-1', 'FILLED', 100, 1000, 0, at, 'manual')).applied
    buy = _proposal('buy'); ledger.create_notice(buy, at=at + timedelta(days=1))
    assert ledger.report(TradeEvent('buy-fill', 'buy', 'FILLED', 100, 1000, 0,
                                    at + timedelta(days=1), 'manual')).applied
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy', 'BUY', 'APPROVED')])
    snapshot = {'positions': [dict(code='7203', qty=100)], 'open_orders': [order], 'at': at.isoformat()}
    result = derive_holdings(ledger, journal, snapshot, [], AS_OF)
    assert result.reason_codes == [] and result.holdings[0]['lot_qty'] == 100
    assert result.holdings[0]['unattributed_qty'] == 0
    ledger.close()


def test_43_external_sell_consumes_buy_lot_after_unlinked_sell_uses_initial_balance(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    order = dict(proposal_id=None, code='7203', side='SELL', qty=100, limit_price=1000, lot_size=100)
    ledger = Ledger(tmp_path / 'ledger.sqlite')
    ledger.init_snapshot(1_000_000, [dict(code='7203', qty=100, avg_price=900)], [order], at)
    buy = _proposal('buy', qty=200); ledger.create_notice(buy, at=at)
    assert ledger.report(TradeEvent('buy-fill', 'buy', 'FILLED', 200, 900, 0, at, 'manual')).applied
    unlinked = _proposal('unlinked', 'SELL', qty=100); ledger.create_notice(unlinked, at=at)
    assert ledger.report(TradeEvent('unlinked-fill', 'unlinked', 'FILLED', 100, 1000, 0,
                                    at + timedelta(hours=1), 'manual')).applied
    assert ledger.report(TradeEvent('external-fill', 'external-1', 'FILLED', 100, 1000, 0,
                                    at + timedelta(hours=2), 'manual')).applied
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy', 'BUY', 'APPROVED'),
                                                        ('unlinked', 'SELL', 'APPROVED')])
    snapshot = {'positions': [dict(code='7203', qty=100)], 'open_orders': [order], 'at': at.isoformat()}
    derived = derive_holdings(ledger, journal, snapshot, [], AS_OF)
    assert derived.reason_codes == []
    assert [(h['source_proposal_id'], h['lot_qty'], h['unattributed_qty']) for h in derived.holdings] == [
        ('buy', 100, 0)]
    proposals = build(derived.holdings).proposals
    assert len(proposals) == 1 and proposals[0].code == '7203' and proposals[0].qty == 100
    ledger.close()


def test_known_approved_sell_is_open_before_delivery(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite'); ledger.init_snapshot(1_000_000, [], [], at)
    buy = _proposal('buy'); ledger.create_notice(buy, at=at)
    assert ledger.report(TradeEvent('buy-fill', 'buy', 'FILLED', 100, 1000, 0, at, 'manual')).applied
    sell = _proposal('sell', 'SELL'); ledger.create_notice(sell, at=at + timedelta(days=1))
    ledger.set_notice_state('sell', 'APPROVED', at + timedelta(days=1))
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy', 'BUY', 'APPROVED'), ('sell', 'SELL', 'APPROVED')])
    snapshot = {'positions': [], 'open_orders': [], 'at': at.isoformat()}
    assert derive_holdings(ledger, journal, snapshot, [], AS_OF).holdings[0]['open_sell_notice'] is True
    ledger.close()


def test_split_unresolved_excludes_only_affected_code(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite'); ledger.init_snapshot(1_000_000, [], [], at)
    for pid, code, qty in [('buy-7203-a', '7203', 1), ('buy-7203-b', '7203', 1), ('buy-6857', '6857', 100)]:
        p = _proposal(pid, code=code, qty=qty); ledger.create_notice(p, at=at)
        assert ledger.report(TradeEvent('fill-' + pid, pid, 'FILLED', qty, 1000, 0, at, 'manual')).applied
    split_at = at + timedelta(days=1)
    ledger.adjust('SPLIT', None, '7203', .5, split_at, 'reverse')
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy-7203-a', 'BUY', 'APPROVED'),
        ('buy-7203-b', 'BUY', 'APPROVED'), ('buy-6857', 'BUY', 'APPROVED')])
    snapshot = {'positions': [], 'open_orders': [], 'at': at.isoformat()}
    result = derive_holdings(ledger, journal, snapshot,
        [{'code': '7203', 'ratio': .5, 'at': split_at.isoformat()}], AS_OF)
    assert [h['code'] for h in result.holdings] == ['6857']
    assert result.reason_codes == []
    assert result.excluded == (
        {'code': '7203', 'source_proposal_id': 'buy-7203-a', 'reason_code': 'SPLIT_UNRESOLVED'},
        {'code': '7203', 'source_proposal_id': 'buy-7203-b', 'reason_code': 'SPLIT_UNRESOLVED'},)
    ledger.close()


def test_one_buy_lot_two_partial_fills_can_resolve_to_integer_after_split(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite'); ledger.init_snapshot(1_000_000, [], [], at)
    buy = _proposal('buy', qty=2); ledger.create_notice(buy, at=at)
    assert ledger.report(TradeEvent('part', 'buy', 'PARTIAL', 1, 1000, 0, at, 'manual')).applied
    assert ledger.report(TradeEvent('final', 'buy', 'FILLED', 1, 1000, 0,
                                    at + timedelta(minutes=1), 'manual')).applied
    split_at = at + timedelta(days=1)
    ledger.adjust('SPLIT', None, '7203', .5, split_at, 'reverse')
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy', 'BUY', 'APPROVED')])
    snapshot = {'positions': [], 'open_orders': [], 'at': at.isoformat()}
    result = derive_holdings(ledger, journal, snapshot,
                             [{'code': '7203', 'ratio': .5, 'at': split_at.isoformat()}], AS_OF)
    assert result.reason_codes == [] and result.excluded == ()
    assert result.holdings[0]['lot_qty'] == 1
    ledger.close()


def test_sell_fills_are_consumed_in_business_time_not_pid_order(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite'); ledger.init_snapshot(1_000_000, [], [], at)
    for pid in ('buy-a', 'buy-b'):
        p = _proposal(pid); ledger.create_notice(p, at=at)
        assert ledger.report(TradeEvent('fill-' + pid, pid, 'FILLED', 100, 1000, 0, at, 'manual')).applied
    linked = _proposal('z-linked', 'SELL', qty=100,
                       events={'exit_lots': [{'source_proposal_id': 'buy-a', 'qty': 100}]})
    ledger.create_notice(linked, at=at + timedelta(days=1))
    assert ledger.report(TradeEvent('linked-fill', 'z-linked', 'FILLED', 100, 1000, 0,
                                    at + timedelta(days=1), 'manual')).applied
    unlinked = _proposal('a-unlinked', 'SELL', qty=50)
    ledger.create_notice(unlinked, at=at + timedelta(days=2))
    assert ledger.report(TradeEvent('unlinked-fill', 'a-unlinked', 'FILLED', 50, 1000, 0,
                                    at + timedelta(days=2), 'manual')).applied
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('buy-a', 'BUY', 'APPROVED'),
        ('buy-b', 'BUY', 'APPROVED'), ('z-linked', 'SELL', 'APPROVED'), ('a-unlinked', 'SELL', 'APPROVED')])
    snapshot = {'positions': [], 'open_orders': [], 'at': at.isoformat()}
    result = derive_holdings(ledger, journal, snapshot, [], AS_OF)
    assert result.reason_codes == []
    assert [(h['source_proposal_id'], h['lot_qty']) for h in result.holdings] == [('buy-b', 50)]
    ledger.close()


def test_same_jst_entry_day_fifo_uses_pid_not_fill_clock(tmp_path):
    at = datetime(2026, 9, 1, 7, tzinfo=JST)
    ledger = Ledger(tmp_path / 'ledger.sqlite'); ledger.init_snapshot(1_000_000, [], [], at)
    for pid, hour in [('z-lot', 9), ('a-lot', 15)]:
        p = _proposal(pid); ledger.create_notice(p, at=at)
        fill_at = at.replace(hour=hour)
        assert ledger.report(TradeEvent('fill-' + pid, pid, 'FILLED', 100, 1000, 0, fill_at, 'manual')).applied
    sell = _proposal('sell', 'SELL', qty=50); ledger.create_notice(sell, at=at + timedelta(days=1))
    assert ledger.report(TradeEvent('sell-fill', 'sell', 'FILLED', 50, 1000, 0,
                                    at + timedelta(days=1), 'manual')).applied
    journal = tmp_path / 'j.sqlite'; _journal(journal, [('z-lot', 'BUY', 'APPROVED'),
        ('a-lot', 'BUY', 'APPROVED'), ('sell', 'SELL', 'APPROVED')])
    snapshot = {'positions': [], 'open_orders': [], 'at': at.isoformat()}
    result = derive_holdings(ledger, journal, snapshot, [], AS_OF)
    assert [(h['source_proposal_id'], h['lot_qty']) for h in result.holdings] == [('a-lot', 50), ('z-lot', 100)]
    ledger.close()
