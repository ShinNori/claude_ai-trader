"""Independent offline evidence for the virtual 06:30--07:15 rehearsal."""
import json
import socket
import subprocess
from contextlib import closing

import pytest

from aitrader.runner import RunError
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier


SCENARIOS = (
    'normal', 'data_missing', 'holiday', 'judge_missing', 'deadline',
    'stop_before_review',
)


@pytest.fixture(scope='module')
def rehearsals(tmp_path_factory):
    """Run each required path once; the shared market build is deliberately heavy."""
    from aitrader.daily_rehearsal import run_daily_rehearsal

    root = tmp_path_factory.mktemp('daily-rehearsal')
    outcomes = {}
    with pytest.MonkeyPatch.context() as patch:
        def forbidden(*args, **kwargs):
            pytest.fail('daily rehearsal attempted external I/O or delivery')

        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(socket, 'create_connection', forbidden)
        patch.setattr(socket.socket, 'connect', forbidden)
        patch.setattr(Notifier, 'flush', forbidden)
        for scenario in SCENARIOS:
            home = root/scenario
            summary = run_daily_rehearsal(home, scenario=scenario, seed=42)
            outcomes[scenario] = (home, summary)
    return outcomes


def _reserved(home):
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        return ledger.reserved()


def test_all_scenarios_are_virtual_persisted_and_never_sent(rehearsals):
    assert set(rehearsals) == set(SCENARIOS)
    for scenario, (home, summary) in rehearsals.items():
        saved = json.loads((home/'daily_rehearsal.json').read_text(encoding='utf-8'))
        assert saved == summary
        assert summary['scenario'] == scenario
        assert summary['mode'] == 'mock'
        assert summary['source'] == 'synthetic'
        assert summary['virtual_clock'] is True
        assert summary['delivery'] == 'NOT_SENT'
        assert summary['real_sent'] is False
        assert summary['real_sent_by_this_call'] is False


def test_virtual_stage_clock_and_early_exit_paths(rehearsals):
    expected = {
        'acquire_validate': '06:30:00',
        'generate': '06:50:00',
        'review': '07:00:00',
        'prepare': '07:00:00',
        'enqueue': '07:00:00',
        'deadline': '07:15:00',
    }
    for home, summary in rehearsals.values():
        assert {name: stage['scheduled_at'][11:19]
                for name, stage in summary['stages'].items()} == expected

    for scenario, status in [('data_missing', 'DATA_INCOMPLETE'),
                             ('holiday', 'NO_SESSION')]:
        home, summary = rehearsals[scenario]
        assert summary['status'] == status == summary['result']['status']
        assert all(summary['stages'][name]['status'] == 'SKIPPED'
                   for name in ('generate', 'review', 'prepare', 'enqueue'))
        assert not (home/'generated_proposals.json').exists()
        assert not (home/'notification.sqlite').exists()
        assert _reserved(home) == 0


def test_normal_reservations_and_pending_cards_match_approved_candidates(rehearsals):
    home, summary = rehearsals['normal']
    generated = json.loads((home/'generated_proposals.json').read_text(encoding='utf-8'))
    candidates = summary['result']['candidates']
    approved = [candidate for candidate in candidates if candidate['status'] == 'APPROVED']
    rejected = [candidate for candidate in candidates if candidate['status'] != 'APPROVED']
    assert len(generated) == len(candidates) == summary['generated_count']
    assert len(approved) == 2 and rejected
    assert all('DAILY_NEW_LIMIT' in candidate['gate']['reason_codes'] for candidate in rejected)
    assert summary['reservation'] == _reserved(home)
    assert summary['reservation'] == sum(c['gate']['reserve_amount'] for c in approved)
    assert {item['key'] for item in summary['plan']['plans']} == {
        item['key'] for item in summary['queue']['entries']}
    assert all(item['state'] == 'PENDING' for item in summary['queue']['entries'])
    assert summary['queue']['sent_by_this_call'] is False


@pytest.mark.parametrize('scenario', ['judge_missing', 'deadline'])
def test_incomplete_review_has_only_a_status_card_and_no_reservation(rehearsals, scenario):
    home, summary = rehearsals[scenario]
    assert summary['status'] == 'REVIEW_INCOMPLETE'
    assert summary['reservation'] == _reserved(home) == 0
    plans = summary['plan']['plans']
    assert len(plans) == 1 and plans[0]['plan_type'] == 'RUN_STATUS'
    assert plans[0]['source_status'] == 'REVIEW_INCOMPLETE'
    assert len(summary['queue']['entries']) == 1
    assert summary['queue']['entries'][0]['state'] == 'PENDING'
    assert summary['queue']['sent_by_this_call'] is False


def test_stop_before_review_rejects_every_new_candidate(rehearsals):
    home, summary = rehearsals['stop_before_review']
    candidates = summary['result']['candidates']
    assert (home/'STOP').exists()
    assert candidates and all(c['status'] != 'APPROVED' for c in candidates)
    assert all('STOP_NEW' in c['gate']['reason_codes'] for c in candidates)
    assert summary['reservation'] == _reserved(home) == 0
    assert not any(plan.get('kind') == 'NEW' for plan in summary['plan']['plans'])


@pytest.mark.parametrize('scenario,seed', [
    ('', 42), ('NORMAL', 42), (None, 42), (1, 42),
    ('normal', -1), ('normal', 1.5), ('normal', True),
])
def test_invalid_inputs_fail_before_market_generation(tmp_path, monkeypatch, scenario, seed):
    from aitrader import daily_rehearsal

    monkeypatch.setattr(daily_rehearsal, 'load_synthetic',
                        lambda *a, **k: pytest.fail('invalid input reached market generation'))
    with pytest.raises(RunError):
        daily_rehearsal.run_daily_rehearsal(tmp_path/'invalid', scenario=scenario, seed=seed)
    assert not (tmp_path/'invalid').exists()


def test_missing_calendar_row_is_data_incomplete_not_a_holiday(tmp_path, monkeypatch):
    from aitrader import daily_rehearsal
    from aitrader.api import load_synthetic as real_load
    from aitrader.db import connect

    def load_without_calendar(home, seed=42):
        real_load(home, seed=seed)
        with connect(home) as con:
            con.execute('DELETE FROM calendar WHERE date=?', [daily_rehearsal.NORMAL_DAY])

    monkeypatch.setattr(daily_rehearsal, 'load_synthetic', load_without_calendar)
    summary = daily_rehearsal.run_daily_rehearsal(tmp_path/'missing-calendar')
    assert summary['status'] == 'DATA_INCOMPLETE'
    assert summary['result']['status'] == 'DATA_INCOMPLETE'
    assert 'MARKET_DATA_INCOMPLETE' in summary['result']['reason_codes']
    assert all(summary['stages'][name]['status'] == 'SKIPPED'
               for name in ('generate', 'review', 'prepare', 'enqueue'))
    assert summary['reservation'] == 0


def test_existing_home_is_rejected_without_touching_it(tmp_path, monkeypatch):
    from aitrader import daily_rehearsal

    home = tmp_path/'existing'
    home.mkdir()
    marker = home/'keep.txt'
    marker.write_text('preserve', encoding='utf-8')
    monkeypatch.setattr(daily_rehearsal, 'load_synthetic',
                        lambda *a, **k: pytest.fail('existing home reached market generation'))
    with pytest.raises(RunError):
        daily_rehearsal.run_daily_rehearsal(home)
    assert list(home.iterdir()) == [marker]
    assert marker.read_text(encoding='utf-8') == 'preserve'
