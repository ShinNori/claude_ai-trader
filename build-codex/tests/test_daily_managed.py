"""Focused evidence for explicit managed STOP daily selection."""
import json
import shutil

import pytest

from aitrader.runner import RunError


@pytest.fixture(scope='module')
def managed_days(tmp_path_factory):
    from aitrader.api import load_synthetic
    from aitrader import daily_rehearsal

    source = tmp_path_factory.mktemp('daily-managed-market')
    load_synthetic(source, seed=42)
    market = source/'market.duckdb'
    root = tmp_path_factory.mktemp('daily-managed-days')
    outcomes = {}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(daily_rehearsal, 'load_synthetic',
                      lambda home, seed=42: shutil.copy2(market, home/'market.duckdb'))
        for scenario in ('normal', 'stop_before_review'):
            home = root/scenario
            outcomes[scenario] = (home, daily_rehearsal.run_daily_rehearsal(
                home, scenario=scenario, seed=42, managed_stop=True))
    return outcomes


def test_managed_daily_is_sealed_and_uses_one_fixed_policy(managed_days):
    from aitrader.daily_receipt import inspect_daily_rehearsal
    from aitrader.managed_stop import managed_stop_policy

    home, summary = managed_days['normal']
    policy = managed_stop_policy(home)
    assert policy['stop_policy'] == summary['stop_policy'] == 'managed-v1'
    assert summary['managed_stop'] is True
    assert not (home/'.managed-stop-initializing').exists()
    assert inspect_daily_rehearsal(home)['status'] == 'VERIFIED_HISTORY'


def test_managed_stop_scenario_records_persistent_stop_before_gate(managed_days):
    from aitrader.managed_stop import inspect_managed_stop
    from aitrader_ops.models import JST
    from datetime import datetime

    home, summary = managed_days['stop_before_review']
    assert summary['reservation'] == 0
    assert summary['result']['candidates']
    assert all('STOP_NEW' in item['gate']['reason_codes']
               for item in summary['result']['candidates'])
    status = inspect_managed_stop(home, now=datetime(2026, 8, 31, 7, 1, tzinfo=JST))
    assert status['status'] == 'STOPPED'
    assert status['persistent_stopped'] is True
    assert not (home/'STOP').exists()


def test_managed_selection_input_and_cli_forwarding(tmp_path, monkeypatch, capsys):
    from aitrader import daily_rehearsal

    invalid = tmp_path/'invalid'
    with pytest.raises(RunError):
        daily_rehearsal.run_daily_rehearsal(invalid, managed_stop=1)
    assert not invalid.exists()

    captured = {}
    def fake(home, *, scenario, seed, managed_stop):
        captured.update(home=home, scenario=scenario, seed=seed, managed_stop=managed_stop)
        return {'mode': 'mock', 'delivery': 'NOT_SENT'}

    monkeypatch.setattr(daily_rehearsal, 'run_daily_rehearsal', fake)
    assert daily_rehearsal.main(['--home', str(tmp_path/'cli'), '--managed-stop']) == 0
    assert captured['managed_stop'] is True
    assert json.loads(capsys.readouterr().out)['delivery'] == 'NOT_SENT'


def test_managed_selection_rejects_existing_home_without_changes(tmp_path):
    from aitrader import daily_rehearsal

    home = tmp_path/'existing'
    home.mkdir()
    marker = home/'keep.txt'
    marker.write_text('preserve', encoding='utf-8')
    with pytest.raises((RunError, ValueError)):
        daily_rehearsal.run_daily_rehearsal(home, managed_stop=True)
    assert list(home.iterdir()) == [marker]
    assert marker.read_text(encoding='utf-8') == 'preserve'
