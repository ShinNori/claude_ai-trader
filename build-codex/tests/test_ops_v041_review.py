"""v0.4.1 independent regression probes; synthetic input, no network."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'common/tests/phase2')]
import test_judges as j
import test_notify as n
from contextlib import closing
from datetime import timedelta, timezone
ledger = n.ledger
offline = n.offline


@pytest.mark.parametrize('action', ['STOP', 'RESUME'])
@pytest.mark.parametrize('stamp', [None, True, 'invalid', 10**1000])
def test_w02_invalid_control_time_keeps_batch_alive(tmp_path, ledger, action, stamp):
    """欠損・型違い・巨大日時でも後続の正常イベントを処理する。"""
    with closing(n.notifier(tmp_path, ledger)) as obj:
        bad = n.event(action, 'bad'); bad['timestamp'] = stamp
        result = n.receive(obj, [bad, n.event('STOP', 'good')])
        assert [x['status'] for x in result['results']] == ['INVALID', 'STOPPED']


@pytest.mark.parametrize('action', ['STOP', 'RESUME'])
@pytest.mark.parametrize('offset', [0, 1])
def test_w03_control_future_boundary_in_utc(tmp_path, ledger, action, offset):
    """別タイムゾーンでも同時刻は受理対象、1ミリ秒先は拒否する。"""
    now = n.NOW.replace(hour=10).astimezone(timezone.utc)
    with closing(n.notifier(tmp_path, ledger)) as obj:
        ev = n.event(action, at=now + timedelta(milliseconds=offset))
        result = n.receive(obj, [ev], now=now, reconciled_at=now)
        assert result['results'][0]['status'] == ('INVALID' if offset else ('STOPPED' if action == 'STOP' else 'RESUMED'))


def test_w04_signature_precedes_duplicate_detection(tmp_path, ledger):
    """同ID改変でも署名不正は競合検査より先に拒否し履歴を汚さない。"""
    with closing(n.notifier(tmp_path, ledger)) as obj:
        original = n.event('STOP', 'same')
        assert n.receive(obj, [original])['results'][0]['status'] == 'STOPPED'
        changed = n.event('RESUME', 'same')
        assert n.receive(obj, [changed], signature='invalid')['http_status'] == 401
        assert n.receive(obj, [original])['results'][0]['status'] == 'DUPLICATE'
        assert n.receive(obj, [changed])['results'][0]['status'] == 'CONFLICT'


@pytest.mark.parametrize('judge', ['claude', 'codex'])
@pytest.mark.parametrize('location', ['nested_value', 'nested_key', 'long_value'])
def test_w01_decision_log_masks_nested_secrets(tmp_path, monkeypatch, judge, location):
    """決定ログのネスト値・キー・長文末尾からダミー秘密値が漏れないか検査する。"""
    secret = 'dummy-v041-secret-unique'
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', secret)
    response = j.record(judge)
    if location == 'nested_key':
        response['extra'] = [{'prefix-' + secret + '-suffix': 'value'}]
    elif location == 'long_value':
        response['extra'] = ['あ' * 70000 + secret]
    else:
        response['extra'] = [{'inner': ['prefix-' + secret + '-suffix']}]
    j.run(tmp_path, judge, response)
    logs = list((tmp_path / 'decisions').glob('*.jsonl'))
    assert logs, '審査ログが作成されていない'
    for path in logs:
        text = path.read_text(encoding='utf-8')
        assert secret not in text
        for line in text.splitlines():
            json.loads(line)
