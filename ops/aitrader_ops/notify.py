"""通知アダプタ（フェーズ2 順序4、共通仕様_フェーズ2_順序4_修正提案_v0.1 §3〜4）。

- render_message: 通知 4 種（NEW / EXIT / RECONCILE / RISK）の本文を生成する。NEW / EXIT は LINE Flex カード 1 候補 1 枚。
  confidence はどのフィールドにも載せない。楽天リンクは settings.broker.link_template の {code} だけを展開し、
  https かつ rakuten-sec.co.jp（正規サブドメイン。member. は禁止）以外を拒否する。
- Notifier: SQLite の送信待ち outbox。enqueue は外部送信しない。flush が送信直前に期限（min(expires_at, 07:15)）・STOP・
  台帳状態・月予算（既定 200 通）を再確認し、成功確認後にだけ outbox SENT と台帳 notice_state=SENT にする。
  失敗（PENDING）・成否不明（UNKNOWN）では予約を解放しない。同一 retry_key で再試行する。
- handle_webhook: 生 body の HMAC-SHA256（channel secret）を JSON 解析より先に検証し、許可 userId の 1 対 1 トークだけを受け付け、
  webhookEventId を一意管理（同 ID 同内容 = DUPLICATE、同 ID 異内容 = CONFLICT）し、ボタン報告を Ledger.report へ写像する。
  STOP は新規候補だけを止め、RESUME は親が確認した reconciled_at（STOP 以後・now 以前）がなければ RECONCILIATION_REQUIRED。

本ループの transport は 'stub' だけ。'line' は実送信を伴うため呼ばない（実 LINE・実口座・実 LLM は使わない）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import string
import uuid
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .ledger import LedgerError
from .models import JST, TradeEvent

KINDS = ('NEW', 'EXIT', 'RECONCILE', 'RISK')
CANDIDATE_KINDS = ('NEW', 'EXIT')
STATUS_TEXT = {'NO_SIGNAL': '本日サインなし', 'REVIEW_INCOMPLETE': '審査未完了',
               'REJECTED': '候補はあるが審査で不承認', 'DATA_MISSING': 'データ不足', 'ERROR': '障害'}
BUTTONS = (('発注した', 'ORDERED'), ('一部約定', 'PARTIAL'), ('全部約定', 'FILLED'), ('取消した', 'CANCELLED'), ('見送り', 'SKIPPED'))
ACTIONS = {label: action for label, action in BUTTONS}
REPORT_ACTIONS = tuple(action for _, action in BUTTONS)
CONTROL_ACTIONS = ('STOP', 'RESUME')
NOTIFY_CUTOFF = time(7, 15)
DEFAULT_BUDGET = 200
RETRY_WINDOW = timedelta(hours=24)          # LINE の retry key 有効期間（公式 24 時間）
STUB_RESULTS = ('success', 'failure', 'timeout')
OUTBOX_STATES = ('PENDING', 'SENDING', 'UNKNOWN', 'SENT', 'EXPIRED')
STALE_CLAIM = timedelta(minutes=10)          # 別プロセスの SENDING claim をクラッシュとみなすまでの猶予
_CODE = re.compile(r'^[0-9A-Z]{4,5}$')


# ---- 文面 ---------------------------------------------------------------------

def _yen(value: Any) -> str:
    return f'{int(round(float(value))):,}'


def _jst(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('タイムゾーン付き datetime が必要です')
    return value.astimezone(JST)


def broker_link(template: Any, code: Any) -> str:
    """楽天証券リンク。{code} だけを展開し、https・正規ドメイン・userinfo なし・会員 URL 禁止を検証する。"""
    if not isinstance(template, str) or not template.strip():
        raise ValueError('broker.link_template が未設定です（候補カードは作れません）')
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        raise ValueError(f'銘柄コードが不正: {code!r}')
    fields = [name for _, name, _, _ in string.Formatter().parse(template) if name is not None]
    if fields != ['code']:
        raise ValueError('link_template の置換は {code} だけを許可します')
    url = template.replace('{code}', code)
    parts = urlsplit(url)
    if parts.scheme != 'https':
        raise ValueError('リンクは https のみ')
    if '@' in parts.netloc or parts.username is not None or parts.password is not None:
        raise ValueError('リンクに userinfo は許可されない')
    host = (parts.hostname or '').lower().rstrip('.')
    if not host.isascii():
        raise ValueError('リンク先ホストに非 ASCII 文字（IDN）は許可しない（ブラウザの ASCII 化で会員ホスト等に正規化され得る）')
    if not (host == 'rakuten-sec.co.jp' or host.endswith('.rakuten-sec.co.jp')):
        raise ValueError('リンク先は rakuten-sec.co.jp とその正規サブドメインのみ')
    if host == 'member.rakuten-sec.co.jp' or host.startswith('member.'):
        raise ValueError('会員ページ（member.）への直接リンクは禁止')
    return url


def _text_block(text: str, **style) -> dict:
    return {'type': 'text', 'text': text, 'wrap': True, **style}


def _verdict_lines(verdicts) -> list[str]:
    lines = []
    for v in verdicts:
        judge = getattr(v, 'judge', None) or (v.get('judge') if isinstance(v, dict) else None)
        decision = getattr(v, 'decision', None) or (v.get('decision') if isinstance(v, dict) else None)
        reason = getattr(v, 'reason', None) or (v.get('reason') if isinstance(v, dict) else '')
        risks = getattr(v, 'risks', None) or (v.get('risks') if isinstance(v, dict) else ())
        lines.append(f'{judge}: {decision} 「{reason}」')
        if risks:
            lines.append(f'  リスク: {", ".join(risks)}')
    return lines


def _candidate_card(kind: str, p: Any, verdicts, context: dict, settings: dict) -> dict:
    if p is None:
        raise ValueError(f'{kind} には proposal が必要です')
    get = (lambda k: p.get(k)) if isinstance(p, dict) else (lambda k: getattr(p, k))
    code, side, qty = str(get('code')), get('side'), int(get('qty'))
    if kind == 'NEW' and side != 'BUY' or kind == 'EXIT' and side != 'SELL':
        raise ValueError(f'{kind} の売買方向が不正: {side}')
    if get('account_type') != 'CASH':
        raise ValueError('現物（CASH）以外の候補は通知できません')
    link = broker_link((settings.get('broker') or {}).get('link_template'), code)
    expires = _jst(get('expires_at'))
    exec_label = '寄付指値' if get('exec_condition') == 'OPENING_LIMIT' else str(get('exec_condition'))
    side_label = '買い' if side == 'BUY' else '売り'
    title = f'【{"買い候補" if kind == "NEW" else "手仕舞い候補"} #{get("proposal_id")}】{code} {context.get("name", "")}（現物）'
    confirmed = context.get('account_confirmed_at')
    body = [
        f'{side_label}: {qty:,}株 / {exec_label} {_yen(get("limit_price"))}円 / 有効期限: {expires:%m/%d %H:%M}（JST）',
        f'戦略: {get("strategy")} {get("strategy_version")}',
        *_verdict_lines(verdicts),
        f'本日の新規候補: {context.get("new_sent_today", "?")}/{context.get("max_new_per_day", "?")} 件',
        f'口座確認時刻: {_jst(confirmed):%Y-%m-%d %H:%M}' if confirmed is not None else '口座確認時刻: 未確認',
        f'予約後の余力目安: {_yen(context["available_after"])}円' if context.get('available_after') is not None else '予約後の余力目安: 不明',
        '※ 発注前に楽天証券側で余力・保有・未約定注文を確認してください。発注は 7:40 以降（7:30〜7:40 は注文不可）',
    ]
    buttons = [{'type': 'button', 'style': 'link', 'action': {'type': 'uri', 'label': '楽天証券で銘柄を開く', 'uri': link}}]
    for label, action in BUTTONS:
        buttons.append({'type': 'button', 'style': 'secondary', 'action': {
            'type': 'postback', 'label': label, 'displayText': label,
            'data': json.dumps({'action': action, 'proposal_id': get('proposal_id')}, ensure_ascii=False)}})
    return {
        'type': 'flex',
        'altText': f'{title} {side_label} {qty:,}株 {exec_label} {_yen(get("limit_price"))}円 期限 {expires:%H:%M}',
        'contents': {
            'type': 'bubble',
            'header': {'type': 'box', 'layout': 'vertical', 'contents': [_text_block(title, weight='bold')]},
            'body': {'type': 'box', 'layout': 'vertical', 'contents': [_text_block(line) for line in body]},
            'footer': {'type': 'box', 'layout': 'vertical', 'contents': buttons},
        },
        'kind': kind, 'proposal_id': get('proposal_id'), 'code': code,
    }


def render_message(*, kind: str, proposal=None, verdicts=(), context: dict, settings: dict) -> dict:
    if kind not in KINDS:
        raise ValueError(f'kind は {KINDS} のいずれか')
    if not isinstance(context, dict) or not isinstance(settings, dict):
        raise ValueError('context / settings は辞書')
    if kind in CANDIDATE_KINDS:
        card = _candidate_card(kind, proposal, verdicts, context, settings)
        dumped = json.dumps(card, ensure_ascii=False)
        if 'confidence' in dumped:
            raise ValueError('通知に confidence を含めてはいけません')
        return card
    status = context.get('status')
    parts = []
    if status is not None:
        if status not in STATUS_TEXT:
            raise ValueError(f'不明な status: {status}')
        parts.append(STATUS_TEXT[status])
    if context.get('text'):
        parts.append(str(context['text']))
    if not parts:
        raise ValueError(f'{kind} には text または status が必要です')
    prefix = '【照合・状態】' if kind == 'RECONCILE' else '【リスク警告】'
    return {'type': 'text', 'text': prefix + ' '.join(parts), 'kind': kind, 'status': status}


# ---- outbox --------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox(
  key TEXT PRIMARY KEY, kind TEXT NOT NULL, proposal_id TEXT, message TEXT NOT NULL, content_hash TEXT NOT NULL,
  recipient TEXT NOT NULL, retry_key TEXT NOT NULL, state TEXT NOT NULL, month TEXT, expires_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, attempts TEXT NOT NULL DEFAULT '[]', seq INTEGER NOT NULL,
  claimed_by TEXT, claimed_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS outbox_candidate ON outbox(kind, proposal_id) WHERE proposal_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS kv(name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS webhook_events(
  event_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS controls(seq INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, at TEXT NOT NULL,
  event_id TEXT, reconciled_at TEXT, event_at TEXT);
"""


def _month(now: datetime) -> str:
    return f'{_jst(now):%Y-%m}'


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


class Notifier:
    def __init__(self, *, ledger, state_path: Path, settings: dict, transport: str = 'stub',
                 stub_results: list[str] | None = None, stop_check=None):
        if stop_check is not None and not callable(stop_check):
            raise ValueError('stop_check は callable または None')
        self.stop_check = stop_check
        if transport not in ('stub', 'line'):
            raise ValueError('transport は stub / line')
        if transport == 'line':
            raise ValueError('transport=line（実送信）は本ループでは無効です')
        if not isinstance(settings, dict):
            raise ValueError('settings は辞書')
        line = settings.get('line') or {}
        self.recipient = line.get('allowed_user_id')
        if not isinstance(self.recipient, str) or not self.recipient:
            raise ValueError('settings.line.allowed_user_id が必要です')
        budget = line.get('monthly_budget', DEFAULT_BUDGET)
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise ValueError('monthly_budget は 0 以上の整数')
        self.budget = budget
        self.ledger = ledger
        self.settings = settings
        self.transport = transport
        self.stub_results = list(stub_results or [])
        for r in self.stub_results:
            if r not in STUB_RESULTS:
                raise ValueError(f'stub_results は {STUB_RESULTS} のみ')
        self.path = Path(state_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.instance_id = f'{os.getpid()}:{uuid.uuid4().hex}'
        self._con = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
        self._con.execute('PRAGMA journal_mode=WAL')
        self._con.executescript(_SCHEMA)
        for table, column in (('outbox', 'claimed_by'), ('outbox', 'claimed_at'), ('controls', 'event_at')):
            if column not in [r[1] for r in self._con.execute(f'PRAGMA table_info({table})')]:
                self._con.execute(f'ALTER TABLE {table} ADD COLUMN {column} TEXT')

    def close(self) -> None:
        self._con.close()

    # -- 台帳参照 --
    def _notice(self, proposal_id: str) -> dict:
        try:
            return self.ledger.notice(proposal_id)
        except KeyError as exc:
            raise ValueError(f'台帳に候補 {proposal_id} がありません') from exc

    def _proposal(self, proposal_id: str) -> dict:
        try:
            return self.ledger.proposal(proposal_id)
        except KeyError as exc:
            raise ValueError(f'台帳に候補 {proposal_id} がありません') from exc

    # -- STOP 状態 --
    def _kv(self, name: str, default=None):
        row = self._con.execute('SELECT value FROM kv WHERE name=?', [name]).fetchone()
        return json.loads(row[0]) if row else default

    def _set_kv(self, name: str, value) -> None:
        self._con.execute('INSERT OR REPLACE INTO kv(name, value) VALUES(?,?)', [name, json.dumps(value, ensure_ascii=False)])

    def _stop_file(self) -> bool:
        stop_file = self.settings.get('stop_file')
        return bool(stop_file) and Path(stop_file).exists()

    def is_stopped(self) -> bool:
        additional = False
        if self.stop_check is not None:
            additional = self.stop_check()
            if type(additional) is not bool:
                raise ValueError('stop_check の戻り値は bool')
        return bool(self._kv('stopped', False)) or self._stop_file() or additional

    def _latest_control_event_at(self) -> datetime | None:
        rows = self._con.execute(
            'SELECT event_at FROM controls WHERE event_at IS NOT NULL').fetchall()
        return max((_jst(row[0]) for row in rows), default=None)

    def stop(self, *, now: datetime, event_id: str | None = None, event_at: datetime | None = None) -> str:
        """STOPPED。遅着（event_at がより新しい制御操作より前）の STOP は監査に残すだけで状態を巻き戻さない → IGNORED。"""
        now = _jst(now)
        event_at = _jst(event_at) if event_at is not None else now
        if event_at > now:
            return 'INVALID'
        latest = self._latest_control_event_at()
        self._con.execute('BEGIN IMMEDIATE')
        try:
            late = latest is not None and event_at < latest
            self._con.execute('INSERT INTO controls(action, at, event_id, event_at) VALUES(?,?,?,?)',
                              ['STOP' if not late else 'STOP_LATE', now.isoformat(), event_id, event_at.isoformat()])
            if not late:
                self._set_kv('stopped', True)
                self._set_kv('stopped_at', now.isoformat())
                self._set_kv('stopped_event_at', event_at.isoformat())
            self._con.execute('COMMIT')
        except BaseException:
            try:
                self._con.execute('ROLLBACK')
            except BaseException:
                pass
            raise
        return 'IGNORED' if late else 'STOPPED'

    def resume(self, *, now: datetime, reconciled_at: datetime | None, event_id: str | None = None,
               event_at: datetime | None = None) -> str:
        """RESUMED / RECONCILIATION_REQUIRED / IGNORED（遅着）。reconciled_at は親（呼出側）が確認した値だけを受ける。"""
        now = _jst(now)
        event_at = _jst(event_at) if event_at is not None else now
        if event_at > now:
            return 'INVALID'
        latest = self._latest_control_event_at()
        if latest is not None and event_at < latest:
            self._con.execute('INSERT INTO controls(action, at, event_id, event_at) VALUES(?,?,?,?)',
                              ['RESUME_LATE', now.isoformat(), event_id, event_at.isoformat()])
            return 'IGNORED'
        stopped_at = self._kv('stopped_at')
        if reconciled_at is None:
            return 'RECONCILIATION_REQUIRED'
        reconciled = _jst(reconciled_at)
        if reconciled > now or (stopped_at is not None and reconciled < datetime.fromisoformat(stopped_at)):
            return 'RECONCILIATION_REQUIRED'
        self._con.execute('BEGIN IMMEDIATE')
        try:
            self._set_kv('stopped', False)
            self._con.execute('INSERT INTO controls(action, at, event_id, reconciled_at, event_at) VALUES(?,?,?,?,?)',
                              ['RESUME', now.isoformat(), event_id, reconciled.isoformat(), event_at.isoformat()])
            self._con.execute('COMMIT')
        except BaseException:
            try:
                self._con.execute('ROLLBACK')
            except BaseException:
                pass
            raise
        return 'RESUMED'

    # -- 予算 --
    def _used(self, now: datetime) -> int:
        return self._con.execute("SELECT COUNT(*) FROM outbox WHERE month=? AND state IN ('SENT','UNKNOWN','SENDING')", [_month(now)]).fetchone()[0]

    # -- 公開 API --
    def get_entry(self, key: str) -> dict:
        """検証済みoutbox内容を返す。送信・状態更新・台帳反映は行わず、候補期限は参照照合する。"""
        if not isinstance(key, str) or not key.strip():
            raise ValueError('key は空でない文字列')
        columns = ('key', 'kind', 'proposal_id', 'message', 'content_hash',
                   'state', 'recipient', 'retry_key', 'month', 'expires_at',
                   'created_at', 'updated_at', 'attempts', 'claimed_at')
        row = self._con.execute(
            'SELECT ' + ', '.join(columns) + ' FROM outbox WHERE key=?', [key]).fetchone()
        if row is None:
            raise KeyError(key)
        entry = dict(zip(columns, row))
        self._validate_persisted_rows([entry])
        entry.pop('content_hash')
        entry.pop('expires_at')
        entry.pop('claimed_at')
        entry['message'] = json.loads(entry['message'])
        entry['attempts'] = json.loads(entry['attempts'])
        return entry

    def enqueue(self, *, key: str, kind: str, message: dict, proposal_id: str | None, now: datetime) -> dict:
        if not isinstance(key, str) or not key.strip():
            raise ValueError('key は空でない文字列')
        if kind not in KINDS:
            raise ValueError(f'kind は {KINDS} のいずれか')
        if not isinstance(message, dict) or not message.get('type'):
            raise ValueError('message は type を持つ辞書')
        now = _jst(now)
        expires_at = None
        if kind in CANDIDATE_KINDS:
            if not proposal_id:
                raise ValueError(f'{kind} には proposal_id が必要です')
            notice = self._notice(proposal_id)
            if notice['notice_state'] not in ('APPROVED', 'SENT'):
                raise ValueError(f'候補 {proposal_id} は APPROVED/SENT ではありません（{notice["notice_state"]}）')
            expires_at = _jst(self._proposal(proposal_id)['expires_at']).isoformat()
        elif proposal_id is not None:
            self._notice(proposal_id)
        content_hash = hashlib.sha256(_canonical([kind, proposal_id, message]).encode('utf-8')).hexdigest()
        self._con.execute('BEGIN IMMEDIATE')
        try:
            row = self._con.execute('SELECT content_hash, state, key FROM outbox WHERE key=?', [key]).fetchone()
            if row is None and proposal_id is not None and kind in CANDIDATE_KINDS:
                row = self._con.execute('SELECT content_hash, state, key FROM outbox WHERE kind=? AND proposal_id=?', [kind, proposal_id]).fetchone()
            if row is not None:
                if row[0] != content_hash and row[2] == key:
                    raise ValueError(f'同じ key {key} に異なる内容は登録できません')
                self._con.execute('COMMIT')
                result = {'key': row[2], 'state': row[1], 'duplicate': True}
                if row[0] != content_hash:
                    result['changed'] = True
                return result
            seq = self._con.execute('SELECT COALESCE(MAX(seq),0)+1 FROM outbox').fetchone()[0]
            self._con.execute(
                'INSERT INTO outbox(key, kind, proposal_id, message, content_hash, recipient, retry_key, state, month, expires_at, created_at, updated_at, attempts, seq) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                [key, kind, proposal_id, _canonical(message), content_hash, self.recipient, str(uuid.uuid4()), 'PENDING', None,
                 expires_at, now.isoformat(), now.isoformat(), '[]', seq])
            self._con.execute('COMMIT')
        except BaseException:
            try:
                self._con.execute('ROLLBACK')
            except BaseException:
                pass
            raise
        return {'key': key, 'state': 'PENDING', 'duplicate': False}

    def _deadline(self, row) -> datetime | None:
        if row['expires_at'] is None:
            return None
        expires = _jst(row['expires_at'])
        return min(expires, datetime.combine(expires.date(), NOTIFY_CUTOFF, tzinfo=JST))

    def _send_stub(self) -> str:
        return self.stub_results.pop(0) if self.stub_results else 'failure'

    def _daily_deadline(self, row) -> datetime | None:
        """候補以外（RECONCILE / RISK）は登録日の JST 終日で失効（古い日次通知を新月・翌日に一括配信しない）。"""
        created = _jst(row['created_at'])
        return datetime.combine(created.date(), time(23, 59, 59, 999999), tzinfo=JST)

    def _finish(self, key: str, *, state: str, month, attempts: list, now: datetime) -> bool:
        """自分の claim を持つ行だけを更新する（古い行スナップショットや他 claim による巻戻しを防ぐ）。"""
        return self._con.execute(
            "UPDATE outbox SET state=?, month=?, attempts=?, updated_at=?, claimed_by=NULL, claimed_at=NULL "
            "WHERE key=? AND state='SENDING' AND claimed_by=?",
            [state, month, json.dumps(attempts, ensure_ascii=False), now.isoformat(), key, self.instance_id]).rowcount == 1

    def _selected_keys(self, keys: list[str] | tuple[str, ...] | None) -> set[str] | None:
        """全キーを副作用前に検証する。None は全件、空集合は無動作。"""
        if keys is None:
            invalid = self._con.execute(
                'SELECT 1 FROM outbox WHERE state NOT IN (?,?,?,?,?) LIMIT 1',
                OUTBOX_STATES).fetchone()
            if invalid is not None:
                raise ValueError('保存済み通知キューの状態を確認できません')
            return None
        if not isinstance(keys, (list, tuple)) or any(
                not isinstance(key, str) or not key.strip() for key in keys):
            raise ValueError('keys は空でない文字列を要素とする list / tuple')
        # 終端状態のキーも存在すれば有効。不明キーは一件も処理する前に拒否する。
        for key in dict.fromkeys(keys):
            row = self._con.execute('SELECT state FROM outbox WHERE key=?', [key]).fetchone()
            if row is None:
                raise KeyError(key)
            if row[0] not in OUTBOX_STATES:
                raise ValueError('保存済み通知キューの状態を確認できません')
        return set(keys)

    def _validate_persisted_rows(self, rows: list[dict]) -> None:
        """Validate every selected durable row before any delivery side effect."""
        for row in rows:
            try:
                message = json.loads(row['message'])
                attempts = json.loads(row['attempts'])
                valid = (row['kind'] in KINDS
                         and row['state'] in OUTBOX_STATES
                         and row['recipient'] == self.recipient
                         and isinstance(row['retry_key'], str)
                         and bool(row['retry_key'].strip())
                         and isinstance(message, dict) and bool(message.get('type'))
                         and isinstance(attempts, list)
                         and all(isinstance(attempt, dict) for attempt in attempts)
                         and row['content_hash'] == hashlib.sha256(
                             _canonical([row['kind'], row['proposal_id'], message])
                             .encode('utf-8')).hexdigest())
                if not valid:
                    raise ValueError('invalid persisted row')
                _jst(row['created_at'])
                if row.get('expires_at') is not None:
                    _jst(row['expires_at'])
                if row.get('claimed_at') is not None:
                    _jst(row['claimed_at'])
                for attempt in attempts:
                    _jst(attempt.get('at'))
                    if (not isinstance(attempt.get('retry_key'), str)
                            or not attempt['retry_key'].strip()
                            or attempt['retry_key'] != row['retry_key']):
                        raise ValueError('invalid retry binding')
                if row['kind'] in CANDIDATE_KINDS:
                    if row.get('expires_at') is None:
                        raise ValueError('candidate expiry missing')
                    expected_expiry = _jst(
                        self._proposal(row['proposal_id'])['expires_at'])
                    if _jst(row['expires_at']) != expected_expiry:
                        raise ValueError('candidate expiry mismatch')
                elif row.get('expires_at') is not None:
                    raise ValueError('non-candidate expiry present')
            except (TypeError, ValueError, KeyError, RecursionError,
                    json.JSONDecodeError):
                valid = False
            if not valid:
                raise ValueError('保存済み通知キューの内容を確認できません')

    def _validate_operation_time(self, rows: list[dict], now: datetime) -> None:
        """Reject selected queue history observed after this operation time."""
        try:
            for row in rows:
                if _jst(row['created_at']) > now or _jst(row['updated_at']) > now:
                    raise ValueError('future queue history')
                attempts = json.loads(row['attempts'])
                if any(_jst(attempt['at']) > now for attempt in attempts):
                    raise ValueError('future attempt history')
        except (KeyError, TypeError, ValueError, RecursionError,
                json.JSONDecodeError):
            raise ValueError('通知履歴より前の時刻では処理できません') from None

    def flush(self, *, now: datetime, keys: list[str] | tuple[str, ...] | None = None) -> list[dict]:
        """選択した既存キーだけを処理する。None は全待機行、空選択は処理なし。"""
        now = _jst(now)
        selected = self._selected_keys(keys)
        if selected == set():
            return []
        results = []
        columns = [d[0] for d in self._con.execute('SELECT * FROM outbox LIMIT 0').description]
        rows = [dict(zip(columns, r)) for r in
                self._con.execute("SELECT * FROM outbox WHERE state IN ('PENDING','UNKNOWN','SENDING') ORDER BY seq").fetchall()]
        if selected is not None:
            rows = [row for row in rows if row['key'] in selected]
        self._validate_persisted_rows(rows)
        self._validate_operation_time(rows, now)
        for row in rows:
            key, retry_key, kind = row['key'], row['retry_key'], row['kind']
            attempts = json.loads(row['attempts'])
            if row['state'] == 'SENDING':
                # 別 flush が処理中か、送信中に落ちた行。猶予内は触らず、猶予超過はクラッシュとみなし成否不明（UNKNOWN）へ回復する
                claimed_at = datetime.fromisoformat(row['claimed_at']) if row['claimed_at'] else None
                if row['claimed_by'] == self.instance_id or claimed_at is None or now - claimed_at > STALE_CLAIM:
                    self._con.execute("UPDATE outbox SET state='UNKNOWN', updated_at=?, claimed_by=NULL, claimed_at=NULL WHERE key=? AND state='SENDING'",
                                      [now.isoformat(), key])
                    self._set_kv('alert:SEND_UNKNOWN:' + key, now.isoformat())
                    row['state'] = 'UNKNOWN'
                else:
                    continue
            was_unknown = row['state'] == 'UNKNOWN'
            outcome = None
            # 送信直前の再確認: 期限 → 台帳状態 → STOP → 再試行期限。UNKNOWN 行は「未送信」と断定せず UNKNOWN のまま（予算保持）
            if was_unknown and attempts and now - datetime.fromisoformat(attempts[0]['at']) > RETRY_WINDOW:
                outcome = 'UNKNOWN'                              # 再試行期限超過: 新キーを作らず照合待ち
                self._set_kv('alert:RETRY_EXPIRED:' + key, now.isoformat())
            # 日次通知（RECONCILE / RISK）の登録日失効は未送信行だけに適用。UNKNOWN 行は同一 retry_key の再試行を妨げない
            deadline = self._deadline(row) if kind in CANDIDATE_KINDS else (None if was_unknown else self._daily_deadline(row))
            if outcome is None and deadline is not None and now > deadline:
                outcome = 'EXPIRED'
            elif outcome is None and kind in CANDIDATE_KINDS and self._notice(row['proposal_id'])['notice_state'] not in ('APPROVED', 'SENT'):
                outcome = 'EXPIRED'
            if outcome is None and kind == 'NEW' and self.is_stopped():
                outcome = 'STOPPED'
            if outcome is not None:
                if was_unknown:
                    if outcome != 'UNKNOWN':
                        self._set_kv('alert:SEND_UNKNOWN:' + key, now.isoformat())
                    outcome = 'UNKNOWN'                          # 成否不明は期限切れ・STOP でも失効扱いにしない（照合待ち・予算保持）
                elif outcome == 'EXPIRED':
                    self._con.execute("UPDATE outbox SET state='EXPIRED', updated_at=? WHERE key=? AND state='PENDING'", [now.isoformat(), key])
                results.append({'key': key, 'state': outcome, 'retry_key': retry_key})
                continue
            # 予算枠の確保と claim を同一トランザクションで行う（UNKNOWN 行は既に月枠を保持している）
            self._con.execute('BEGIN IMMEDIATE')
            try:
                if not was_unknown and self._used(now) >= self.budget:
                    self._set_kv('alert:BUDGET_EXCEEDED:' + _month(now), now.isoformat())
                    self._con.execute('COMMIT')
                    results.append({'key': key, 'state': 'BUDGET_BLOCKED', 'retry_key': retry_key})
                    continue
                claim = self._con.execute(
                    "UPDATE outbox SET state='SENDING', month=COALESCE(month, ?), claimed_by=?, claimed_at=?, updated_at=? "
                    "WHERE key=? AND state IN ('PENDING','UNKNOWN')",
                    [_month(now), self.instance_id, now.isoformat(), now.isoformat(), key]).rowcount
                self._con.execute('COMMIT')
            except BaseException:
                try:
                    self._con.execute('ROLLBACK')
                except BaseException:
                    pass
                raise
            if claim != 1:
                continue                                        # 他の flush が処理中
            # UNKNOWN の再試行は初回の予約月を保持し、月境界でも枠を移さない。
            reserved_month = row['month'] or _month(now)
            try:
                result = self._send_stub() if self.transport == 'stub' else 'failure'
            except Exception:
                # 送信関数が例外で落ちた: 成否を断定せず UNKNOWN（同一 retry_key・予算保持・警告）にしてから例外を伝える
                attempts.append({'at': now.isoformat(), 'result': 'exception', 'retry_key': retry_key})
                self._finish(key, state='UNKNOWN', month=reserved_month, attempts=attempts, now=now)
                self._set_kv('alert:SEND_UNKNOWN:' + key, now.isoformat())
                raise
            attempts.append({'at': now.isoformat(), 'result': result, 'retry_key': retry_key})
            if result == 'success':
                state = 'SENT'
            elif result == 'timeout' or was_unknown:
                state = 'UNKNOWN'                                # 過去に成否不明があれば、後続の失敗でも未送信とは断定しない
                self._set_kv('alert:SEND_UNKNOWN:' + key, now.isoformat())
            else:
                state = 'PENDING'
            month = reserved_month if state in ('SENT', 'UNKNOWN') else None
            if not self._finish(key, state=state, month=month, attempts=attempts, now=now):
                continue                                        # 自分の claim が失われている（巻戻し防止）
            if state == 'SENT' and kind in CANDIDATE_KINDS:
                # 成功の耐久記録の後に台帳へ反映（順序: 予約→enqueue→送信→成功確認→SENT）。既に SENT なら再適用しない
                if self._notice(row['proposal_id'])['notice_state'] == 'APPROVED':
                    self.ledger.set_notice_state(row['proposal_id'], 'SENT', now)
            results.append({'key': key, 'state': state, 'retry_key': retry_key})
        return results

    def reconcile_sent(self, *, now: datetime,
                       keys: list[str] | tuple[str, ...] | None = None) -> list[str]:
        """選択した耐久SENTを台帳へ再適用する。None は全件、空選択は無動作。

        台帳へ記録するnowは修復時刻であり、初回送信成功時刻の復元ではない。
        送信・キュー状態更新は行わず、選択外の台帳には触れない。
        """
        now = _jst(now)
        selected = self._selected_keys(keys)
        if selected == set():
            return []
        columns = [d[0] for d in self._con.execute('SELECT * FROM outbox LIMIT 0').description]
        rows = [dict(zip(columns, row)) for row in self._con.execute(
            "SELECT * FROM outbox WHERE state='SENT' AND proposal_id IS NOT NULL ORDER BY seq")]
        if selected is not None:
            rows = [row for row in rows if row['key'] in selected]
        self._validate_persisted_rows(rows)
        self._validate_operation_time(rows, now)
        fixed = []
        # Discover missing ledger references before repairing the first row.
        # Re-read in the write loop: several selected keys can share a notice.
        for row in rows:
            self._notice(row['proposal_id'])['notice_state']
        for row in rows:
            key, pid = row['key'], row['proposal_id']
            if self._notice(pid)['notice_state'] == 'APPROVED':
                self.ledger.set_notice_state(pid, 'SENT', now)
                fixed.append(key)
        return fixed

    def status(self, *, now: datetime) -> dict:
        now = _jst(now)
        month = _month(now)
        alerts = []
        for (name,) in self._con.execute("SELECT name FROM kv WHERE name LIKE 'alert:%'"):
            _, code, target = name.split(':', 2)
            if code == 'BUDGET_EXCEEDED' and target != month:
                continue
            if code not in alerts:
                alerts.append(code)
        return {'monthly_used': self._used(now), 'stopped': self.is_stopped(), 'alerts': alerts,
                'budget': self.budget, 'month': month}

    # -- webhook の記録 --
    def _event_seen(self, event_id: str) -> tuple[str, str] | None:
        row = self._con.execute('SELECT payload_hash, result FROM webhook_events WHERE event_id=?', [event_id]).fetchone()
        return (row[0], row[1]) if row else None

    def _record_event(self, event_id: str, payload_hash: str, result: dict, now: datetime) -> None:
        self._con.execute('INSERT OR REPLACE INTO webhook_events(event_id, payload_hash, result, received_at) VALUES(?,?,?,?)',
                          [event_id, payload_hash, _canonical(result), _jst(now).isoformat()])


# ---- webhook -------------------------------------------------------------------

def verify_signature(body: bytes, signature: str, secret: str | None) -> bool:
    if not secret or not isinstance(signature, str) or not signature or not signature.isascii():
        return False
    expected = base64.b64encode(hmac.new(secret.encode('utf-8'), body, hashlib.sha256).digest()).decode('ascii')
    return hmac.compare_digest(expected, signature)


def _business_payload(event: dict) -> dict:
    """同一性判定に使う業務 payload（配送メタデータ deliveryContext 等は除く）。"""
    message = event.get('message')
    return {'type': event.get('type'), 'source': event.get('source'), 'timestamp': event.get('timestamp'),
            'postback': event.get('postback'),
            'message': {k: v for k, v in message.items() if k != 'id'} if isinstance(message, dict) else message}


def _event_time(event: dict, data: dict) -> datetime:
    if data.get('at') is not None:
        return _jst(data['at'])
    ts = event.get('timestamp')
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise ValueError('timestamp が不正')
    try:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(JST)
    except (OverflowError, OSError) as exc:
        raise ValueError('timestamp が日時の範囲外です') from exc


def _number(value: Any, label: str, integer: bool = False):
    if value is None or isinstance(value, bool):
        raise KeyError(label)
    if integer:
        if not isinstance(value, int):
            raise ValueError(f'{label} は整数')
        return value
    if not isinstance(value, (int, float)):
        raise ValueError(f'{label} は数値')
    return float(value)


def _handle_event(notifier: Notifier, event: dict, now: datetime, reconciled_at: datetime | None) -> dict:
    event_id = event.get('webhookEventId')
    if not isinstance(event_id, str) or not event_id.strip():
        return {'event_id': None, 'status': 'INVALID', 'detail': 'webhookEventId が必要です'}
    source = event.get('source') if isinstance(event.get('source'), dict) else {}
    if source.get('type') != 'user' or source.get('userId') != notifier.recipient or 'groupId' in source or 'roomId' in source:
        return {'event_id': event_id, 'status': 'FORBIDDEN'}
    payload_hash = hashlib.sha256(_canonical(_business_payload(event)).encode('utf-8')).hexdigest()
    seen = notifier._event_seen(event_id)
    if seen is not None:
        return {'event_id': event_id, 'status': 'DUPLICATE' if seen[0] == payload_hash else 'CONFLICT'}
    result = _apply_event(notifier, event, event_id, now, reconciled_at)
    notifier._record_event(event_id, payload_hash, result, now)
    return result


def _apply_event(notifier: Notifier, event: dict, event_id: str, now: datetime, reconciled_at) -> dict:
    now = _jst(now)
    action, data = None, {}
    for field in ('postback', 'message'):
        if field in event and not isinstance(event[field], dict):
            return {'event_id': event_id, 'status': 'INVALID', 'detail': f'{field} はオブジェクトが必要'}
    if event.get('type') == 'postback':
        postback = event.get('postback')
        if not isinstance(postback, dict) or not isinstance(postback.get('data'), str):
            return {'event_id': event_id, 'status': 'INVALID', 'detail': 'postback はオブジェクトで data は文字列'}
        try:
            data = json.loads(postback['data'])
        except (ValueError, TypeError):
            return {'event_id': event_id, 'status': 'INVALID', 'detail': 'postback.data が JSON ではない'}
        if not isinstance(data, dict):
            return {'event_id': event_id, 'status': 'INVALID', 'detail': 'postback.data はオブジェクト'}
        action = data.get('action')
    elif event.get('type') == 'message':
        message = event.get('message')
        if not isinstance(message, dict):
            return {'event_id': event_id, 'status': 'INVALID', 'detail': 'message はオブジェクト'}
        if message.get('type') == 'text' and message.get('text') in CONTROL_ACTIONS:
            action = message['text']
        else:
            return {'event_id': event_id, 'status': 'INVALID', 'detail': '対応しないメッセージ'}
    else:
        return {'event_id': event_id, 'status': 'INVALID', 'detail': f'対応しない event type: {event.get("type")}'}

    if action in CONTROL_ACTIONS:
        try:
            event_at = _event_time(event, {})
        except ValueError as exc:
            return {'event_id': event_id, 'status': 'INVALID', 'detail': str(exc)}
        if event_at > now:
            return {'event_id': event_id, 'status': 'INVALID', 'detail': '未来の時刻の制御操作は受け付けない'}
        if action == 'STOP':
            return {'event_id': event_id, 'status': notifier.stop(now=now, event_id=event_id, event_at=event_at)}
        return {'event_id': event_id, 'status': notifier.resume(now=now, reconciled_at=reconciled_at, event_id=event_id, event_at=event_at)}
    if action not in REPORT_ACTIONS:
        return {'event_id': event_id, 'status': 'INVALID', 'detail': f'不明な action: {action!r}'}

    proposal_id = data.get('proposal_id')
    if not isinstance(proposal_id, str) or not proposal_id:
        return {'event_id': event_id, 'status': 'INVALID', 'detail': 'proposal_id が必要です'}
    try:
        notifier.ledger.notice(proposal_id)
    except KeyError:
        return {'event_id': event_id, 'status': 'UNKNOWN_PROPOSAL'}
    try:
        at = _event_time(event, data)
    except ValueError as exc:
        return {'event_id': event_id, 'status': 'INVALID', 'detail': str(exc)}
    if at > now:
        return {'event_id': event_id, 'status': 'INVALID', 'detail': '未来の時刻は受け付けない'}
    try:
        if action in ('PARTIAL', 'FILLED'):
            qty = _number(data.get('qty'), 'qty', integer=True)
            price = _number(data.get('price'), 'price')
            fee = _number(data.get('fee'), 'fee')
        else:
            qty, price, fee = 0, 0.0, 0.0
    except KeyError as exc:
        return {'event_id': event_id, 'status': 'NEEDS_DETAILS',
                'detail': f'{action} には実約定の qty / price / fee が必要です（不足: {exc.args[0]}）。同じ候補 ID で新しい報告を送ってください'}
    except ValueError as exc:
        return {'event_id': event_id, 'status': 'INVALID', 'detail': str(exc)}
    broker = data.get('broker_order_id')
    trade = TradeEvent(event_id='line:' + event_id, proposal_id=proposal_id, kind=action, qty=qty, price=price,
                       fee=fee, at=at, source='line', broker_order_id=broker if isinstance(broker, str) else None)
    try:
        res = notifier.ledger.report(trade)
    except LedgerError as exc:
        return {'event_id': event_id, 'status': 'INVALID', 'detail': str(exc)}
    if res.applied:
        status = 'APPLIED'
    elif getattr(res, 'pending', False):
        status = 'PENDING'
    elif res.error:
        status = 'INVALID'
    else:
        status = 'IGNORED'
    confirmation = f'{proposal_id}: {action} を記録（{at:%m/%d %H:%M} JST'
    confirmation += f'、{qty}株 @ {price:g}円 手数料 {fee:g}円）' if action in ('PARTIAL', 'FILLED') else '）'
    return {'event_id': event_id, 'status': status, 'trade_state': res.trade_state, 'detail': res.error or res.ignored_reason,
            'confirmation': confirmation}


def handle_webhook(*, notifier: Notifier, body: bytes, signature: str, now: datetime,
                   reconciled_at: datetime | None = None) -> dict:
    if not isinstance(body, (bytes, bytearray)):
        raise ValueError('body は生バイト列が必要です')
    if not verify_signature(bytes(body), signature, os.environ.get('LINE_CHANNEL_SECRET')):
        return {'http_status': 401, 'results': []}
    try:
        payload = json.loads(bytes(body).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return {'http_status': 400, 'results': []}
    events = payload.get('events') if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return {'http_status': 400, 'results': []}
    if not events:
        return {'http_status': 200, 'results': []}
    results = []
    for event in events:
        if not isinstance(event, dict):
            results.append({'event_id': None, 'status': 'INVALID', 'detail': 'event はオブジェクト'})
            continue
        results.append(_handle_event(notifier, event, now, reconciled_at))
    return {'http_status': 200, 'results': results}
