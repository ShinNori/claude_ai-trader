"""2026-09-09 第17回：ops v0.4.1（Codex 第16回レビュー V01〜V13 の修正）の Claude 独立回帰試験。
実 CLI・ネットワーク・実 LINE は呼ばない。skip/xfail/条件緩和はしない。"""
import base64
import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops'), str(ROOT / 'build-codex')]
from aitrader.packet import build_proposals, render_packet  # noqa: E402
from aitrader_ops.ledger import Ledger  # noqa: E402
from aitrader_ops.models import Proposal, Verdict, compute_packet_hash  # noqa: E402
from aitrader_ops import judges, notify  # noqa: E402

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 9, 7, 0, tzinfo=JST)
SECRET = 'dummy-channel-secret'
USER = 'U-dummy-allowed'
SETTINGS = {'broker': {'link_template': 'https://www.rakuten-sec.co.jp/dummy?code={code}'},
            'line': {'allowed_user_id': USER, 'monthly_budget': 200}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError('実CLI・ネットワークは禁止')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setenv('LINE_CHANNEL_SECRET', SECRET)
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', 'dummy-token-ABC')


def proposal(**changes):
    from dataclasses import replace
    d = dict(proposal_id='A1-20260909-6857-01', packet_hash='', code='6857', side='BUY', qty=100, lot_size=100, limit_price=1000.,
             exec_condition='OPENING_LIMIT', account_type='CASH', strategy='s', strategy_version='v1', as_of=date(2026, 9, 8),
             snapshot_id='s1', policy_version='p1', expires_at=NOW.replace(hour=8, minute=59), reason='r',
             events={'next_earnings_date': None, 'margin_regulated': False})
    d.update(changes)
    p = Proposal(**d)
    return replace(p, packet_hash=compute_packet_hash(p))


@pytest.fixture
def ledger(tmp_path):
    l = Ledger(tmp_path / 'ledger.sqlite')
    l.init_snapshot(1000000, [], [], NOW - timedelta(days=1))
    l.create_notice(proposal(), at=NOW)
    l.set_notice_state(proposal().proposal_id, 'APPROVED', NOW)
    yield l
    l.close()


@pytest.fixture
def make(tmp_path, ledger):
    opened = []
    def factory(results=(), budget=200):
        s = dict(SETTINGS, line=dict(SETTINGS['line'], monthly_budget=budget))
        n = notify.Notifier(ledger=ledger, state_path=tmp_path / 'n.sqlite', settings=s, transport='stub', stub_results=list(results))
        opened.append(n)
        return n
    yield factory
    for n in opened:
        n.close()


def risk(n, key='risk', at=NOW):
    return n.enqueue(key=key, kind='RISK', message={'type': 'text', 'text': key}, proposal_id=None, now=at)


def signed(events, secret=SECRET):
    body = json.dumps({'events': events}, ensure_ascii=False).encode()
    return body, base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def control(action, eid, at):
    return {'type': 'postback', 'webhookEventId': eid, 'timestamp': int(at.timestamp() * 1000),
            'source': {'type': 'user', 'userId': USER}, 'postback': {'data': json.dumps({'action': action})}}


# ---- V02/V03/V05: SENDING・UNKNOWN の各クラッシュ点と並行予算（実送信呼出回数で確認） ------------------------------

def test_s01_send_exception_marks_unknown_keeps_budget_and_retries_with_same_key(make, monkeypatch):
    n = make(['success'])
    risk(n)
    monkeypatch.setattr(n, '_send_stub', lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(RuntimeError):
        n.flush(now=NOW)
    st = n.status(now=NOW)
    assert st['monthly_used'] == 1 and 'SEND_UNKNOWN' in st['alerts']
    monkeypatch.undo()
    n2 = make(['success'])
    out = n2.flush(now=NOW + timedelta(seconds=1))
    assert out and out[0]['state'] == 'SENT' and n2.status(now=NOW)['monthly_used'] == 1
    assert len(n2.stub_results) == 0


def test_s02_stale_sending_claim_from_dead_process_recovers_to_unknown_only_after_grace(make, tmp_path):
    n = make(['success'])
    risk(n)
    n._con.execute("UPDATE outbox SET state='SENDING', claimed_by='999999:dead', claimed_at=?, month=? WHERE key='risk'",
                   [NOW.isoformat(), '2026-09'])
    assert n.flush(now=NOW + timedelta(minutes=1)) == []                              # 猶予内: 他プロセス処理中とみなし触らない
    assert n.status(now=NOW)['monthly_used'] == 1                                      # SENDING も月枠を保持
    out = n.flush(now=NOW + timedelta(minutes=11))                                     # 猶予超過: UNKNOWN へ回復して再試行
    assert out[0]['state'] == 'SENT' and n.status(now=NOW)['monthly_used'] == 1
    assert 'SEND_UNKNOWN' in n.status(now=NOW)['alerts']


def test_s03_parallel_budget_counts_actual_send_calls_across_two_handles(make):
    a, b = make(['success'] * 3, budget=2), make(['success'] * 3, budget=2)
    for i in range(4):
        risk(a, f'r{i}')
    calls = []
    gate = threading.Barrier(2, timeout=5)
    def sender(name, orig):
        def send():
            calls.append(name)
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                pass
            return orig()
        return send
    a._send_stub, b._send_stub = sender('a', a._send_stub), sender('b', b._send_stub)
    out = {}
    ts = [threading.Thread(target=lambda: out.setdefault('a', a.flush(now=NOW))),
          threading.Thread(target=lambda: out.setdefault('b', b.flush(now=NOW)))]
    for t in ts: t.start()
    for t in ts: t.join()
    sent = [r['key'] for rows in out.values() for r in rows if r['state'] == 'SENT']
    assert len(calls) == 2 and sorted(sent) == sorted(set(sent)) and len(sent) == 2
    assert a.status(now=NOW)['monthly_used'] == 2
    assert sum(r['state'] == 'BUDGET_BLOCKED' for rows in out.values() for r in rows) >= 1


def test_s04_unknown_then_failure_then_success_keeps_one_budget_unit_and_one_retry_key(make):
    n = make(['timeout', 'failure', 'success'])
    risk(n)
    r1 = n.flush(now=NOW)[0]
    r2 = n.flush(now=NOW + timedelta(minutes=1))[0]
    r3 = n.flush(now=NOW + timedelta(minutes=2))[0]
    assert [r1['state'], r2['state'], r3['state']] == ['UNKNOWN', 'UNKNOWN', 'SENT']
    assert r1['retry_key'] == r2['retry_key'] == r3['retry_key'] and n.status(now=NOW)['monthly_used'] == 1


def test_s05_unknown_candidate_after_deadline_stays_unknown_and_stop_does_not_change_it(make, ledger):
    n = make(['timeout'])
    n.enqueue(key='new', kind='NEW', message=notify.render_message(kind='NEW', proposal=proposal(), verdicts=(),
              context={'name': 'n'}, settings=SETTINGS), proposal_id=proposal().proposal_id, now=NOW)
    assert n.flush(now=NOW)[0]['state'] == 'UNKNOWN'
    n.stop(now=NOW + timedelta(minutes=1))
    late = n.flush(now=NOW.replace(minute=20))
    assert late[0]['state'] == 'UNKNOWN' and n.status(now=NOW)['monthly_used'] == 1
    assert ledger.notice(proposal().proposal_id)['notice_state'] == 'APPROVED'          # 未送信とも送信済みとも断定しない


# ---- V06/V18: 日次通知の登録日失効と月境界 ----------------------------------------------------------------

def test_s06_daily_notification_expires_at_end_of_creation_day_but_unknown_retry_survives(make):
    n = make(['timeout', 'success', 'success'])
    risk(n, 'old', NOW)
    assert n.flush(now=NOW)[0]['state'] == 'UNKNOWN'
    risk(n, 'fresh', NOW.replace(hour=23, minute=59, second=59))
    out = {r['key']: r['state'] for r in n.flush(now=NOW.replace(hour=23, minute=59, second=59))}
    assert out == {'old': 'SENT', 'fresh': 'SENT'}
    risk(n, 'stale', NOW)
    assert n.flush(now=NOW + timedelta(days=1))[0]['state'] == 'EXPIRED'                # 翌日には送らない
    assert n.status(now=NOW)['monthly_used'] == 2


def test_s07_month_boundary_in_utc_argument_and_restart(make, tmp_path, ledger):
    n = make(['success'] * 2, budget=1)
    end = NOW.replace(day=30, hour=23, minute=59, second=59)
    risk(n, 'sep', end)
    assert n.flush(now=end.astimezone(timezone.utc))[0]['state'] == 'SENT'
    n2 = notify.Notifier(ledger=ledger, state_path=tmp_path / 'n.sqlite', settings=dict(SETTINGS, line=dict(SETTINGS['line'], monthly_budget=1)),
                         transport='stub', stub_results=['success'])
    try:
        start = end + timedelta(seconds=1)
        risk(n2, 'oct', start)
        assert n2.flush(now=start.astimezone(timezone.utc))[0]['state'] == 'SENT'
        assert n2.status(now=end)['monthly_used'] == 1 and n2.status(now=start)['monthly_used'] == 1
    finally:
        n2.close()


# ---- V07/V10: 遅着・未来の制御操作 ----------------------------------------------------------------------

def test_s08_control_ordering_uses_event_time_and_rejects_future(make):
    n = make()
    body, sig = signed([control('STOP', 's1', NOW.replace(hour=9))])
    assert notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW.replace(hour=9))['results'][0]['status'] == 'STOPPED'
    body, sig = signed([control('RESUME', 'r1', NOW.replace(hour=10))])
    assert notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW.replace(hour=10),
                                 reconciled_at=NOW.replace(hour=10))['results'][0]['status'] == 'RESUMED'
    body, sig = signed([control('STOP', 'late', NOW.replace(hour=8)), control('RESUME', 'late-r', NOW.replace(hour=8, minute=30))])
    r = notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW.replace(hour=11), reconciled_at=NOW.replace(hour=11))
    assert [x['status'] for x in r['results']] == ['IGNORED', 'IGNORED'] and not n.is_stopped()
    body, sig = signed([control('STOP', 'future', NOW.replace(hour=12))])
    assert notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW.replace(hour=11))['results'][0]['status'] == 'INVALID'
    assert not n.is_stopped()
    body, sig = signed([control('STOP', 's2', NOW.replace(hour=11))])                   # 最新の STOP は効く
    assert notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW.replace(hour=11))['results'][0]['status'] == 'STOPPED'
    assert n.is_stopped()
    rows = n._con.execute('SELECT action FROM controls ORDER BY seq').fetchall()
    assert [r[0] for r in rows] == ['STOP', 'RESUME', 'STOP_LATE', 'RESUME_LATE', 'STOP']    # 遅着も監査に残る


# ---- V08/V09/V13: webhook の形と署名、IDN -------------------------------------------------------------------

def test_s09_malformed_shapes_and_signature_variants(make):
    n = make()
    bad = control('STOP', 'b1', NOW)
    bad['postback'] = 'not-a-dict'
    good = control('STOP', 'g1', NOW)
    body, sig = signed([bad, {'type': 'message', 'webhookEventId': 'b2', 'timestamp': 1, 'source': {'type': 'user', 'userId': USER}, 'message': []}, good])
    r = notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW)
    assert [x['status'] for x in r['results']] == ['INVALID', 'INVALID', 'STOPPED']
    for sig2 in ('署名', 'aé', sig + '\n', ' ' + sig):
        assert notify.handle_webhook(notifier=n, body=body, signature=sig2, now=NOW)['http_status'] == 401


@pytest.mark.parametrize('template', ['https://ｍember.rakuten-sec.co.jp/?code={code}', 'https://www.rakuten-sec.co.jp。/?code={code}',
                                      'https://xn--member-1234.rakuten-sec.co.jp.evil/?code={code}', 'https://ｗww.rakuten-sec.co.jp/?code={code}'])
def test_s10_idn_and_lookalike_hosts_rejected(template):
    with pytest.raises(ValueError):
        notify.broker_link(template, '6857')


# ---- V01: ログの秘密値マスクは行全体 ----------------------------------------------------------------------

def test_s11_secret_masked_everywhere_in_log_row(tmp_path):
    p = build_proposals([dict(code='6857', side='BUY', strategy='s', strategy_version='v1', reason='dummy-token-ABC in reason')],
                        date(2026, 9, 8), {'6857': 1000}, {'6857': 100}, {'6857': dict(next_earnings_date=None, margin_regulated=False)},
                        's1', 'p1', 110000, business_days=[date(2026, 9, 9)])[0]
    packet = render_packet(p, {'ref': 'dummy-token-ABC'})
    pr = json.loads(packet)['proposal']
    answer = dict(proposal_id=pr['proposal_id'], packet_hash=pr['packet_hash'], decision='REJECT', risks=['dummy-token-ABC'],
                  reason='reason dummy-token-ABC', confidence=None)
    rec = dict(stdout='', final_message=json.dumps(answer), exit_code=0, elapsed_seconds=1, stderr='dummy-token-ABC', extra={'k': 'dummy-token-ABC'})
    r = judges.review(packet=packet, judge='codex', transport='stub', response=rec, replay_path=None, now=NOW,
                      received_at=NOW + timedelta(seconds=1), timeout_seconds=120, model='m', cli_version='c', run_id='r',
                      prompt_version='review-v1', log_dir=tmp_path / 'log')
    assert r['failure'] is None and r['verdict'].decision == 'REJECT'
    text = ''.join(p.read_text(encoding='utf-8') for p in (tmp_path / 'log').rglob('*.jsonl'))
    assert 'dummy-token-ABC' not in text and text.count('<LINE_CHANNEL_ACCESS_TOKEN>') >= 5
    assert r['verdict'].reason == 'reason dummy-token-ABC'                               # 戻り値はマスクしない（ログだけ）
