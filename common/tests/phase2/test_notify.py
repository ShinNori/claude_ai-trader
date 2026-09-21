"""順序4通知・受信の先行受入契約。LINE通信は全ケースで禁止。"""
import base64
import hashlib
import hmac
import importlib
import json
import socket
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'ops'), str(ROOT/'build-codex')]
from aitrader_ops.ledger import Ledger
from aitrader_ops.models import Proposal, Verdict, PositionIn, compute_packet_hash

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 9, 7, 0, tzinfo=JST)
SECRET = 'dummy-channel-secret'
USER = 'U-dummy-allowed'
SETTINGS = {'broker': {'link_template': 'https://www.rakuten-sec.co.jp/dummy?code={code}'},
            'line': {'allowed_user_id': USER, 'monthly_budget': 200}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('実CLI・ネットワークは禁止')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setenv('LINE_CHANNEL_SECRET', SECRET)
    monkeypatch.setenv('LINE_CHANNEL_ACCESS_TOKEN', 'dummy-access-token')


def api():
    return importlib.import_module('aitrader_ops.notify')


def proposal(**changes):
    data = dict(proposal_id='A1-20260909-6857-01', packet_hash='', code='6857', side='BUY',
        qty=100, lot_size=100, limit_price=1000., exec_condition='OPENING_LIMIT',
        account_type='CASH', strategy='margin_bucket_long', strategy_version='v1',
        as_of=date(2026, 9, 8), snapshot_id='s1', policy_version='p1',
        expires_at=NOW.replace(hour=8, minute=59), reason='信用残減少',
        events={'next_earnings_date': None, 'margin_regulated': False})
    data.update(changes); p = Proposal(**data)
    return replace(p, packet_hash=compute_packet_hash(p))


def votes(p):
    return [Verdict(j, p.proposal_id, p.packet_hash, 'APPROVE', ('EVENT_UNKNOWN',),
        j+'の理由', .987654321, NOW, 'dummy', 'dummy', j+'-run') for j in ('claude', 'codex')]


def message(kind='NEW', **changes):
    p = proposal(side='SELL' if kind == 'EXIT' else 'BUY')
    args = dict(kind=kind, proposal=p if kind in ('NEW', 'EXIT') else None,
        verdicts=votes(p) if kind in ('NEW', 'EXIT') else (), settings=SETTINGS,
        context={'name': 'テスト銘柄', 'new_sent_today': 1, 'max_new_per_day': 2,
            'account_confirmed_at': NOW-timedelta(days=1), 'available_after': 899800,
            'text': '口座照合が必要', 'status': 'NO_SIGNAL'})
    args.update(changes)
    return api().render_message(**args)


@pytest.fixture
def ledger(tmp_path):
    l = Ledger(tmp_path/'ledger.sqlite')
    l.init_snapshot(1000000, [], [], NOW-timedelta(days=1))
    l.create_notice(proposal(), at=NOW)
    l.set_notice_state(proposal().proposal_id, 'APPROVED', NOW)
    yield l
    l.close()


def notifier(tmp_path, ledger, results=None):
    return api().Notifier(ledger=ledger, state_path=tmp_path/'notify.sqlite',
        settings=SETTINGS, transport='stub', stub_results=results or [])


def enqueue(n, kind='NEW', key='k1', now=NOW):
    return n.enqueue(key=key, kind=kind, message=message(kind),
        proposal_id=proposal().proposal_id if kind == 'NEW' else None, now=now)


def event(action='ORDERED', event_id='ev1', at=None, **data):
    payload = dict(action=action, proposal_id=proposal().proposal_id)
    payload.update(data)
    return {'type': 'postback', 'webhookEventId': event_id,
        'timestamp': int((at or NOW.replace(hour=9)).timestamp()*1000),
        'source': {'type': 'user', 'userId': USER},
        'postback': {'data': json.dumps(payload, ensure_ascii=False)}}


def receive(n, events, signature=None, now=None, reconciled_at=None, body=None):
    body = body if body is not None else json.dumps({'events': events}, ensure_ascii=False).encode('utf-8')
    signature = signature if signature is not None else base64.b64encode(
        hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    return api().handle_webhook(notifier=n, body=body, signature=signature,
        now=now or NOW.replace(hour=10), reconciled_at=reconciled_at)


def test_flex_card_fields():
    """カードの判断材料・手動確認・ボタンを欠かさず確信度は載せない。"""
    m = message(); s = json.dumps(m, ensure_ascii=False)
    assert m['type'] == 'flex' and m['altText'] and m['contents']['type'] == 'bubble'
    for expected in ['6857', 'テスト銘柄', '100', '1,000', '08:59', '寄付指値', '現物',
        proposal().proposal_id, 'margin_bucket_long', 'v1', 'claudeの理由', 'codexの理由',
        '1/2', '899,800', '2026-09-08', '7:40', '余力', '保有', '未約定',
        '発注した', '一部約定', '全部約定', '取消した', '見送り']:
        assert expected in s
    assert 'confidence' not in s and '0.987654321' not in s
    assert 'https://www.rakuten-sec.co.jp/dummy?code=6857' in s


@pytest.mark.parametrize('kind', ['NEW', 'EXIT', 'RECONCILE', 'RISK'])
def test_four_message_kinds(kind):
    """4種類を個別に生成し候補以外に二重承認を要求しない。"""
    assert message(kind)['type'] in ('text', 'flex')


@pytest.mark.parametrize('status,text', [('NO_SIGNAL', '本日サインなし'),
    ('REVIEW_INCOMPLETE', '審査未完了'), ('REJECTED', '不承認'),
    ('DATA_MISSING', 'データ不足'), ('ERROR', '障害')])
def test_distinct_status_text(status, text):
    """候補なしと審査失敗を同じ成功文面へ丸めない。"""
    m = message('RECONCILE', context={'status': status})
    assert text in json.dumps(m, ensure_ascii=False)


@pytest.mark.parametrize('url', ['http://www.rakuten-sec.co.jp/{code}',
    'https://rakuten-sec.co.jp.evil.example/{code}', 'https://evil.example/{code}',
    'https://rakuten-sec.co.jp@evil.example/{code}',
    'https://member.rakuten-sec.co.jp/{code}', 'javascript:alert(1)',
    'https://www.rakuten-sec.co.jp/{unknown}'])
def test_bad_broker_link(url):
    """偽ドメイン・会員セッションURL・未定義置換を拒否する。"""
    with pytest.raises(ValueError):
        message(settings={**SETTINGS, 'broker': {'link_template': url}})


def test_outbox_success_and_dedup(tmp_path, ledger):
    """成功後だけSENT、同一キー再起動再送でも二重配信しない。"""
    n = notifier(tmp_path, ledger, ['success'])
    enqueue(n)
    assert ledger.notice(proposal().proposal_id)['notice_state'] == 'APPROVED'
    assert n.flush(now=NOW)[0]['state'] == 'SENT'
    assert ledger.notice(proposal().proposal_id)['notice_state'] == 'SENT'
    again = notifier(tmp_path, ledger, ['success'])
    assert enqueue(again)['duplicate'] is True
    assert again.flush(now=NOW) == [] and again.status(now=NOW)['monthly_used'] == 1


@pytest.mark.parametrize('failure', ['failure', 'timeout'])
def test_failed_send_keeps_reservation(tmp_path, ledger, failure):
    """送信失敗・成否不明ではSENTや予約解放を先取りしない。"""
    n = notifier(tmp_path, ledger, [failure, 'success']); enqueue(n)
    before = ledger.reserved()
    first = n.flush(now=NOW)[0]
    assert first['state'] == ('UNKNOWN' if failure == 'timeout' else 'PENDING')
    assert ledger.notice(proposal().proposal_id)['notice_state'] == 'APPROVED'
    assert ledger.reserved() == before
    retry = n.flush(now=NOW+timedelta(seconds=1))[0]
    assert retry['state'] == 'SENT' and retry['retry_key'] == first['retry_key']
    assert n.status(now=NOW)['monthly_used'] == 1


def test_same_key_changed_message(tmp_path, ledger):
    """同じ冪等キーの内容差し替えで新たな通知を作らない。"""
    n = notifier(tmp_path, ledger); enqueue(n)
    with pytest.raises(ValueError):
        n.enqueue(key='k1', kind='RISK', message={'type':'text','text':'変更'}, proposal_id=None, now=NOW)


def test_unapproved_candidate_blocked(tmp_path, ledger):
    """未承認候補を送信アダプタだけで配信できない。"""
    p = proposal(proposal_id='unapproved'); ledger.create_notice(p, at=NOW)
    n = notifier(tmp_path, ledger)
    with pytest.raises(ValueError):
        n.enqueue(key='bad', kind='NEW', message=message(proposal=p, verdicts=votes(p)),
            proposal_id=p.proposal_id, now=NOW)


def test_expired_queue_never_sent(tmp_path, ledger):
    """候補送信待ちを07:15以後に再送せず予約は保持する。"""
    n = notifier(tmp_path, ledger, ['success']); enqueue(n); reserved = ledger.reserved()
    r = n.flush(now=NOW.replace(minute=15, second=1))
    assert r[0]['state'] == 'EXPIRED'
    assert ledger.notice(proposal().proposal_id)['notice_state'] != 'SENT'
    assert ledger.reserved() == reserved and n.status(now=NOW)['monthly_used'] == 0


def test_budget_200_and_month_rollover(tmp_path, ledger):
    """月200通の境界を永続化し超過時はローカル障害状態へ残す。"""
    n = notifier(tmp_path, ledger, ['success']*201)
    for i in range(201):
        enqueue(n, 'RISK', key=f'risk-{i}')
    rows = n.flush(now=NOW)
    assert sum(row['state']=='SENT' for row in rows) == 200
    assert rows[-1]['state'] == 'BUDGET_BLOCKED'
    again = notifier(tmp_path, ledger, ['success'])
    status = again.status(now=NOW)
    assert status['monthly_used'] == 200 and 'BUDGET_EXCEEDED' in status['alerts']
    october = NOW.replace(month=10, day=1)
    assert again.status(now=october)['monthly_used'] == 0


def test_stop_new_only_and_authenticated_resume(tmp_path, ledger):
    """STOPは新規だけ抑止し再開に停止後の口座照合を必須にする。"""
    n = notifier(tmp_path, ledger, ['success']*4)
    stop = event('STOP', at=NOW)
    assert receive(n, [stop], now=NOW)['results'][0]['status'] == 'STOPPED'
    enqueue(n)
    for kind in ('RECONCILE', 'RISK'):
        enqueue(n, kind, key=kind)
    rows = n.flush(now=NOW)
    assert rows[0]['state'] == 'STOPPED'
    assert sum(r['state']=='SENT' for r in rows) == 2
    assert notifier(tmp_path, ledger).status(now=NOW)['stopped'] is True
    resume = event('RESUME', event_id='resume', at=NOW+timedelta(seconds=2))
    assert receive(n, [resume], now=NOW+timedelta(seconds=3))['results'][0]['status'] == 'RECONCILIATION_REQUIRED'
    resume['webhookEventId'] = 'resume2'
    assert receive(n, [resume], now=NOW+timedelta(seconds=3),
        reconciled_at=NOW+timedelta(seconds=1))['results'][0]['status'] == 'RESUMED'


@pytest.mark.parametrize('sig', ['', 'not-base64', base64.b64encode(b'wrong').decode()])
def test_bad_signature_before_parsing(tmp_path, ledger, sig):
    """署名をJSON解析より先に検証し台帳を変更しない。"""
    n = notifier(tmp_path, ledger); seq = ledger.seq()
    r = receive(n, [], signature=sig, body=b'not json')
    assert r['http_status'] == 401 and r['results'] == [] and ledger.seq() == seq


@pytest.mark.parametrize('source', [{'type':'user','userId':'U-attacker'},
    {'type':'group','userId':USER,'groupId':'G-dummy'}, {'type':'room','userId':USER}, {}])
def test_untrusted_source(tmp_path, ledger, source):
    """正しい署名でも許可本人の1対1トーク以外を拒否する。"""
    n = notifier(tmp_path, ledger); ev = event('STOP'); ev['source'] = source
    r = receive(n, [ev])
    assert r['results'][0]['status'] == 'FORBIDDEN'
    assert n.status(now=NOW)['stopped'] is False


@pytest.mark.parametrize('action,state', [('ORDERED','ORDERED'), ('PARTIAL','PARTIAL'),
    ('FILLED','FILLED'), ('CANCELLED','CANCELLED'), ('SKIPPED','SKIPPED')])
def test_report_mapping(tmp_path, ledger, action, state):
    """ボタン報告を既存reportへ写像し約定時だけ実残高を動かす。"""
    n = notifier(tmp_path, ledger)
    ledger.set_notice_state(proposal().proposal_id, 'SENT', NOW)
    data = dict(qty=40 if action=='PARTIAL' else 100, price=990, fee=0, broker_order_id='b1')
    r = receive(n, [event(action, **data)])
    assert r['http_status'] == 200 and r['results'][0]['status'] == 'APPLIED'
    assert ledger.notice(proposal().proposal_id)['trade_state'] == state
    expected = 960400 if action=='PARTIAL' else 901000 if action=='FILLED' else 1000000
    assert ledger.cash() == expected


@pytest.mark.parametrize('action', ['PARTIAL', 'FILLED'])
def test_fill_button_needs_actual_details(tmp_path, ledger, action):
    """ボタンだけでは約定数・価格・手数料を推定しない。"""
    n = notifier(tmp_path, ledger); seq = ledger.seq()
    r = receive(n, [event(action)])
    assert r['results'][0]['status'] == 'NEEDS_DETAILS'
    assert ledger.seq() == seq and ledger.cash() == 1000000


def test_webhook_duplicate_restart_and_conflict(tmp_path, ledger):
    """webhookEventIdを再起動後も一意管理し内容差し替えを拒否する。"""
    n = notifier(tmp_path, ledger)
    ev = event('FILLED', qty=100, price=990, fee=0, broker_order_id='b1')
    assert receive(n, [ev])['results'][0]['status'] == 'APPLIED'
    seq = ledger.seq(); n = notifier(tmp_path, ledger)
    assert receive(n, [ev])['results'][0]['status'] == 'DUPLICATE'
    ev['postback']['data'] = json.dumps(dict(action='FILLED', proposal_id=proposal().proposal_id,
        qty=100, price=1, fee=0, broker_order_id='b1'))
    assert receive(n, [ev])['results'][0]['status'] == 'CONFLICT'
    assert ledger.seq() == seq and ledger.cash() == 901000


def test_late_and_reordered_reports(tmp_path, ledger):
    """期限後の実約定は受け付け遅着発注報告で終端状態を戻さない。"""
    n = notifier(tmp_path, ledger)
    fill = event('FILLED', at=NOW.replace(hour=9), qty=100, price=990, fee=0)
    assert receive(n, [fill])['results'][0]['status'] == 'APPLIED'
    order = event('ORDERED', event_id='old-order', at=NOW.replace(hour=8))
    receive(n, [order])
    assert ledger.notice(proposal().proposal_id)['trade_state'] == 'FILLED'
    assert ledger.cash() == 901000 and ledger.reserved() == 0


def test_unknown_and_future_event(tmp_path, ledger):
    """不明候補と未来時刻を残高イベントへ入れない。"""
    n = notifier(tmp_path, ledger); seq = ledger.seq()
    ev = event(proposal_id='unknown')
    assert receive(n, [ev])['results'][0]['status'] == 'UNKNOWN_PROPOSAL'
    ev = event(event_id='future', at=NOW.replace(hour=11))
    assert receive(n, [ev])['results'][0]['status'] == 'INVALID'
    assert ledger.seq() == seq


def test_raw_body_signature_and_empty_events(tmp_path, ledger):
    """生バイト署名を使い空eventsの疎通確認は副作用なしで成功する。"""
    n = notifier(tmp_path, ledger); seq = ledger.seq()
    body = b'{ "events" : [] }'
    r = receive(n, [], body=body)
    assert r == {'http_status': 200, 'results': []} and ledger.seq() == seq


def test_exit_continues_during_stop(tmp_path):
    """STOP中でも承認済み保有株売却候補の配信を止めない。"""
    l = Ledger(tmp_path/'ledger.sqlite')
    try:
        l.init_snapshot(1000000, [PositionIn('6857', 100, 900)], [], NOW-timedelta(days=1))
        p = proposal(side='SELL'); l.create_notice(p, at=NOW)
        l.set_notice_state(p.proposal_id, 'APPROVED', NOW)
        n = notifier(tmp_path, l, ['success'])
        receive(n, [event('STOP', at=NOW)], now=NOW)
        n.enqueue(key='exit', kind='EXIT', message=message('EXIT'), proposal_id=p.proposal_id, now=NOW)
        assert n.flush(now=NOW)[0]['state'] == 'SENT'
        assert l.notice(p.proposal_id)['notice_state'] == 'SENT'
    finally:
        l.close()


def test_text_stop_and_payload_tampering(tmp_path, ledger):
    """文字STOPも認証し署名後の本文変更で停止を実行しない。"""
    n = notifier(tmp_path, ledger)
    ev = event(); ev['type'] = 'message'; del ev['postback']
    ev['message'] = {'type':'text', 'text':'STOP', 'id':'dummy'}
    body = json.dumps({'events':[ev]}).encode()
    sig = base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    assert receive(n, [], body=body+b' ', signature=sig)['http_status'] == 401
    assert n.status(now=NOW)['stopped'] is False
    assert receive(n, [], body=body, signature=sig)['results'][0]['status'] == 'STOPPED'


def test_partial_then_final_is_incremental(tmp_path, ledger):
    """部分約定と残り約定を増分で足し同一webhook再送を除外する。"""
    n = notifier(tmp_path, ledger)
    first = event('PARTIAL', qty=40, price=990, fee=0, broker_order_id='b1')
    last = event('FILLED', event_id='remaining', at=NOW.replace(hour=9, minute=1),
                 qty=60, price=1000, fee=0, broker_order_id='b1')
    assert receive(n, [first, last])['http_status'] == 200
    assert ledger.cash() == 900400 and ledger.positions()['6857'].qty == 100
    assert ledger.reserved() == 0
    receive(n, [last, first])
    assert ledger.cash() == 900400 and ledger.positions()['6857'].qty == 100


def test_missing_webhook_id(tmp_path, ledger):
    """一意ID欠損をランダムIDで補完して報告を重複させない。"""
    n = notifier(tmp_path, ledger); seq = ledger.seq()
    ev = event(); del ev['webhookEventId']
    assert receive(n, [ev])['results'][0]['status'] == 'INVALID'
    assert ledger.seq() == seq
