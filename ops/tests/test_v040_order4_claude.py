"""2026-09-09 第16回：フェーズ2 順序4（judges / notify）の Claude 独立試験（仕様案 §5 の後続検証項目）。
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
from aitrader_ops.models import Proposal, Verdict, PositionIn, compute_packet_hash  # noqa: E402
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
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', 'dummy-access-token-XYZ')


def packet():
    p = build_proposals([dict(code='6857', side='BUY', strategy='margin_bucket_long', strategy_version='v1', reason='r')],
                        date(2026, 9, 8), {'6857': 1000}, {'6857': 100},
                        {'6857': dict(next_earnings_date=None, margin_regulated=False)}, 's1', 'p1', 110000,
                        business_days=[date(2026, 9, 9)])[0]
    return render_packet(p, {})


def answer(**changes):
    p = json.loads(packet())['proposal']
    a = dict(proposal_id=p['proposal_id'], packet_hash=p['packet_hash'], decision='APPROVE', risks=[], reason='ok', confidence=None)
    a.update(changes)
    return a


def record(judge, value=None, **changes):
    raw = json.dumps(answer() if value is None else value, ensure_ascii=False) if not isinstance(value, str) else value
    r = dict(stdout=json.dumps(dict(type='result', subtype='success', is_error=False, result=raw)) if judge == 'claude' else '',
             final_message=raw if judge == 'codex' else None, exit_code=0, elapsed_seconds=1)
    r.update(changes)
    return r


def review(tmp_path, judge='claude', response=None, **changes):
    args = dict(packet=packet(), judge=judge, transport='stub', response=record(judge) if response is None else response,
                replay_path=None, now=NOW, received_at=NOW + timedelta(seconds=1), timeout_seconds=120, model='m',
                cli_version='c', run_id='r', prompt_version='review-v1', log_dir=tmp_path / 'log')
    args.update(changes)
    if args['replay_path'] is not None and response is None:
        args['response'] = None
    return judges.review(**args)


# ---- judges --------------------------------------------------------------------------------------

@pytest.mark.parametrize('bad', [
    dict(now=NOW.replace(tzinfo=None)), dict(received_at=NOW - timedelta(seconds=1)), dict(judge='gpt'),
    dict(transport='http'), dict(timeout_seconds=0), dict(timeout_seconds=True), dict(model=''),
    dict(replay_path=Path('x.json'), response=record('claude')),   # response と両方指定
])
def test_q01_argument_contract_raises_value_error(tmp_path, bad):
    with pytest.raises(ValueError):
        review(tmp_path, **bad)
    assert not list((tmp_path / 'log').rglob('*.jsonl'))    # 引数エラーはログを残さない


def test_q02_cli_transport_is_refused_in_this_loop(tmp_path):
    with pytest.raises(ValueError):
        review(tmp_path, transport='cli')


def test_q03_log_write_failure_stops_the_review_without_returning_a_verdict(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError('disk full (simulated)')
    monkeypatch.setattr(judges, '_log', boom)
    with pytest.raises(OSError):
        review(tmp_path)


def test_q04_secrets_and_oversized_output_are_masked_in_the_log(tmp_path):
    big = 'x' * (judges.STDOUT_LIMIT + 1000)
    raw = json.dumps(dict(type='result', subtype='success', is_error=False,
                          result='dummy-access-token-XYZ ' + big))
    r = review(tmp_path, response=record('claude', stdout=raw))
    assert r['failure'] == 'malformed'
    row = json.loads(next((tmp_path / 'log').rglob('*.jsonl')).read_text(encoding='utf-8').splitlines()[0])
    assert 'dummy-access-token-XYZ' not in row['record']['stdout'] and '<LINE_CHANNEL_ACCESS_TOKEN>' in row['record']['stdout']
    assert len(row['record']['stdout'].encode()) < judges.STDOUT_LIMIT + 200 and row['record']['stdout'].endswith('<truncated>')


def test_q05_priority_input_hash_beats_deadline_and_timeout(tmp_path):
    obj = json.loads(packet()); obj['proposal']['qty'] += 100
    r = review(tmp_path, packet=json.dumps(obj), response=record('claude', exit_code=1, elapsed_seconds=999),
               received_at=NOW.replace(hour=9))
    assert r['failure'] == 'hash_mismatch' and r['verdict'].decision == 'INVALID'
    r = review(tmp_path, response=record('claude', exit_code=1, elapsed_seconds=999), received_at=NOW.replace(minute=16))
    assert r['failure'] == 'deadline'
    r = review(tmp_path, response=record('claude', exit_code=1, elapsed_seconds=999))
    assert r['failure'] == 'timeout' and r['verdict'].decision == 'ABSTAIN'


def test_q06_deadline_uses_jst_date_of_expiry_even_in_utc_inputs(tmp_path):
    utc_now = (NOW - timedelta(minutes=30)).astimezone(timezone.utc)
    r = review(tmp_path, now=utc_now, received_at=NOW.replace(minute=15).astimezone(timezone.utc))
    assert r['failure'] is None
    r = review(tmp_path, now=utc_now, received_at=(NOW.replace(minute=15) + timedelta(microseconds=1)).astimezone(timezone.utc))
    assert r['failure'] == 'deadline'


@pytest.mark.parametrize('raw', ['{"proposal_id":"x"} {"a":1}', '{"proposal_id": NaN}', '{"proposal_id":"x","x":Infinity}', '"just a string"',
                                 json.dumps(dict(answer(), extra=None))])
def test_q07_more_malformed_shapes(tmp_path, raw):
    r = review(tmp_path, response=record('claude', raw))
    assert r['failure'] == 'malformed' and r['verdict'].decision == 'INVALID'


def test_q08_pure_abstain_has_no_failure_and_replay_file_is_read_only(tmp_path):
    rec = record('codex', answer(decision='ABSTAIN', reason='情報不足'))
    p = tmp_path / 'rec.json'; p.write_text(json.dumps(rec), encoding='utf-8'); before = p.read_bytes()
    r = review(tmp_path, 'codex', response=None, replay_path=p)
    assert r['failure'] is None and r['verdict'].decision == 'ABSTAIN' and p.read_bytes() == before


def test_q09_build_cli_request_creates_nothing_and_uses_unique_output(tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    a = judges.build_cli_request(judge='codex', packet=packet(), model='m', work_dir=tmp_path, prompt_version='review-v1')
    b = judges.build_cli_request(judge='codex', packet=packet(), model='m', work_dir=tmp_path, prompt_version='review-v1')
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert a['argv'][a['argv'].index('--output-last-message') + 1] != b['argv'][b['argv'].index('--output-last-message') + 1]
    assert 'dummy-access-token-XYZ' not in a['stdin'] and '数量' in a['stdin']
    with pytest.raises(ValueError):
        judges.build_cli_request(judge='codex', packet=packet(), model='m', work_dir=tmp_path, prompt_version='../etc/passwd')


# ---- notify -------------------------------------------------------------------------------------

def proposal(**changes):
    d = dict(proposal_id='A1-20260909-6857-01', packet_hash='', code='6857', side='BUY', qty=100, lot_size=100, limit_price=1000.,
             exec_condition='OPENING_LIMIT', account_type='CASH', strategy='s', strategy_version='v1', as_of=date(2026, 9, 8),
             snapshot_id='s1', policy_version='p1', expires_at=NOW.replace(hour=8, minute=59), reason='r',
             events={'next_earnings_date': None, 'margin_regulated': False})
    d.update(changes)
    from dataclasses import replace
    p = Proposal(**d)
    return replace(p, packet_hash=compute_packet_hash(p))


def votes(p):
    return [Verdict(j, p.proposal_id, p.packet_hash, 'APPROVE', (), j, None, NOW, 'm', 'c', j) for j in ('claude', 'codex')]


def card(p=None, kind='NEW', **ctx):
    p = p or proposal()
    context = {'name': 'n', 'new_sent_today': 0, 'max_new_per_day': 2, 'account_confirmed_at': NOW, 'available_after': 1}
    context.update(ctx)
    return notify.render_message(kind=kind, proposal=p, verdicts=votes(p), context=context, settings=SETTINGS)


@pytest.fixture
def ledger(tmp_path):
    l = Ledger(tmp_path / 'ledger.sqlite')
    l.init_snapshot(1000000, [], [], NOW - timedelta(days=1))
    l.create_notice(proposal(), at=NOW)
    l.set_notice_state(proposal().proposal_id, 'APPROVED', NOW)
    yield l
    l.close()


def notifier(tmp_path, ledger, results=None, settings=None):
    return notify.Notifier(ledger=ledger, state_path=tmp_path / 'n.sqlite', settings=settings or SETTINGS,
                           transport='stub', stub_results=results or [])


def signed(events):
    body = json.dumps({'events': events}, ensure_ascii=False).encode()
    return body, base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


def test_r01_render_rejects_margin_or_wrong_side_and_bad_template_placeholders():
    with pytest.raises(ValueError):
        card(proposal(account_type='MARGIN'))
    with pytest.raises(ValueError):
        card(proposal(side='SELL'))                                     # NEW に SELL
    with pytest.raises(ValueError):
        card(kind='EXIT')                                                # EXIT に BUY
    for tmpl in ('https://www.rakuten-sec.co.jp/{code}/{code}', 'https://www.rakuten-sec.co.jp/?c={code}&x={0}', '', None):
        with pytest.raises(ValueError):
            notify.render_message(kind='NEW', proposal=proposal(), verdicts=votes(proposal()),
                                  context={'name': 'n'}, settings={**SETTINGS, 'broker': {'link_template': tmpl}})
    assert notify.broker_link('https://www.RAKUTEN-SEC.co.jp/x?code={code}', '6857').startswith('https://')
    with pytest.raises(ValueError):
        notify.broker_link('https://www.rakuten-sec.co.jp/{code}', '68 57')


def test_r02_enqueue_contract_errors_and_same_candidate_dedup_across_keys(tmp_path, ledger):
    n = notifier(tmp_path, ledger)
    m = card()
    for bad in (dict(key=''), dict(kind='PUSH'), dict(proposal_id=None), dict(proposal_id='missing'), dict(message={'no': 'type'})):
        args = dict(key='k', kind='NEW', message=m, proposal_id=proposal().proposal_id, now=NOW)
        args.update(bad)
        with pytest.raises(ValueError):
            n.enqueue(**args)
    assert n.enqueue(key='k', kind='NEW', message=m, proposal_id=proposal().proposal_id, now=NOW)['duplicate'] is False
    again = n.enqueue(key='k2', kind='NEW', message=m, proposal_id=proposal().proposal_id, now=NOW)   # キーを変えても同一候補
    assert again['duplicate'] is True and again['key'] == 'k'
    assert len(n.flush(now=NOW)) == 1


def test_r03_unknown_keeps_budget_and_retry_key_then_expires_after_24h(tmp_path, ledger):
    n = notifier(tmp_path, ledger, ['timeout', 'timeout'])
    n.enqueue(key='risk', kind='RISK', message={'type': 'text', 'text': 'x'}, proposal_id=None, now=NOW)
    first = n.flush(now=NOW)[0]
    assert first['state'] == 'UNKNOWN' and n.status(now=NOW)['monthly_used'] == 1
    assert 'SEND_UNKNOWN' in n.status(now=NOW)['alerts']
    second = n.flush(now=NOW + timedelta(hours=1))[0]
    assert second['state'] == 'UNKNOWN' and second['retry_key'] == first['retry_key'] and n.status(now=NOW)['monthly_used'] == 1
    late = n.flush(now=NOW + timedelta(hours=25))[0]                      # 再試行期限超過: 新キーを作らず照合待ち
    assert late['state'] == 'UNKNOWN' and late['retry_key'] == first['retry_key']
    assert 'RETRY_EXPIRED' in n.status(now=NOW + timedelta(hours=25))['alerts'] and n.stub_results == []


def test_r04_failure_releases_budget_and_blocked_row_stays_pending_for_next_month(tmp_path, ledger):
    n = notifier(tmp_path, ledger, ['failure'], settings={**SETTINGS, 'line': {'allowed_user_id': USER, 'monthly_budget': 1}})
    n.enqueue(key='a', kind='RISK', message={'type': 'text', 'text': 'a'}, proposal_id=None, now=NOW)
    n.enqueue(key='b', kind='RISK', message={'type': 'text', 'text': 'b'}, proposal_id=None, now=NOW)
    assert [r['state'] for r in n.flush(now=NOW)] == ['PENDING', 'PENDING'] and n.status(now=NOW)['monthly_used'] == 0
    n.stub_results[:] = ['success', 'success']
    assert [r['state'] for r in n.flush(now=NOW)] == ['SENT', 'BUDGET_BLOCKED']
    assert n.status(now=NOW)['monthly_used'] == 1 and 'BUDGET_EXCEEDED' in n.status(now=NOW)['alerts']
    october = NOW.replace(month=10, day=1)
    assert n.status(now=october)['monthly_used'] == 0 and 'BUDGET_EXCEEDED' not in n.status(now=october)['alerts']
    assert [r['state'] for r in n.flush(now=october)] == ['EXPIRED']        # V06: 登録日を過ぎた日次通知は新月に一括配信しない（失効）
    assert n.status(now=october)['monthly_used'] == 0


def test_r05_stop_file_is_or_condition_and_survives_resume(tmp_path, ledger):
    stop_file = tmp_path / 'STOP'
    settings = {**SETTINGS, 'stop_file': str(stop_file)}
    n = notifier(tmp_path, ledger, ['success'] * 3, settings=settings)
    n.enqueue(key='new', kind='NEW', message=card(), proposal_id=proposal().proposal_id, now=NOW)
    stop_file.write_text('stop')
    assert n.flush(now=NOW)[0]['state'] == 'STOPPED' and n.status(now=NOW)['stopped'] is True
    body, sig = signed([{'type': 'message', 'webhookEventId': 'r1', 'timestamp': int(NOW.timestamp() * 1000),
                         'source': {'type': 'user', 'userId': USER}, 'message': {'type': 'text', 'text': 'RESUME', 'id': '1'}}])
    r = notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW + timedelta(seconds=5), reconciled_at=NOW + timedelta(seconds=1))
    assert r['results'][0]['status'] == 'RESUMED' and n.status(now=NOW)['stopped'] is True      # STOP ファイルが残る限り実効停止
    stop_file.unlink()
    assert n.status(now=NOW)['stopped'] is False and n.flush(now=NOW)[0]['state'] == 'SENT'


def test_r06_crash_between_success_record_and_ledger_update_is_recoverable(tmp_path, ledger, monkeypatch):
    n = notifier(tmp_path, ledger, ['success', 'success'])
    n.enqueue(key='new', kind='NEW', message=card(), proposal_id=proposal().proposal_id, now=NOW)
    real = ledger.set_notice_state
    monkeypatch.setattr(ledger, 'set_notice_state', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('crash after send')))
    with pytest.raises(RuntimeError):
        n.flush(now=NOW)
    monkeypatch.setattr(ledger, 'set_notice_state', real)
    assert n.status(now=NOW)['monthly_used'] == 1                                # 送信成功は耐久記録済み（二重送信しない）
    assert ledger.notice(proposal().proposal_id)['notice_state'] == 'APPROVED'
    assert n.flush(now=NOW) == [] and n.stub_results == ['success']             # SENT 行は再送しない
    assert n.reconcile_sent(now=NOW) == ['new']                                  # 同一成功から台帳状態を再適用
    assert ledger.notice(proposal().proposal_id)['notice_state'] == 'SENT'


def test_r07_concurrent_flush_sends_each_row_once(tmp_path, ledger):
    n1 = notifier(tmp_path, ledger, ['success'] * 5)
    for i in range(5):
        n1.enqueue(key=f'r{i}', kind='RISK', message={'type': 'text', 'text': str(i)}, proposal_id=None, now=NOW)
    n2 = notify.Notifier(ledger=ledger, state_path=tmp_path / 'n.sqlite', settings=SETTINGS, transport='stub', stub_results=['success'] * 5)
    out = {}
    def run(name, n):
        out[name] = n.flush(now=NOW)
    threads = [threading.Thread(target=run, args=('a', n1)), threading.Thread(target=run, args=('b', n2))]
    for t in threads: t.start()
    for t in threads: t.join()
    sent = [r['key'] for rows in out.values() for r in rows if r['state'] == 'SENT']
    assert sorted(sent) == [f'r{i}' for i in range(5)] and len(sent) == 5
    assert n1.status(now=NOW)['monthly_used'] == 5 and len(n1.stub_results) + len(n2.stub_results) == 5


def test_r08_webhook_rejects_bad_json_after_good_signature_and_non_dict_events(tmp_path, ledger):
    n = notifier(tmp_path, ledger)
    body = b'{"events": [1, {"type": "postback"}]}'
    sig = base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    r = notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW)
    assert r['http_status'] == 200 and [x['status'] for x in r['results']] == ['INVALID', 'INVALID']
    body = b'{not json'
    sig = base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    assert notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW) == {'http_status': 400, 'results': []}
    os.environ.pop('LINE_CHANNEL_SECRET')
    assert notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW)['http_status'] == 401


def test_r09_webhook_report_edge_cases(tmp_path, ledger):
    n = notifier(tmp_path, ledger)
    ledger.set_notice_state(proposal().proposal_id, 'SENT', NOW)
    def ev(eid, **data):
        payload = dict(action='FILLED', proposal_id=proposal().proposal_id, qty=100, price=990, fee=0)
        payload.update(data)
        return {'type': 'postback', 'webhookEventId': eid, 'timestamp': int(NOW.replace(hour=9).timestamp() * 1000),
                'source': {'type': 'user', 'userId': USER}, 'postback': {'data': json.dumps(payload)}}
    body, sig = signed([ev('e1', qty='100'), ev('e2', qty=True), ev('e3', at='2026-09-09T09:00:00'),   # 型違反・naive
                        ev('e4', action='BUY'), ev('e5', qty=200), ev('e6')])
    r = notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW.replace(hour=10))
    statuses = [x['status'] for x in r['results']]
    assert statuses[:4] == ['INVALID', 'NEEDS_DETAILS', 'INVALID', 'INVALID']
    assert statuses[4] == 'INVALID' and statuses[5] == 'APPLIED'                 # 200 株は残数量超過 → 台帳拒否、正当な 100 株は適用
    assert ledger.cash() == 901000
    # 同じイベントの再送（配送メタデータだけ違う）は DUPLICATE、実約定の再報告も台帳では冪等
    e6 = ev('e6'); e6['deliveryContext'] = {'isRedelivery': True}
    body, sig = signed([e6])
    assert notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW.replace(hour=10))['results'][0]['status'] == 'DUPLICATE'
    assert ledger.cash() == 901000


def test_r10_status_text_and_card_never_leak_confidence_even_via_context(tmp_path):
    m = card(text='confidence 0.9 と書いてある参照', status='NO_SIGNAL')
    s = json.dumps(m, ensure_ascii=False)
    assert 'confidence' not in s and '0.9' not in s
    with pytest.raises(ValueError):
        notify.render_message(kind='RISK', context={}, settings=SETTINGS)
    with pytest.raises(ValueError):
        notify.render_message(kind='RECONCILE', context={'status': 'OK'}, settings=SETTINGS)
