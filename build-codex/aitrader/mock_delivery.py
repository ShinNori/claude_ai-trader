"""Explicit, run-scoped simulation of delivery for an already prepared queue."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime, time
from pathlib import Path

from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import Notifier
from .notification_plan import (_inside, _read, _prepare_locked, _validate_run_time,
                                _record_notification_time, _notification_home_guard)
from .notification_queue import _matches
from .runner import RunError, aware, write_json
from .managed_stop import (prepare_managed_operation, advance_managed_operation,
                           managed_file_check)


@contextmanager
def _verified_queue(home, run_id, execution_day, *, now, contexts, settings,
                    include_status=False, stub_results=()):
    """Hold the runner lock and validate all existing rows before an operation."""
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise RunError('run_idが不正です')
    if not isinstance(execution_day, date) or isinstance(execution_day, datetime):
        raise RunError('執行日はdateで指定してください')
    home = _notification_home_guard(home)
    _inside(home, home)
    if not isinstance(contexts, dict) or not isinstance(settings, dict):
        raise RunError('contextsとsettingsを明示した辞書が必要です')
    if not isinstance(include_status, bool):
        raise RunError('include_statusはboolで指定してください')
    if not isinstance(stub_results, (list, tuple)) or any(
            value not in ('success', 'timeout', 'failure') for value in stub_results):
        raise RunError('模擬結果列を明示してください')
    now = aware(now)
    if now.date() != execution_day:
        raise RunError('模擬配信は執行日当日に限定します')
    folder = _inside(home, home/'runs'/execution_day.isoformat()/run_id)
    plan_path = _inside(home, folder/'notification_plan.json')
    binding_path = _inside(home, home/'notification-plans.sqlite')
    queue_path = _inside(home, home/'notification.sqlite')
    receipt_path = _inside(home, folder/'notification_queue.json')
    stop_path = _inside(home, home/'STOP')
    if not all(path.is_file() for path in (plan_path, binding_path, queue_path, receipt_path)):
        raise RunError('固定計画と通知キュー登録の完了が必要です')
    with closing(sqlite3.connect(_inside(home, home/'runner-lock.sqlite'),
                                isolation_level=None, timeout=5)) as lock:
        lock.execute('BEGIN IMMEDIATE')
        with closing(sqlite3.connect(binding_path.as_uri()+'?mode=ro', uri=True)) as db:
            original = db.execute('SELECT body FROM plans WHERE run_id=?', [run_id]).fetchone()
        if not original or json.loads(original[0]) != _read(plan_path):
            raise RunError('固定済み通知計画の原本が一致しません')
        prepared = _prepare_locked(home, run_id, execution_day, contexts=contexts,
                                   settings=settings, include_status=include_status,
                                   allow_sent=True)
        _validate_run_time(home, run_id, execution_day, now)
        receipt = _read(receipt_path)
        keys = [card['key'] for card in prepared['plans']]
        if (not isinstance(receipt, dict)
                or receipt.get('mode') != 'mock' or receipt.get('transport') != 'stub'
                or receipt.get('action') != 'ENQUEUE_ONLY'
                or receipt.get('sent_by_this_call') is not False
                or receipt.get('run_id') != run_id
                or receipt.get('execution_day') != execution_day.isoformat()
                or receipt.get('plan_hash') != prepared['plan_hash']
                or not isinstance(receipt.get('entries'), list)
                or any(not isinstance(entry, dict) for entry in receipt['entries'])
                or [entry.get('key') for entry in receipt['entries']] != keys):
            raise RunError('通知キュー登録記録と固定計画が一致しません')
        if aware(receipt.get('observed_at')) > now:
            raise RunError('通知登録より前の時刻では模擬配信できません')
        policy = prepare_managed_operation(home, now=now, settings=settings)
        stop_check = (managed_file_check(home, policy, now=now) if policy is not None
                      else lambda: stop_path.exists())
        with closing(Ledger(_inside(home, home/'ledger.sqlite'))) as ledger:
            with closing(Notifier(ledger=ledger, state_path=queue_path, settings=settings,
                                  transport='stub', stub_results=list(stub_results),
                                  stop_check=stop_check)) as notifier:
                before = {}
                for card in prepared['plans']:
                    try:
                        entry = notifier.get_entry(card['key'])
                    except KeyError as exc:
                        raise RunError('登録済み通知が欠損しています') from exc
                    except ValueError:
                        raise RunError('登録済み通知の内容を確認できません') from None
                    if not _matches(entry, card, notifier.recipient):
                        raise RunError('通知キューと固定計画が一致しません')
                    if (aware(entry['created_at']) > now or aware(entry['updated_at']) > now
                            or any(aware(attempt['at']) > now for attempt in entry['attempts'])):
                        raise RunError('通知履歴より前の時刻では模擬配信できません')
                    if (card['proposal_id'] is not None
                            and ledger.notice(card['proposal_id'])['notice_state'] == 'SENT'
                            and entry['state'] != 'SENT'):
                        raise RunError('台帳の送信記録と通知キューが一致しません')
                    before[card['key']] = entry
                yield home, folder, now, prepared, notifier, before, stop_path, policy


def _save_report(home, output, report):
    _inside(home, output)
    write_json(output, report, redact=False)
    return report


def deliver_prepared_mock(home, run_id, execution_day, *, now, contexts,
                          settings, stub_results, include_status=False):
    """Simulate verified existing run keys; never enqueue or reserve again."""
    with _verified_queue(home, run_id, execution_day, now=now, contexts=contexts,
                         settings=settings, include_status=include_status,
                         stub_results=stub_results) as verified:
        home, folder, now, prepared, notifier, before, stop_path, policy = verified
        output = home/'runs'/execution_day.isoformat()/run_id/'mock_delivery.json'
        _inside(home, output)
        keys = [card['key'] for card in prepared['plans']]
        selected, skipped = [], []
        for card in prepared['plans']:
            entry = before[card['key']]
            if entry['state'] == 'SENT':
                observation = 'ALREADY_SENT'
            elif card['kind'] in ('NEW', 'EXIT') and now.time() >= time(7, 15):
                observation = 'SKIPPED_DEADLINE'
            elif card['kind'] == 'NEW' and (stop_path.exists() or notifier.is_stopped()):
                observation = 'STOPPED'
            else:
                selected.append(card['key'])
                continue
            skipped.append(dict(key=card['key'], state=observation,
                                retry_key=entry['retry_key']))
        advance_managed_operation(home, policy, now=now)
        _record_notification_time(home, run_id, execution_day, now)
        results = notifier.flush(now=now, keys=selected) + skipped
        observations = {item['key']: item['state'] for item in results}
        entries = [dict(notifier.get_entry(key),
                        observation=observations.get(key, 'UNCHANGED')) for key in keys]
        attempted = any(len(entry['attempts']) > len(before[entry['key']]['attempts'])
                        for entry in entries)
        succeeded = any(entry['state'] == 'SENT' and before[entry['key']]['state'] != 'SENT'
                        for entry in entries)
        status = notifier.status(now=now)
        status['stopped'] = status['stopped'] or stop_path.exists()
        report = dict(mode='mock', transport='stub', action='MOCK_DELIVERY',
                      delivery='SIMULATION_ONLY', simulation=True,
                      warning='模擬配信の記録です。実LINE送信・注文の実績ではありません。',
                      run_id=run_id, execution_day=execution_day.isoformat(),
                      plan_hash=prepared['plan_hash'], observed_at=now.isoformat(),
                      attempted_by_this_call=attempted,
                      simulated_sent_by_this_call=succeeded, real_sent_by_this_call=False,
                      entries=entries, results=results, status=status)
        return _save_report(home, output, report)


def reconcile_prepared_mock(home, run_id, execution_day, *, now, contexts,
                            settings, include_status=False):
    """Repair this run's ledger from durable simulated SENT without any send.

    The ledger receives repair time, not a reconstructed original success time.
    A report failure can be retried from durable queue and ledger state.
    """
    with _verified_queue(home, run_id, execution_day, now=now, contexts=contexts,
                         settings=settings, include_status=include_status) as verified:
        home, folder, now, prepared, notifier, before, stop_path, policy = verified
        output = home/'runs'/execution_day.isoformat()/run_id/'mock_reconciliation.json'
        _inside(home, output)
        keys = [card['key'] for card in prepared['plans']]
        advance_managed_operation(home, policy, now=now)
        _record_notification_time(home, run_id, execution_day, now)
        repaired = notifier.reconcile_sent(now=now, keys=keys)
        entries = [notifier.get_entry(key) for key in keys]
        report = dict(mode='mock', transport='stub', action='MOCK_RECONCILIATION',
                      delivery='SIMULATION_ONLY', simulation=True,
                      warning='模擬配信の台帳修復です。実LINE送信・注文の実績ではありません。',
                      run_id=run_id, execution_day=execution_day.isoformat(),
                      plan_hash=prepared['plan_hash'], observed_at=now.isoformat(),
                      attempted_by_this_call=False, simulated_sent_by_this_call=False,
                      real_sent_by_this_call=False, repaired_keys=repaired, entries=entries)
        return _save_report(home, output, report)
