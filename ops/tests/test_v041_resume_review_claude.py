"""2026-09-11 第18回：ops v0.4.1（第17回dev・99%枠の継続分）の Claude 独立レビュー。

対象は Codex が単独で継続した期間（2026-09-09〜09-11）の ops/ 側の補強、とくに
制御イベント（STOP/RESUME）の時刻契約・通知キューの予算/状態・台帳の内部属性/移行マーカー。
既存の受入テスト・Codex 試験・主系・共通仕様は一切変更していない。skip/xfail/条件緩和もしない。

このファイルは build-codex の `aitrader` パッケージに依存しない（ops 単独で収集・実行できる）。
"""
import json
import sqlite3
import sys
import tempfile
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops')]
from aitrader_ops import notify                                    # noqa: E402
from aitrader_ops.ledger import Ledger, LedgerError                # noqa: E402
from aitrader_ops.models import Proposal, PositionIn, compute_packet_hash  # noqa: E402
from aitrader_ops.stop_status import inspect_stop_status           # noqa: E402

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 9, 7, 0, tzinfo=JST)
USER = 'U-review-allowed'
SETTINGS = {'broker': {'link_template': 'https://www.rakuten-sec.co.jp/dummy?code={code}'},
            'line': {'allowed_user_id': USER, 'monthly_budget': 200}}


def proposal(pid='P1', **changes):
    fields = dict(proposal_id=pid, packet_hash='', code='6857', side='BUY', qty=100, lot_size=100,
                  limit_price=1000., exec_condition='OPENING_LIMIT', account_type='CASH', strategy='s',
                  strategy_version='v1', as_of=date(2026, 9, 8), snapshot_id='s1', policy_version='p1',
                  expires_at=NOW.replace(hour=8, minute=59), reason='r',
                  events={'next_earnings_date': None, 'margin_regulated': False})
    fields.update(changes)
    p = Proposal(**fields)
    return replace(p, packet_hash=compute_packet_hash(p))


@pytest.fixture
def env(tmp_path):
    """台帳 + 通知アダプタ。実 CLI・実 LINE・実ネットワークは使わない（transport='stub'）。"""
    opened = []

    def make(results=(), budget=200, state_name='notify.sqlite'):
        ledger = Ledger(tmp_path / 'ledger.sqlite')
        ledger.init_snapshot(1_000_000, [], [], NOW - timedelta(days=1))
        settings = dict(SETTINGS, line=dict(SETTINGS['line'], monthly_budget=budget))
        notifier = notify.Notifier(ledger=ledger, state_path=tmp_path / state_name,
                                   settings=settings, transport='stub', stub_results=list(results))
        opened.append((notifier, ledger))
        return ledger, notifier

    yield make
    for notifier, ledger in opened:
        notifier.close()
        ledger.close()


def approved(ledger, notifier, key, pid, at=NOW):
    ledger.create_notice(proposal(pid), at=at)
    ledger.set_notice_state(pid, 'APPROVED', at)
    notifier.enqueue(key=key, kind='NEW', message={'type': 'text', 'text': key},
                     proposal_id=pid, now=at)


# ---- W01（A）: 直接 API の未来時刻 STOP/RESUME が緊急停止を無効化する ----------------------

def test_w01a_future_dated_resume_must_not_disable_a_later_real_stop(env):
    """webhook は未来時刻の制御を INVALID で拒否するが、直接 Notifier API には同じ検査がない。

    未来時刻の RESUME が controls の最新 event_at を未来へ進めるため、その後の**本物の STOP**が
    すべて遅着（STOP_LATE）と判定され、stopped が立たない。緊急停止が黙って効かなくなる。
    """
    ledger, n = env()
    assert n.stop(now=NOW, event_at=NOW) == 'STOPPED' and n.is_stopped()
    assert n.resume(now=NOW + timedelta(hours=1), reconciled_at=NOW + timedelta(minutes=30),
                    event_at=NOW + timedelta(days=1)) == 'INVALID'
    later = NOW + timedelta(hours=2)
    assert n.stop(now=later, event_at=later) == 'STOPPED', '本物の STOP が遅着扱いで無視されている'
    assert n.is_stopped(), 'STOP 後も stopped が立たない（緊急停止が無効）'


def test_w01b_future_dated_stop_must_not_block_a_later_real_resume(env):
    """逆向き。未来時刻の STOP を受けると、その後の正当な RESUME が遅着扱いで復帰できない。"""
    ledger, n = env()
    assert n.stop(now=NOW, event_at=NOW + timedelta(days=1)) == 'INVALID'
    later = NOW + timedelta(hours=1)
    assert n.resume(now=later, reconciled_at=NOW + timedelta(minutes=30), event_at=later) == 'RESUMED'
    assert not n.is_stopped()


def test_w01c_new_candidates_still_sent_after_an_ignored_real_stop(env):
    """W01 の実害。STOP を押したのに NEW 候補が送信され続ける。"""
    ledger, n = env(results=['success'])
    n.stop(now=NOW, event_at=NOW)
    n.resume(now=NOW + timedelta(minutes=1), reconciled_at=NOW, event_at=NOW + timedelta(days=1))
    n.stop(now=NOW + timedelta(minutes=2), event_at=NOW + timedelta(minutes=2))   # 本物の STOP
    approved(ledger, n, 'k1', 'P1')
    states = [r['state'] for r in n.flush(now=NOW + timedelta(minutes=3))]
    assert states == ['STOPPED'], f'STOP 後に NEW を送信した: {states}'


def test_w01d_writer_and_reader_disagree_about_the_poisoned_history(env, tmp_path):
    """書き手（Notifier）は未来 event_at を受け入れるが、読み手（stop_status）は同じ履歴を無効と判定する。

    結果として「送信は続くのに管理状態は UNKNOWN」という不一致になる。片側だけの検査では防げない。
    """
    ledger, n = env()
    n.stop(now=NOW, event_at=NOW)
    n.resume(now=NOW + timedelta(hours=1), reconciled_at=NOW + timedelta(minutes=30),
             event_at=NOW + timedelta(days=1))
    state_path = Path(n.path)
    n.close()
    ledger.close()
    report = inspect_stop_status(state_path, now=NOW + timedelta(hours=2))
    assert report['status'] != 'UNKNOWN', f'書き手が受理した履歴を読み手が読めない: {report["reasons"]}'


# ---- W02（B）: 同一候補・別キー・内容変更の enqueue が黙って捨てられる ----------------------

def test_w02_enqueue_of_changed_content_under_a_new_key_is_not_silently_dropped(env):
    """同じ key なら内容差で ValueError になるが、別 key + 同一候補では旧行を duplicate として返すだけ。

    訂正後の本文（指値の訂正など）が警告なく配信されない。二重通知の抑止は正しいが、
    「完全な重複」と「内容が変わった再登録」は呼出側が区別できる必要がある。
    """
    ledger, n = env()
    approved(ledger, n, 'k1', 'P1')
    result = n.enqueue(key='k2', kind='NEW', message={'type': 'text', 'text': '訂正後の本文'},
                       proposal_id='P1', now=NOW)
    assert result.get('changed') is True or result.get('state') == 'CONFLICT', (
        f'内容が変わったのに完全重複と同じ応答: {result}')


# ---- 以下は今回の補強が保たれていることの確認（通過すべき回帰） ------------------------------

def test_w03_webhook_still_rejects_future_control_events(env):
    """webhook 側の未来時刻拒否（V10）は維持されている。W01 との対比。"""
    import base64
    import hashlib
    import hmac
    import os
    ledger, n = env()
    os.environ['LINE_CHANNEL_SECRET'] = 'secret-for-review'
    event = {'type': 'message', 'webhookEventId': 'e1',
             'timestamp': int((NOW + timedelta(hours=5)).timestamp() * 1000),
             'source': {'type': 'user', 'userId': USER},
             'message': {'type': 'text', 'text': 'STOP'}}
    body = json.dumps({'events': [event]}).encode('utf-8')
    sig = base64.b64encode(hmac.new(b'secret-for-review', body, hashlib.sha256).digest()).decode()
    result = notify.handle_webhook(notifier=n, body=body, signature=sig, now=NOW)
    assert result['results'][0]['status'] == 'INVALID' and not n.is_stopped()


def test_w04_stop_status_reader_rejects_future_control_history(env, tmp_path):
    """読み手は未来時刻の制御履歴を UNKNOWN（effective_stop=True）にする＝保守側に倒す。"""
    ledger, n = env(state_name='reader.sqlite')
    n.stop(now=NOW, event_at=NOW)
    path = Path(n.path)
    n.close()
    ledger.close()
    with sqlite3.connect(path) as con:
        con.execute("UPDATE controls SET event_at=? WHERE seq=1", [(NOW + timedelta(days=2)).isoformat()])
    report = inspect_stop_status(path, now=NOW + timedelta(hours=1))
    assert report['status'] == 'UNKNOWN' and report['effective_stop'] is True


def test_w05_internal_attributes_from_external_input_are_never_persisted(env):
    """第5回の指摘（内部属性の偽装）が TRADE / CSV の両方で塞がれていること。"""
    ledger, n = env()
    ledger.create_notice(proposal('P1'), at=NOW + timedelta(hours=2))
    report = ledger.report(dict(event_id='f1', proposal_id='P1', kind='FILLED', qty=100, price=1000.,
                                fee=0., at=NOW + timedelta(hours=1), source='line',
                                broker_order_id='o1', _applied_proposal_id='ghost'))
    assert report.applied
    with sqlite3.connect(ledger.path) as con:
        stored = json.loads(con.execute("SELECT payload FROM ledger_events WHERE kind='TRADE'").fetchone()[0])
    assert '_applied_proposal_id' not in stored
    assert ledger.replay(NOW + timedelta(hours=1, minutes=30)).cash == 900_000


def test_w06_fractional_cashout_requires_a_held_code(env):
    """端株精算は保有銘柄に限る（第5回 D12 の指摘が契約化されていること）。"""
    ledger, n = env()
    for code in ('9999', None):
        with pytest.raises(LedgerError):
            ledger.adjust('FRACTIONAL_CASHOUT', 500, code, None, NOW, 'unheld')
    assert ledger.cash() == 1_000_000


def test_w07_second_policy_upgrade_marker_is_rejected(env, tmp_path):
    """移行マーカーの二重付与が拒否されること（第5回 D06 の指摘の一部）。"""
    path = tmp_path / 'legacy.sqlite'
    legacy = Ledger(path)
    legacy.init_snapshot(1_000_000, [PositionIn('6857', 100, 1000.)], [], NOW - timedelta(days=1))
    legacy.close()
    for i in (1, 2):
        with sqlite3.connect(path) as con:
            con.execute('INSERT INTO ledger_events(event_id,kind,at,payload,recorded_at) VALUES(?,?,?,?,?)',
                        [f'pu{i}', 'POLICY_UPGRADE', NOW.isoformat(),
                         json.dumps(dict(schema_version=3, at=NOW.isoformat(), legacy_last_seq=1)), NOW.isoformat()])
    with pytest.raises(LedgerError):
        Ledger(path)


def test_w08_unknown_row_keeps_its_original_month_and_stops_at_the_retry_window(env):
    """成否不明行は初回の月枠を保持し、24 時間の再試行期限を超えたら新送信しない。"""
    ledger, n = env(results=['timeout'], budget=1)
    approved(ledger, n, 'k1', 'P1')
    assert [r['state'] for r in n.flush(now=NOW)] == ['UNKNOWN']
    assert n.status(now=NOW)['monthly_used'] == 1
    much_later = NOW + timedelta(days=25)
    n.stub_results = ['success']
    assert [r['state'] for r in n.flush(now=much_later)] == ['UNKNOWN'], '再試行期限後に新規送信した'
    assert n.stub_results == ['success'], '再試行期限後に送信関数を呼んでいる'
    rows = n._con.execute('SELECT state, month FROM outbox').fetchall()
    assert rows == [('UNKNOWN', '2026-09')]
    assert 'RETRY_EXPIRED' in n.status(now=much_later)['alerts']
