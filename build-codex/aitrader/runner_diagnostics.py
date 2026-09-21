"""Conservative read-only observations of incomplete mock runner state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from aitrader_ops.judges import redact_log
from aitrader_ops.ledger import _NOTICE_TRANSITIONS, LedgerError, validate_snapshot_for_replay
from .notification_plan import _inside, _read
from .runner import RunError, digest, encoded, normalize_proposal, plain
from .managed_stop import managed_stop_policy


def _diagnose_mock_run_internal(home, run_id, execution_day):
    """Inspect stopped databases only; classifications never authorize restart.

    Existing WAL/SHM means a live or uncheckpointed database and is refused.
    Concurrent external writes are unsupported; no cross-DB snapshot is promised.
    """
    home = Path(home).resolve()
    _inside(home, home)
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise RunError('run_idが不正です')
    if not isinstance(execution_day, date) or isinstance(execution_day, datetime):
        raise RunError('執行日はdateで指定してください')
    policy = managed_stop_policy(home)
    fixed_policy = None if policy is None else plain({
        key: ([str(item) for item in value] if isinstance(value, tuple) else
              str(value) if isinstance(value, Path) else value)
        for key, value in policy.items()})
    report = dict(mode='mock', action='READ_ONLY_DIAGNOSIS', run_id=run_id,
                  execution_day=execution_day.isoformat(), classification='MISSING',
                  reasons=[], candidates=[], snapshot_consistent=False,
                  automatic_resume_allowed=False, repaired=False,
                  current_signal=False,
                  warning='停止中DBの読取観測のみ。WAL/SHM存在時は診断不可。DB間の同時点整合は保証せず、自動再開の許可ではありません。')
    journal_path = _inside(home, home/'orchestration.sqlite')
    ledger_path = _inside(home, home/'ledger.sqlite')
    if not journal_path.is_file() or not ledger_path.is_file():
        report['reasons'].append('REQUIRED_DATABASE_MISSING')
        return report
    def has_sidecars():
        return any(Path(str(path)+suffix).exists()
                   for path in (journal_path, ledger_path) for suffix in ('-wal', '-shm'))

    if has_sidecars():
        report.update(classification='NEEDS_RECONCILIATION', reasons=['LIVE_DATABASE_UNSUPPORTED'])
        return report
    try:
        with closing(sqlite3.connect(journal_path.as_uri()+'?mode=ro&immutable=1', uri=True)) as journal:
            saved = journal.execute('SELECT hash,manifest,result FROM runs WHERE id=?', [run_id]).fetchone()
            rows = journal.execute('SELECT pid,hash,day,side,state,result,owner FROM candidates').fetchall()
            outbox = dict(journal.execute('SELECT key,body FROM outbox').fetchall())
        with closing(sqlite3.connect(ledger_path.as_uri()+'?mode=ro&immutable=1', uri=True)) as ledger:
            events = ledger.execute('SELECT seq,kind,payload FROM ledger_events ORDER BY seq').fetchall()
    except sqlite3.DatabaseError:
        report.update(classification='CONFLICT', reasons=['DATABASE_UNREADABLE'])
        return report
    if has_sidecars():
        report.update(classification='NEEDS_RECONCILIATION', reasons=['LIVE_DATABASE_UNSUPPORTED'])
        return report
    if saved is None:
        report['reasons'].append('RUN_ORIGINAL_MISSING')
        return report
    folder = _inside(home, home/'runs'/execution_day.isoformat()/run_id)
    try:
        manifest = json.loads(saved[1])
        if ((fixed_policy is not None and manifest.get('managed_stop_policy') != fixed_policy)
                or (fixed_policy is None and 'managed_stop_policy' in manifest)):
            raise ValueError('managed policy mismatch')
        if (manifest.get('mode') != 'mock' or manifest.get('run_id') != run_id
                or manifest.get('execution_day') != execution_day.isoformat()
                or manifest.get('request_hash') != saved[0]):
            raise ValueError('manifest mismatch')
        artifacts = {name: _read(_inside(home, folder/(name+'.json')))
                     for name in ('manifest', 'proposals', 'verdicts', 'result', 'gate_results')}
        if artifacts['manifest'] != redact_log(manifest):
            raise ValueError('manifest artifact mismatch')
        hashes = manifest.get('saved_artifact_hashes', {})
        if any(hashes.get(name) != digest(artifacts[name]) for name in ('proposals', 'verdicts')):
            raise ValueError('artifact hash mismatch')
        completed = saved[2] is not None
        if completed and artifacts['result'] != redact_log(json.loads(saved[2])):
            raise ValueError('result artifact mismatch')
        if artifacts['gate_results'] != artifacts['result'].get('candidates'):
            raise ValueError('gate artifact mismatch')
        proposals = {p.proposal_id: p for p in map(normalize_proposal, artifacts['proposals'])}
        if len(proposals) != len(artifacts['proposals']):
            raise ValueError('duplicate proposal')
        result_rows = {item['proposal_id']: item for item in artifacts['result']['candidates']}
        if completed and (set(result_rows) != set(proposals)
                          or len(result_rows) != len(artifacts['result']['candidates'])):
            raise ValueError('result candidate mismatch')
    except (RunError, ValueError, TypeError, KeyError, AttributeError):
        report.update(classification='CONFLICT', reasons=['ORIGINAL_OR_ARTIFACT_MISMATCH'])
        return redact_log(report)
    notices = {}
    complex_history = False
    initialized = False
    try:
        for seq, kind, payload in events:
            value = json.loads(payload)
            if kind == 'SNAPSHOT':
                if initialized or notices:
                    raise ValueError('duplicate or late snapshot')
                validate_snapshot_for_replay(value)
                initialized = True
                if value.get('open_orders'):
                    complex_history = True
            elif kind == 'NOTICE_CREATED':
                p = normalize_proposal(value['proposal'])
                if not initialized or p.proposal_id in notices:
                    raise ValueError('uninitialized or duplicate notice')
                notices[p.proposal_id] = dict(state='CREATED', proposal=p, seq=seq)
            elif kind == 'NOTICE_STATE':
                if value['proposal_id'] not in notices:
                    raise ValueError('unknown notice')
                previous = notices[value['proposal_id']]['state']
                if value['state'] not in _NOTICE_TRANSITIONS.get(previous, set()):
                    raise ValueError('invalid notice transition')
                notices[value['proposal_id']].update(state=value['state'], seq=seq)
            else:
                complex_history = True
        if not initialized:
            raise ValueError('snapshot missing')
    except (LedgerError, ValueError, TypeError, KeyError, AttributeError):
        report.update(classification='CONFLICT', reasons=['LEDGER_EVENT_UNREADABLE'])
        return redact_log(report)
    indexed = {row[0]: row for row in rows}
    for pid, proposal in proposals.items():
        row, notice = indexed.get(pid), notices.get(pid)
        item = dict(proposal_id=pid, journal_owner=row[6] if row else None,
                    journal_state=row[4] if row else None,
                    ledger_state='UNKNOWN' if complex_history else notice['state'] if notice else None,
                    ledger_notice_present=notice is not None,
                    classification='NEEDS_RECONCILIATION', reasons=[])
        if row is None:
            item['classification'] = 'MISSING'
            item['reasons'].append('CANDIDATE_JOURNAL_MISSING')
        elif (row[1] != digest(proposal) or row[2] != execution_day.isoformat()
              or row[3] != proposal.side or row[6] != run_id):
            item.update(classification='CONFLICT', reasons=['CANDIDATE_BINDING_MISMATCH'])
        elif notice and digest(notice['proposal']) != digest(proposal):
            item.update(classification='CONFLICT', reasons=['LEDGER_PROPOSAL_MISMATCH'])
        elif completed and (row[4] != result_rows[pid].get('status')
                            or row[5] is None):
            item.update(classification='CONFLICT', reasons=['JOURNAL_RESULT_MISMATCH'])
        elif complex_history:
            item['reasons'].append('COMPLEX_LEDGER_HISTORY_REQUIRES_REPLAY')
        elif row[4] == 'INTENT':
            item['reasons'].append('LEDGER_SIDE_WRITE' if notice else 'INTENT_WITHOUT_NOTICE')
        elif row[4] == 'APPROVED':
            key = f'{execution_day}:{pid}:{proposal.packet_hash}:CANDIDATE'
            if not notice or key not in outbox:
                item.update(classification='CONFLICT', reasons=['APPROVED_LEDGER_OR_OUTBOX_MISSING'])
            else:
                try:
                    body = json.loads(outbox[key])
                    valid = (body.get('key') == key and body.get('mode') == 'mock'
                             and body.get('delivery') == 'NOT_SENT'
                             and digest(normalize_proposal(body['proposal'])) == digest(proposal))
                except (ValueError, TypeError, KeyError, AttributeError):
                    valid = False
                if not valid:
                    item.update(classification='CONFLICT', reasons=['OUTBOX_CONTENT_MISMATCH'])
                elif notice['state'] in ('APPROVED', 'SENT'):
                    item.update(classification='COMPLETE', reasons=[])
                else:
                    item['reasons'].append('LEDGER_STATE_REQUIRES_RECONCILIATION')
        elif row[4] == 'REJECTED' and not notice:
            item.update(classification='COMPLETE', reasons=[])
        else:
            item['reasons'].append('UNRECOGNIZED_COMBINATION')
        report['candidates'].append(item)
        if completed and row is not None and row[5] is not None:
            try:
                result_matches = redact_log(json.loads(row[5])) == result_rows[pid]
            except (ValueError, TypeError):
                result_matches = False
            if not result_matches:
                item.update(classification='CONFLICT', reasons=['JOURNAL_RESULT_MISMATCH'])
    classes = {item['classification'] for item in report['candidates']}
    report['classification'] = ('CONFLICT' if 'CONFLICT' in classes else
                                'MISSING' if 'MISSING' in classes else
                                'COMPLETE' if completed and classes <= {'COMPLETE'} else
                                'NEEDS_RECONCILIATION')
    if not completed:
        report['reasons'].append('RUN_INCOMPLETE')
    return redact_log(report)


_SIDECAR_SUFFIXES = ('-wal', '-shm', '-journal', '.wal', '.shm', '.journal')


def _link_or_reparse(path):
    if path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path)):
        return True
    try:
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return False


def _safe_home(home):
    path = Path(home).absolute()
    if any('dropbox' in part.lower() for part in path.parts):
        raise RunError('実行先はDropbox外にしてください')
    for item in reversed((path, *path.parents)):
        try:
            if _link_or_reparse(item):
                raise RunError('実行homeを安全に読み取れません')
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RunError('実行homeを安全に読み取れません') from exc
    return path


def _fingerprint(path):
    """Return a bounded-memory identity for a regular file, or None if missing."""
    try:
        for item in reversed((path, *path.parents)):
            try:
                if _link_or_reparse(item):
                    raise OSError('unsafe file')
            except FileNotFoundError:
                continue
        if _link_or_reparse(path):
            raise OSError('unsafe file')
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise OSError('not regular')
    value = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        identity = lambda value: (value.st_dev, value.st_ino, value.st_mode)
        if not stat.S_ISREG(opened.st_mode) or identity(before) != identity(opened):
            raise OSError('not regular')
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            value.update(chunk)
    after = path.lstat()
    if (_link_or_reparse(path) or not stat.S_ISREG(after.st_mode)
            or identity(before) != identity(after)
            or size != opened.st_size
            or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
        raise OSError('changed file')
    return size, value.hexdigest()


def _observation_paths(home, run_id, execution_day):
    databases = (home/'orchestration.sqlite', home/'ledger.sqlite')
    folder = home/'runs'/execution_day.isoformat()/run_id
    files = (home/'mock-runner.json', *databases,
             *(folder/(name+'.json') for name in
               ('manifest', 'proposals', 'verdicts', 'result', 'gate_results')))
    sidecars = tuple(Path(str(db)+suffix) for db in databases for suffix in _SIDECAR_SUFFIXES)
    return files, sidecars


def _uncertain_report(run_id, execution_day, reason):
    return dict(mode='mock', action='READ_ONLY_DIAGNOSIS', run_id=run_id,
                execution_day=execution_day.isoformat(),
                classification='NEEDS_RECONCILIATION', reasons=[reason], candidates=[],
                snapshot_consistent=False, automatic_resume_allowed=False,
                observation_unchanged=False, repaired=False, current_signal=False,
                warning='読取観測中の変化または安全でないファイルを検知しました。自動再開の許可ではありません。')


def diagnose_mock_run(home, run_id, execution_day):
    """Inspect a mock run through conservative before/after file guards."""
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise RunError('run_idが不正です')
    if not isinstance(execution_day, date) or isinstance(execution_day, datetime):
        raise RunError('執行日はdateで指定してください')
    home = _safe_home(home)
    files, sidecars = _observation_paths(home, run_id, execution_day)
    try:
        before_sidecars = {str(path): _fingerprint(path) for path in sidecars}
        if any(value is not None for value in before_sidecars.values()):
            return _uncertain_report(run_id, execution_day, 'LIVE_DATABASE_UNSUPPORTED')
        before = {str(path): _fingerprint(path) for path in files}
        policy_before = managed_stop_policy(home)
    except (OSError, RecursionError):
        return _uncertain_report(run_id, execution_day, 'OBSERVATION_UNSAFE')
    try:
        result = _diagnose_mock_run_internal(home, run_id, execution_day)
        policy_after = managed_stop_policy(home)
        after = {str(path): _fingerprint(path) for path in files}
        after_sidecars = {str(path): _fingerprint(path) for path in sidecars}
    except (OSError, RunError, ValueError, TypeError, RecursionError):
        return _uncertain_report(run_id, execution_day, 'OBSERVATION_UNSAFE')
    if (before != after or before_sidecars != after_sidecars
            or any(value is not None for value in after_sidecars.values())
            or policy_before != policy_after):
        return _uncertain_report(run_id, execution_day, 'OBSERVATION_CHANGED')
    result['snapshot_consistent'] = False
    result['observation_unchanged'] = True
    result.setdefault('current_signal', False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='模擬runの読み取り専用診断（修復なし）')
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--day', type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    try:
        result = diagnose_mock_run(args.home, args.run_id, args.day)
    except (RunError, ValueError) as exc:
        parser.exit(2, f'{exc}\n')
    print(encoded(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
