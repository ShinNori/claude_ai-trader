"""Explicit opt-in simulated demo delivery, with real I/O forbidden."""
import json
from contextlib import closing

import pytest

from test_notification_plan import offline
from aitrader.mock_demo import run_demo, NOW, main
from aitrader.runner import RunError
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier


@pytest.mark.parametrize('scenario', ['approve', 'reject', 'missing', 'timeout'])
def test_explicit_simulation_sends_only_stub_cards(tmp_path, scenario):
    home = tmp_path/scenario
    summary = run_demo(home, scenario, simulate_delivery=True)
    assert summary['delivery'] == 'SIMULATION_ONLY'
    assert summary['real_sent_by_this_call'] is False
    result = summary['mock_delivery']
    assert result['mode'] == 'mock' and result['transport'] == 'stub'
    assert result['real_sent_by_this_call'] is False
    assert result['simulated_sent_by_this_call'] is True
    cards = summary['plan']['plans']
    assert len(cards) == 1
    assert cards[0]['kind'] == ('NEW' if scenario == 'approve' else 'RECONCILE')
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        with closing(Notifier(ledger=ledger, state_path=home/'notification.sqlite',
                              settings=summary['settings'], transport='stub')) as notifier:
            entry = notifier.get_entry(cards[0]['key'])
            assert entry['state'] == 'SENT'
            assert [item['result'] for item in entry['attempts']] == ['success']
            assert notifier.status(now=NOW)['monthly_used'] == 1
        assert ledger.reserved() == summary['reserved']
        if scenario == 'approve':
            assert ledger.notice('demo-buy')['notice_state'] == 'SENT'
        else:
            assert ledger.reserved() == 0
    assert json.loads((home/'demo_summary.json').read_text(encoding='utf-8')) == summary


@pytest.mark.parametrize('value', [None, 1, 'true', []])
def test_non_boolean_opt_in_is_rejected_before_initialization(tmp_path, value):
    home = tmp_path/'invalid'
    with pytest.raises((RunError, ValueError)):
        run_demo(home, simulate_delivery=value)
    assert not home.exists()


def test_default_does_not_call_delivery(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('default demo must not flush')
    monkeypatch.setattr(Notifier, 'flush', forbidden)
    result = run_demo(tmp_path/'default')
    assert result['delivery'] == 'NOT_SENT'
    assert not (tmp_path/'default'/'runs'/result['execution_day']/result['run_id']/'mock_delivery.json').exists()


def test_cli_flag_explicitly_enables_stub_delivery(tmp_path, capsys):
    home = tmp_path/'cli'
    assert main(['--home', str(home), '--scenario', 'approve', '--simulate-delivery']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['delivery'] == 'SIMULATION_ONLY'
    assert result['real_sent_by_this_call'] is False
