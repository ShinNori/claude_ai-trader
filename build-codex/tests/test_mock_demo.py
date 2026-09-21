"""Independent synthetic demo checks: two votes, reservations and no delivery."""
import json
from contextlib import closing

import pytest

from test_notification_plan import offline
from aitrader.mock_demo import run_demo, NOW
from aitrader.runner import RunError
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier


@pytest.mark.parametrize('scenario,approved', [('approve', True), ('reject', False),
                                             ('missing', False), ('timeout', False)])
def test_scenario_ends_with_consistent_unsent_cards(tmp_path, monkeypatch, scenario, approved):
    home = tmp_path/scenario
    monkeypatch.setattr(Notifier, 'flush', lambda *a, **kw: pytest.fail('demo must not flush'))
    result = run_demo(home, scenario)
    assert result['mode'] == 'mock' and result['delivery'] == 'NOT_SENT'
    assert result['queue']['delivery'] == 'NOT_SENT'
    assert json.loads((home/'demo_summary.json').read_text(encoding='utf-8')) == result
    cards = result['plan']['plans']
    new_cards = [card for card in cards if card['kind'] == 'NEW']
    assert len(new_cards) == int(approved)
    status_cards = [card for card in cards if card.get('plan_type') == 'RUN_STATUS']
    assert len(status_cards) == int(not approved)
    status_text = json.dumps(status_cards, ensure_ascii=False)
    if not approved:
        assert '模擬・未送信' in status_text
        assert '承認: 0件' in status_text
    if scenario == 'missing':
        assert '審査未完了該当: 1件' in status_text
    if scenario == 'timeout':
        assert result['result']['status'] == 'NOT_APPROVED'
        verdict_path = home/'runs'/result['execution_day']/result['run_id']/'verdicts.json'
        verdicts = json.loads(verdict_path.read_text(encoding='utf-8'))
        assert any(v['decision'] == 'ABSTAIN' for v in verdicts['demo-buy'])
    with closing(Ledger(home/'ledger.sqlite')) as ledger:
        assert ledger.reserved() == result['reserved']
        assert (ledger.reserved() > 0) is approved
        with closing(Notifier(ledger=ledger, state_path=home/'notification.sqlite',
                              settings=result['settings'], transport='stub')) as notifier:
            assert notifier.status(now=NOW)['monthly_used'] == 0
            for card in cards:
                row = notifier.get_entry(card['key'])
                assert row['state'] == 'PENDING'
                assert row['attempts'] == []
                assert row['message'] == card['message']


def test_existing_demo_cannot_be_overwritten(tmp_path):
    home = tmp_path/'demo'
    run_demo(home)
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    with pytest.raises((RunError, ValueError)):
        run_demo(home, 'reject')
    after = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    assert after == before


def test_existing_foreign_folder_is_preserved(tmp_path):
    home = tmp_path/'foreign'
    home.mkdir()
    (home/'keep.txt').write_text('unchanged', encoding='utf-8')
    with pytest.raises((RunError, ValueError)):
        run_demo(home)
    assert list(home.iterdir()) == [home/'keep.txt']
    assert (home/'keep.txt').read_text(encoding='utf-8') == 'unchanged'


@pytest.mark.parametrize('scenario', ['live', ''])
def test_invalid_scenario_has_no_filesystem_effect(tmp_path, scenario):
    home = tmp_path/'invalid'
    with pytest.raises((RunError, ValueError)):
        run_demo(home, scenario)
    assert not home.exists()
