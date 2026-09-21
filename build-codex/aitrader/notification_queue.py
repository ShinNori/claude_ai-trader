"""Enqueue previously fixed mock plans. Never flush or create reservations."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier
from .notification_plan import (_inside, _read, _prepare_locked, _validate_run_time,
                                _record_notification_time, _notification_home_guard)
from .runner import RunError, aware, write_json
from .managed_stop import prepare_managed_operation, advance_managed_operation


def _matches(entry, card, recipient):
    return (entry['key'] == card['key'] and entry['kind'] == card['kind']
            and entry['proposal_id'] == card['proposal_id']
            and entry['message'] == card['message'] and entry['recipient'] == recipient)


def enqueue_prepared_notifications(home, run_id, execution_day, *, now, contexts,
                                   settings, include_status=False):
    """Revalidate fixed cards and enqueue only; repeated calls reuse their keys.

    All supplied rendering/recipient settings must have been fixed at preparation.
    A partial enqueue is recovered with the same inputs; no cross-DB atomicity is
    claimed. The receipt is a queue observation, never evidence of delivery.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise RunError('run_idが不正です')
    if not isinstance(execution_day, date) or isinstance(execution_day, datetime):
        raise RunError('執行日はdateで指定してください')
    home = _notification_home_guard(home)
    _inside(home, home)
    now = aware(now)
    if now.date() != execution_day:
        raise RunError('通知キュー登録は執行日当日に限定します')
    raw_folder = home/'runs'/execution_day.isoformat()/run_id
    folder = _inside(home, raw_folder)
    plan_path = _inside(home, folder/'notification_plan.json')
    binding_path = _inside(home, home/'notification-plans.sqlite')
    queue_path = _inside(home, home/'notification.sqlite')
    raw_receipt_path = raw_folder/'notification_queue.json'
    receipt_path = _inside(home, raw_receipt_path)
    if not plan_path.is_file() or not binding_path.is_file():
        raise RunError('先に通知計画を固定してください')
    with closing(sqlite3.connect(_inside(home, home/'runner-lock.sqlite'),
                                isolation_level=None, timeout=5)) as lock:
        lock.execute('BEGIN IMMEDIATE')
        with closing(sqlite3.connect(binding_path.as_uri()+'?mode=ro', uri=True)) as db:
            original = db.execute('SELECT body FROM plans WHERE run_id=?', [run_id]).fetchone()
        if not original or json.loads(original[0]) != _read(plan_path):
            raise RunError('固定済み通知計画の原本が一致しません')
        prepared = _prepare_locked(home, run_id, execution_day, contexts=contexts,
                                   settings=settings, include_status=include_status)
        _validate_run_time(home, run_id, execution_day, now)
        if receipt_path.is_file() and aware(_read(receipt_path).get('observed_at')) > now:
            raise RunError('前回の通知観測より前の時刻では再登録できません')
        policy = prepare_managed_operation(home, now=now, settings=settings)
        with closing(Ledger(_inside(home, home/'ledger.sqlite'))) as ledger:
            with closing(Notifier(ledger=ledger, state_path=queue_path, settings=settings,
                                  transport='stub', stub_results=[])) as notifier:
                # Preflight every known key before allowing the first insertion.
                for card in prepared['plans']:
                    try:
                        entry = notifier.get_entry(card['key'])
                    except KeyError:
                        continue
                    except ValueError:
                        raise RunError('既存通知キューの内容を確認できません') from None
                    if not _matches(entry, card, notifier.recipient):
                        raise RunError('既存通知キューと固定計画が一致しません')
                    if (aware(entry['created_at']) > now or aware(entry['updated_at']) > now
                            or any(aware(attempt['at']) > now for attempt in entry['attempts'])):
                        raise RunError('通知履歴より前の時刻では再登録できません')
                advance_managed_operation(home, policy, now=now)
                _record_notification_time(home, run_id, execution_day, now)
                entries = []
                for card in prepared['plans']:
                    result = notifier.enqueue(key=card['key'], kind=card['kind'],
                                              message=card['message'],
                                              proposal_id=card['proposal_id'], now=now)
                    entry = notifier.get_entry(result['key'])
                    if not _matches(entry, card, notifier.recipient):
                        raise RunError('同一候補の別キーまたは異なる通知内容を検出しました')
                    entries.append(result)
        prior_attempt = any(item['state'] in ('SENT', 'UNKNOWN', 'SENDING') for item in entries)
        receipt = dict(mode='mock',
                       delivery='PRIOR_ATTEMPT_EXISTS' if prior_attempt else 'NOT_SENT',
                       action='ENQUEUE_ONLY', sent_by_this_call=False, transport='stub',
                       run_id=run_id, execution_day=execution_day.isoformat(),
                       plan_hash=prepared['plan_hash'], observed_at=now.isoformat(), entries=entries)
        _inside(home, raw_receipt_path)
        write_json(raw_receipt_path, receipt, redact=False)
        return receipt
