"""Offline integration evidence for explicit SELL/EXIT proposals.

These fixtures exercise the existing downstream contract.  They do not define
an exit strategy or derive sell candidates from market data.
"""
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
import socket
import subprocess

import pytest

from aitrader.db import connect, init
from aitrader.mock_demo import _record
from aitrader.mock_delivery import deliver_prepared_mock
from aitrader.notification_plan import prepare_notifications
from aitrader.notification_queue import enqueue_prepared_notifications
from aitrader.packet import Proposal, packet_hash
from aitrader.review_runner import run_reviewed_mock
from aitrader.runner import Valuation, initialize_mock
from aitrader_ops.ledger import Ledger
from aitrader_ops.models import JST, PositionIn
from aitrader_ops.notify import Notifier


DAY = date(2026, 9, 8)
AS_OF = date(2026, 9, 7)
NOW = datetime(2026, 9, 8, 7, 10, tzinfo=JST)
SETTINGS = {
    'broker': {'link_template': 'https://www.rakuten-sec.co.jp/web/market/search/{code}'},
    'line': {'allowed_user_id': 'explicit-exit-fixture', 'monthly_budget': 100},
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('EXIT integration attempted a process, socket, or implicit delivery')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def proposal(proposal_id, *, side='SELL', code='6857', qty=100):
    value = Proposal(
        proposal_id=proposal_id, packet_hash='', code=code, side=side, qty=qty,
        lot_size=100, limit_price=100.0, exec_condition='OPENING_LIMIT',
        account_type='CASH', strategy='explicit_exit_fixture',
        strategy_version='v1', as_of=AS_OF, snapshot_id='exit-snapshot',
        policy_version='review-v1',
        expires_at=datetime(2026, 9, 8, 8, 59, tzinfo=JST),
        reason='明示した境界試験用Proposal',
        events={'next_earnings_date': None, 'margin_regulated': False},
    )
    return replace(value, packet_hash=packet_hash(value))


def recorded_responses(proposals, run_id, *, omit=()):
    responses = {}
    for value in proposals:
        answer = {
            'proposal_id': value.proposal_id, 'packet_hash': value.packet_hash,
            'decision': 'APPROVE', 'risks': [], 'reason': '明示stub承認',
            'confidence': None,
        }
        responses[value.proposal_id] = {
            judge: {'record': _record(judge, answer), 'received_at': NOW}
            for judge in ('claude', 'codex') if judge not in omit
        }
    return responses


@pytest.fixture
def exit_home(tmp_path):
    home = tmp_path/'explicit-exit'
    initialized = NOW-timedelta(days=1)
    initialize_mock(home, 1_000_000,
                    [PositionIn('6857', 200, 100.0)], initialized)
    init(home)
    with connect(home) as con:
        for offset in range(-3, 4):
            day = DAY+timedelta(days=offset)
            con.execute('INSERT INTO calendar VALUES(?,?)', [day, day.weekday() < 5])
        con.executemany('INSERT INTO prices_daily(code,date,close) VALUES(?,?,?)', [
            ('6857', AS_OF, 100.0), ('7203', AS_OF, 100.0),
        ])
    return home


def run_pipeline(home, proposals, *, run_id='exit-run', omit=(), stop=False):
    if stop:
        (home/'STOP').write_text('explicit EXIT boundary', encoding='utf-8')
    result = run_reviewed_mock(
        home, run_id, DAY, proposals,
        recorded_responses(proposals, run_id, omit=omit), NOW,
        Valuation(1_020_000, 1_020_000), started_at=NOW,
        market_context={},
    )
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        available = ledger.available()
    contexts = {
        value.proposal_id: {
            'name': '明示境界銘柄', 'account_confirmed_at': NOW.isoformat(),
            'available_after': available, 'new_sent_today': 0,
            'max_new_per_day': 2,
        } for value in proposals
    }
    plan = prepare_notifications(home, run_id, DAY, contexts=contexts,
                                 settings=SETTINGS, include_status=False)
    queue = enqueue_prepared_notifications(
        home, run_id, DAY, now=NOW, contexts=contexts, settings=SETTINGS,
        include_status=False,
    )
    return result, plan, queue, contexts


def ledger_state(home):
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        view = ledger.view()
        return view.cash, view.positions['6857'].qty, ledger.reserved(), view.reserved_shares


def test_approved_sell_becomes_unsent_exit_and_reserves_only_shares(exit_home):
    sell = proposal('sell-100')
    result, plan, queue, _ = run_pipeline(exit_home, [sell])

    assert result['candidates'][0]['status'] == 'APPROVED'
    cards = plan['plans']
    assert len(cards) == 1 and cards[0]['kind'] == 'EXIT'
    assert cards[0]['proposal_id'] == sell.proposal_id
    assert queue['delivery'] == 'NOT_SENT'
    assert queue['entries'][0]['state'] == 'PENDING'
    assert ledger_state(exit_home) == (1_000_000, 200, 0, {'6857': 100})


def test_multiple_sells_cannot_reserve_more_than_current_holding(exit_home):
    proposals = [proposal('sell-first', qty=100), proposal('sell-second', qty=200)]
    result, plan, _, _ = run_pipeline(exit_home, proposals)

    candidates = {item['proposal_id']: item for item in result['candidates']}
    assert candidates['sell-first']['status'] == 'APPROVED'
    assert candidates['sell-second']['status'] == 'REJECTED'
    assert 'INSUFFICIENT_SHARES' in candidates['sell-second']['gate']['reason_codes']
    assert [card['proposal_id'] for card in plan['plans']] == ['sell-first']
    assert ledger_state(exit_home)[2:] == (0, {'6857': 100})


def test_stop_does_not_block_exit(exit_home):
    result, plan, _, _ = run_pipeline(exit_home, [proposal('sell-stop')], stop=True)

    candidate = result['candidates'][0]
    assert candidate['status'] == 'APPROVED'
    assert 'STOP_NEW' not in candidate['gate']['reason_codes']
    assert plan['plans'][0]['kind'] == 'EXIT'
    assert ledger_state(exit_home)[2:] == (0, {'6857': 100})


@pytest.mark.parametrize('missing_judge', ['claude', 'codex'])
def test_exit_without_both_reviews_is_not_approved_or_reserved(exit_home, missing_judge):
    sell = proposal('sell-missing')
    result, plan, queue, _ = run_pipeline(exit_home, [sell], omit=(missing_judge,))

    assert result['status'] == 'REVIEW_INCOMPLETE'
    assert result['candidates'][0]['status'] != 'APPROVED'
    assert 'REVIEW_INCOMPLETE' in result['candidates'][0]['gate']['reason_codes']
    assert plan['plans'] == [] and queue['entries'] == []
    assert ledger_state(exit_home)[2:] == (0, {})


def test_buy_and_sell_cards_have_distinct_keys_and_kinds(exit_home):
    values = [proposal('sell-mixed'), proposal('buy-mixed', side='BUY', code='6857')]
    result, plan, queue, _ = run_pipeline(exit_home, values)

    assert all(item['status'] == 'APPROVED' for item in result['candidates'])
    cards = {item['proposal_id']: item for item in plan['plans']}
    assert cards['sell-mixed']['kind'] == 'EXIT'
    assert cards['buy-mixed']['kind'] == 'NEW'
    assert cards['sell-mixed']['key'] != cards['buy-mixed']['key']
    assert {item['key'] for item in queue['entries']} == {
        cards['sell-mixed']['key'], cards['buy-mixed']['key']}
    with closing(Ledger(exit_home/'ledger.sqlite')) as ledger:
        with closing(Notifier(ledger=ledger,
                              state_path=exit_home/'notification.sqlite',
                              settings=SETTINGS, transport='stub')) as notifier:
            assert notifier.get_entry(cards['sell-mixed']['key'])['kind'] == 'EXIT'
            assert notifier.get_entry(cards['buy-mixed']['key'])['kind'] == 'NEW'


def test_explicit_stub_delivery_does_not_apply_a_fill(exit_home):
    sell = proposal('sell-delivered')
    _, plan, _, contexts = run_pipeline(exit_home, [sell])
    before = ledger_state(exit_home)

    delivery = deliver_prepared_mock(
        exit_home, 'exit-run', DAY, now=NOW, contexts=contexts,
        settings=SETTINGS, stub_results=['success'],
    )

    assert delivery['transport'] == 'stub'
    assert delivery['simulated_sent_by_this_call'] is True
    assert delivery['real_sent_by_this_call'] is False
    assert ledger_state(exit_home) == before
    with closing(Ledger(exit_home/'ledger.sqlite')) as ledger:
        assert ledger.notice(sell.proposal_id)['notice_state'] == 'SENT'
    with closing(Ledger(exit_home/'ledger.sqlite')) as ledger:
        with closing(Notifier(ledger=ledger,
                              state_path=exit_home/'notification.sqlite',
                              settings=SETTINGS, transport='stub')) as notifier:
            entry = notifier.get_entry(plan['plans'][0]['key'])
            assert entry['state'] == 'SENT'
