"""Prepare immutable mock candidate cards; never enqueue or send them."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from contextlib import closing, nullcontext
from datetime import date, datetime, time
from pathlib import Path

from aitrader_ops.judges import redact_log
from aitrader_ops.ledger import Ledger
from aitrader_ops.notify import render_message
from .runner import (RunError, aware, digest, encoded, normalize_proposal, plain,
                     write_json, _prior_candidate, _runner_home_guard)


_NOTIFICATION_FILES = ('notification-plans.sqlite', 'notification.sqlite',
                       'managed-stop-clock.json', 'STOP')


def _notification_home_guard(home):
    return _runner_home_guard(home, extra_files=_NOTIFICATION_FILES)


def _inside(home, path):
    home = Path(home).absolute()
    raw = Path(path).absolute()
    if not raw.is_relative_to(home):
        raise RunError('通知計画の入出力はDropbox外の専用home内に限定します')
    for candidate in reversed((raw, *raw.parents)):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise RunError('通知計画の入出力pathを安全に確認できません') from None
        if (stat.S_ISLNK(info.st_mode)
                or (hasattr(os.path, 'isjunction') and os.path.isjunction(candidate))
                or bool(getattr(info, 'st_file_attributes', 0)
                        & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))):
            raise RunError('通知計画の入出力pathを安全に確認できません')
    resolved = raw.resolve()
    if not resolved.is_relative_to(home) or any('dropbox' in p.lower() for p in resolved.parts):
        raise RunError('通知計画の入出力はDropbox外の専用home内に限定します')
    return resolved


def _read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RunError('保存済み模擬実行の成果物を確認できません') from exc


def _fixed_stop_policy(home, settings):
    """Bind managed notification rendering to the runner's immutable policy."""
    from .managed_stop import managed_stop_policy

    policy = managed_stop_policy(home)
    if policy is None:
        return None
    if digest(settings) != policy['settings_hash']:
        raise RunError('managed STOPの固定settingsが一致しません')
    return {
        key: ([str(item) for item in value] if isinstance(value, tuple) else
              str(value) if isinstance(value, Path) else value)
        for key, value in policy.items()
    }


def _validate_run_time(home, run_id, execution_day, now):
    """Call under runner-lock after _prepare_locked verified the saved original."""
    folder = _inside(home, home/'runs'/execution_day.isoformat()/run_id)
    manifest = _read(_inside(home, folder/'manifest.json'))
    # runner's started_at is its decision time, after all accepted review input.
    # Invalid review timestamps must not prevent a REVIEW_INVALID status card.
    if aware(manifest.get('started_at')) > now:
        raise RunError('審査結果の確定より前の時刻では通知処理できません')
    binding_path = _inside(home, home/'notification-plans.sqlite')
    with closing(sqlite3.connect(binding_path.as_uri()+'?mode=ro', uri=True)) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_clock'").fetchone()
        if exists:
            saved = db.execute('SELECT observed_at FROM notification_clock WHERE run_id=?', [run_id]).fetchone()
            if saved and aware(saved[0]) > now:
                raise RunError('前回の耐久通知観測より前の時刻では処理できません')
    for name in ('notification_queue.json', 'mock_delivery.json', 'mock_reconciliation.json'):
        path = _inside(home, folder/name)
        if path.is_file():
            observation = _read(path)
            if not isinstance(observation, dict):
                raise RunError('通知観測記録の形式が不正です')
            if aware(observation.get('observed_at')) > now:
                raise RunError('前回の通知観測より前の時刻では処理できません')


def _record_notification_time(home, run_id, execution_day, now):
    """Persist an operation's clock after preflight, before its first mutation.

    The caller holds runner-lock. Even a no-send observation or failed report
    save retains this clock; equal-time retries are allowed, rollback is not.
    """
    _validate_run_time(home, run_id, execution_day, now)
    binding_path = _inside(home, home/'notification-plans.sqlite')
    with closing(sqlite3.connect(binding_path.as_uri()+'?mode=rw', uri=True,
                                isolation_level=None)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            db.execute('CREATE TABLE IF NOT EXISTS notification_clock '
                       '(run_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL)')
            saved = db.execute('SELECT observed_at FROM notification_clock WHERE run_id=?', [run_id]).fetchone()
            if saved and aware(saved[0]) > now:
                raise RunError('前回の耐久通知観測より前の時刻では処理できません')
            db.execute('INSERT INTO notification_clock VALUES (?,?) '
                       'ON CONFLICT(run_id) DO UPDATE SET observed_at=excluded.observed_at',
                       [run_id, now.isoformat()])
            db.execute('COMMIT')
        except BaseException:
            try:
                db.execute('ROLLBACK')
            except BaseException:
                pass
            raise


def _prepare_locked(home, run_id, execution_day, *, contexts, settings,
                          include_status=False, allow_sent=False):
    """Render APPROVED candidates from saved mock artifacts without delivery.

    contexts maps proposal IDs to explicitly supplied display dictionaries.
    Existing plans are immutable, including settings and display context.
    The binding journal contains masked cards and input digests, not secrets.
    include_status adds a fixed summary of this run, never a daily aggregate.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise RunError('run_idが不正です')
    if not isinstance(execution_day, date) or isinstance(execution_day, datetime):
        raise RunError('執行日はdateで指定してください')
    home = _notification_home_guard(home)
    _inside(home, home)
    ledger_path = _inside(home, home/'ledger.sqlite')
    if not ledger_path.is_file():
        raise RunError('initialize_mockで作った専用台帳が必要です')
    if not isinstance(contexts, dict) or not isinstance(settings, dict):
        raise RunError('contextsとsettingsを明示した辞書が必要です')
    if not isinstance(include_status, bool):
        raise RunError('include_statusはboolで指定してください')
    fixed_stop_policy = _fixed_stop_policy(home, settings)
    raw_folder = home/'runs'/execution_day.isoformat()/run_id
    folder = _inside(home, raw_folder)
    raw_output = raw_folder/'notification_plan.json'
    output = _inside(home, raw_output)
    lock_path = _inside(home, home/'runner-lock.sqlite')
    binding_path = _inside(home, home/'notification-plans.sqlite')
    with nullcontext():
        artifacts = {name: _read(_inside(home, folder/(name+'.json')))
                     for name in ('manifest', 'proposals', 'verdicts', 'result', 'gate_results')}
        manifest, result = artifacts['manifest'], artifacts['result']
        journal_path = _inside(home, home/'orchestration.sqlite')
        if not journal_path.is_file():
            raise RunError('完了実行の原本がありません')
        with closing(sqlite3.connect(journal_path.as_uri()+'?mode=ro', uri=True)) as journal:
            original = journal.execute('SELECT manifest,result FROM runs WHERE id=?', [run_id]).fetchone()
        if (not original or original[1] is None
                or redact_log(json.loads(original[0])) != manifest
                or redact_log(json.loads(original[1])) != result):
            raise RunError('保存済み成果物と実行原本が一致しません')
        hashes = manifest.get('saved_artifact_hashes')
        if (not isinstance(hashes, dict)
                or any(hashes.get(name) != digest(artifacts[name])
                       for name in ('proposals', 'verdicts'))):
            raise RunError('候補・審査成果物の固定hashがありません、または不一致です。新しい模擬runが必要です')
        if (not isinstance(manifest, dict) or not isinstance(result, dict)
                or manifest.get('run_id') != run_id
                or manifest.get('execution_day') != execution_day.isoformat()
                or any(x.get('mode') != 'mock' or x.get('delivery') != 'NOT_SENT'
                       for x in (manifest, result))
                or result.get('status') not in ('CANDIDATES', 'NO_SIGNAL', 'NOT_APPROVED',
                                               'REVIEW_INCOMPLETE', 'REVIEW_INVALID')):
            raise RunError('完了済みの模擬未送信結果ではありません')
        if fixed_stop_policy is not None:
            if manifest.get('managed_stop_policy') != plain(fixed_stop_policy):
                raise RunError('実行時と通知時のmanaged STOP policyが一致しません')
        elif 'managed_stop_policy' in manifest:
            raise RunError('legacy実行にmanaged STOP policyが混在しています')
        if artifacts['gate_results'] != result.get('candidates'):
            raise RunError('保存済みゲート結果が一致しません')
        if not isinstance(artifacts['proposals'], list) or not isinstance(artifacts['verdicts'], dict):
            raise RunError('候補または審査結果の形式が不正です')
        proposals = [normalize_proposal(p) for p in artifacts['proposals']]
        by_id = {p.proposal_id: p for p in proposals}
        if len(by_id) != len(proposals) or set(contexts)-set(by_id):
            raise RunError('候補IDが重複または未知です')
        candidates = result['candidates']
        if not isinstance(candidates, list) or any(not isinstance(c, dict) for c in candidates):
            raise RunError('候補結果の形式が不正です')
        ids = [c.get('proposal_id') for c in candidates]
        if len(ids) != len(set(ids)) or set(ids) != set(by_id):
            raise RunError('候補と結果のIDが一致しません')
        # A saved run result is history, not sufficient ownership evidence for
        # a new notification operation. Check the live journal before effects.
        try:
            with closing(sqlite3.connect(journal_path.as_uri()+'?mode=ro', uri=True)) as journal:
                for candidate in candidates:
                    prior = _prior_candidate(journal, by_id[candidate['proposal_id']],
                                             run_id, execution_day)
                    if (prior is None or prior[0] == 'INTENT'
                            or redact_log(plain(prior[1])) != candidate):
                        raise RunError('通知対象の候補原本と保存結果が一致しません')
        except sqlite3.DatabaseError:
            raise RunError('通知対象の候補原本を確認できません') from None
        plans = []
        ledger = Ledger(ledger_path)
        try:
            for candidate in sorted(candidates, key=lambda c: c['proposal_id']):
                if candidate.get('status') != 'APPROVED':
                    continue
                p = by_id[candidate['proposal_id']]
                if candidate.get('gate', {}).get('allowed') is not True:
                    raise RunError('ゲート承認を確認できません')
                if p.side not in ('BUY', 'SELL') or aware(p.expires_at).date() != execution_day:
                    raise RunError('候補の方向または執行日が不正です')
                votes = artifacts['verdicts'].get(p.proposal_id)
                if not isinstance(votes, list) or len(votes) != 2 or any(not isinstance(v, dict) for v in votes):
                    raise RunError('両審査の承認が必要です')
                if {v.get('judge') for v in votes} != {'claude', 'codex'}:
                    raise RunError('両審査の承認が必要です')
                for v in votes:
                    received = aware(v.get('received_at'))
                    if (v.get('decision') != 'APPROVE' or v.get('proposal_id') != p.proposal_id
                            or v.get('packet_hash') != p.packet_hash or v.get('run_id') != run_id
                            or v.get('model') != 'mock' or v.get('cli_version') != 'mock'
                            or received.date() != execution_day or received.time() >= time(7, 15)):
                        raise RunError('保存済み模擬承認と候補が一致しません')
                try:
                    notice = ledger.notice(p.proposal_id)
                    recorded = normalize_proposal(ledger.proposal(p.proposal_id))
                except KeyError as exc:
                    raise RunError('台帳に候補がありません') from exc
                allowed_states = ('APPROVED', 'SENT') if allow_sent else ('APPROVED',)
                if notice['notice_state'] not in allowed_states or digest(recorded) != digest(p):
                    raise RunError('台帳の承認と候補が一致しません')
                context = contexts.get(p.proposal_id)
                if not isinstance(context, dict):
                    raise RunError('候補ごとの表示contextを明示してください')
                kind = 'NEW' if p.side == 'BUY' else 'EXIT'
                message = render_message(kind=kind, proposal=p, verdicts=votes,
                                         context=context, settings=settings)
                warning = '【模擬・未送信】実際の注文には使用しないでください。'
                message['altText'] = warning + message['altText']
                message['contents']['body']['contents'].insert(0, {'type': 'text', 'text': warning, 'wrap': True})
                plans.append(dict(key=f'{execution_day}:{p.proposal_id}:{p.packet_hash}:{kind}',
                                  kind=kind, proposal_id=p.proposal_id, packet_hash=p.packet_hash,
                                  message=redact_log(message)))
        finally:
            ledger.close()
        status_text = {
            'NO_SIGNAL': '候補なし。',
            'REVIEW_INCOMPLETE': '審査未完了の候補があります。未完了候補は見送ります。',
            'NOT_APPROVED': '承認条件を満たす候補はありません。',
            'REVIEW_INVALID': '審査結果を確認できない候補があります。該当候補は保留しました。',
        }
        source_status = result['status']
        if include_status and source_status in status_text:
            approved_count = sum(c.get('status') == 'APPROVED' for c in candidates)
            incomplete_count = sum('REVIEW_INCOMPLETE' in c.get('gate', {}).get('reason_codes', [])
                                   for c in candidates)
            invalid_count = sum('REVIEW_INVALID' in c.get('gate', {}).get('reason_codes', [])
                                for c in candidates)
            counts = (f' 承認: {approved_count}件。審査未完了該当: {incomplete_count}件。'
                      f'審査結果不正該当: {invalid_count}件。'
                      '各該当件数は同一候補を重複して含む場合があります。')
            message = render_message(kind='RECONCILE', context={
                'text': '【模擬・未送信】当該runの結果: '+status_text[source_status]
                        +counts
                        +' 日次全体の結果ではありません。実際の注文には使用しないでください。',
            }, settings=settings)
            plans.append(dict(key=f'{execution_day}:{run_id}:STATUS', kind='RECONCILE',
                              proposal_id=None, source_status=source_status,
                              plan_type='RUN_STATUS', message=redact_log(message)))
        fingerprint_input = dict(artifacts=artifacts, contexts=contexts,
                                 settings=settings, include_status=include_status)
        if fixed_stop_policy is not None:
            fingerprint_input['managed_stop_policy'] = fixed_stop_policy
        fingerprint = digest(fingerprint_input)
        prepared = dict(mode='mock', delivery='NOT_SENT', run_id=run_id,
                        execution_day=execution_day.isoformat(), plan_hash=fingerprint, plans=plans)
        with closing(sqlite3.connect(binding_path, isolation_level=None)) as db:
            db.executescript('CREATE TABLE IF NOT EXISTS plans(run_id TEXT PRIMARY KEY, hash TEXT, body TEXT);'
                             'CREATE TABLE IF NOT EXISTS cards(key TEXT PRIMARY KEY, hash TEXT);')
            db.execute('BEGIN IMMEDIATE')
            saved = db.execute('SELECT hash,body FROM plans WHERE run_id=?', [run_id]).fetchone()
            if saved and saved[0] != fingerprint:
                raise RunError('固定済み通知計画の入力変更はできません')
            if saved:
                try:
                    valid_body = digest(json.loads(saved[1])) == digest(prepared)
                except (ValueError, TypeError, RecursionError):
                    valid_body = False
                if not valid_body:
                    raise RunError('固定済み通知計画の原本内容が一致しません')
            for plan in plans:
                context = contexts[plan['proposal_id']] if plan['proposal_id'] is not None else None
                card_input = dict(plan=plan, context=context, settings=settings)
                if fixed_stop_policy is not None:
                    card_input['managed_stop_policy'] = fixed_stop_policy
                card_hash = digest(card_input)
                prior = db.execute('SELECT hash FROM cards WHERE key=?', [plan['key']]).fetchone()
                if prior and prior[0] != card_hash:
                    raise RunError('同一候補の通知計画変更はできません')
                db.execute('INSERT OR IGNORE INTO cards VALUES(?,?)', [plan['key'], card_hash])
            if not saved:
                db.execute('INSERT INTO plans VALUES(?,?,?)', [run_id, fingerprint, encoded(prepared)])
            if output.exists() and _read(output) != prepared:
                raise RunError('保存済み通知計画が変更されています')
            db.execute('COMMIT')
        _inside(home, raw_output)
        if raw_output.exists():
            if _read(raw_output) != prepared:
                raise RunError('保存済み通知計画が変更されています')
        else:
            write_json(raw_output, prepared, redact=False)
        return prepared


def prepare_notifications(home, run_id, execution_day, *, contexts, settings, include_status=False):
    """Prepare fixed mock cards under the shared runner lock."""
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise RunError('run_idが不正です')
    if not isinstance(execution_day, date) or isinstance(execution_day, datetime):
        raise RunError('執行日はdateで指定してください')
    if not isinstance(contexts, dict) or not isinstance(settings, dict):
        raise RunError('contextsとsettingsを明示した辞書が必要です')
    if not isinstance(include_status, bool):
        raise RunError('include_statusはboolで指定してください')
    home = _notification_home_guard(home)
    _inside(home, home)
    from .managed_stop import managed_stop_policy
    managed_stop_policy(home)
    if not _inside(home, home/'ledger.sqlite').is_file():
        raise RunError('initialize_mockで作った専用台帳が必要です')
    with closing(sqlite3.connect(_inside(home, home/'runner-lock.sqlite'),
                                isolation_level=None, timeout=5)) as lock:
        lock.execute('BEGIN IMMEDIATE')
        return _prepare_locked(home, run_id, execution_day, contexts=contexts,
                               settings=settings, include_status=include_status)
