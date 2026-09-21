"""Independent safety checks for the static operations overview."""
from pathlib import Path

import pytest

from aitrader.operations_view import main, render_operations_view


NOW = '2026-08-31T07:16:00+09:00'


def report(*, status='UNKNOWN', stop_status='UNKNOWN', stop_known=False,
           history_status='NEEDS_RECONCILIATION', progress_status='UNKNOWN'):
    return {
        'status': status, 'read_only': True, 'current_signal': False,
        'auto_resume': False, 'real_sent': False, 'observed_at': NOW,
        'stop': {'status': stop_status, 'known': stop_known,
                 'effective_stop': not stop_known or stop_status == 'STOPPED',
                 'persistent_stopped': None,
                 'reasons': ['STATE_DB_UNKNOWN'], 'observed_at': NOW,
                 'control_event_at': None, 'read_only': True, 'managed': True},
        'history': {'status': history_status, 'reason': 'HISTORY_UNKNOWN'},
        'progress': {'status': progress_status, 'known': False,
                     'current_stage': None, 'checkpoint_seq': None,
                     'reasons': ['PROGRESS_UNREADABLE']},
    }


def test_unknown_view_has_three_separate_sections_and_no_order_conditions(monkeypatch):
    from aitrader import operations_view

    value = report()
    value['history']['untrusted'] = {
        'code': '6857-SECRET', 'qty': 999, 'limit_price': 1234,
        'broker_link': 'https://outside.example/order'}
    monkeypatch.setattr(operations_view, 'build_operations_status',
                        lambda home, *, now: value)

    html = render_operations_view('ignored', now=NOW)

    assert html.index('1. 今回観測した停止状態') < html.index('2. 保存済みの日次履歴')
    assert html.index('2. 保存済みの日次履歴') < html.index('3. 進捗ファイルの記録')
    assert '状態を確認できません' in html
    assert '現在の売買承認ではありません' in html
    assert '6857-SECRET' not in html and '1234' not in html
    assert 'outside.example' not in html


def test_verified_history_stays_historical_when_current_stop_is_unknown(monkeypatch):
    from aitrader import operations_view

    value = report(history_status='VERIFIED_HISTORY')
    value['history'] = {'status': 'VERIFIED_HISTORY', 'summary': {
        'status': 'CANDIDATES', 'scenario': 'normal',
        'execution_day': '2026-08-31', 'reservation': 10020,
        'reserved': 10020, 'managed_stop': True,
        'stop_policy': 'managed-v1'}}
    monkeypatch.setattr(operations_view, 'build_operations_status',
                        lambda home, *, now: value)

    html = render_operations_view('ignored', now=NOW)

    assert '保存履歴の一致を確認' in html
    assert '保存時の模擬予約' in html and '10020' in html
    assert '現在の残高・送信実績の保証ではありません' in html
    assert '状態を確認できません' in html
    assert '送信可能' not in html and '発注可能' not in html


def test_clear_is_explicitly_not_an_authorization(monkeypatch):
    from aitrader import operations_view

    value = report(status='CLEAR', stop_status='CLEAR', stop_known=True,
                   history_status='VERIFIED_HISTORY', progress_status='COMPLETED')
    value['stop'].update(effective_stop=False, persistent_stopped=False, reasons=[])
    value['history'] = {'status': 'VERIFIED_HISTORY', 'summary': {
        'status': 'NO_SIGNAL', 'scenario': 'normal',
        'execution_day': '2026-08-31', 'reservation': 0}}
    value['progress'] = {'status': 'COMPLETED', 'known': True,
                         'current_stage': 'finalize', 'checkpoint_seq': 8,
                         'reasons': [], 'result_status': 'NO_SIGNAL'}
    monkeypatch.setattr(operations_view, 'build_operations_status',
                        lambda home, *, now: value)

    html = render_operations_view('ignored', now=NOW)

    assert '停止なし・履歴一致' in html
    assert '停止なし（売買の許可ではありません）' in html
    assert '自動再開・修復・予約解除は行いません' in html


def test_all_dynamic_text_is_escaped(monkeypatch):
    from aitrader import operations_view

    value = report()
    attack = '<img src=x onerror=alert(1)>'
    value['stop']['reasons'] = [attack]
    value['history']['reason'] = attack
    value['observed_at'] = attack
    monkeypatch.setattr(operations_view, 'build_operations_status',
                        lambda home, *, now: value)

    html = render_operations_view('ignored', now=NOW)

    assert attack not in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html


def test_html_is_self_contained_and_has_no_action_surface(monkeypatch):
    from aitrader import operations_view

    monkeypatch.setattr(operations_view, 'build_operations_status',
                        lambda home, *, now: report())
    html = render_operations_view('ignored', now=NOW).lower()

    assert "default-src &#39;none&#39;" in html
    assert "form-action &#39;none&#39;" in html
    for fragment in ('<script', '<form', '<button', '<input', '<a ',
                     ' href=', ' src=', 'javascript:'):
        assert fragment not in html


def test_real_managed_home_is_byte_unchanged_by_render(tmp_path):
    from aitrader.daily_rehearsal import run_daily_rehearsal

    home = tmp_path/'managed-view'
    run_daily_rehearsal(home, scenario='normal', seed=42, managed_stop=True)
    before = {path.relative_to(home).as_posix(): path.read_bytes()
              for path in home.rglob('*') if path.is_file()}

    html = render_operations_view(home, now=NOW)

    after = {path.relative_to(home).as_posix(): path.read_bytes()
             for path in home.rglob('*') if path.is_file()}
    assert after == before
    assert '保存履歴の一致を確認' in html
    assert '停止なし（売買の許可ではありません）' in html
    assert str(home) not in html


def test_cli_prints_html_only_and_creates_no_output(monkeypatch, capsys, tmp_path):
    from aitrader import operations_view

    monkeypatch.setattr(operations_view, 'build_operations_status',
                        lambda home, *, now: report())
    before = set(tmp_path.iterdir())

    assert main(['--home', str(tmp_path), '--now', NOW]) == 0

    output = capsys.readouterr().out
    assert output.startswith('<!doctype html>')
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize('bad_now', ['2026-08-31T07:16:00', 'broken'])
def test_invalid_now_does_not_render(tmp_path, bad_now):
    with pytest.raises(ValueError):
        render_operations_view(tmp_path, now=bad_now)
    assert list(tmp_path.iterdir()) == []
