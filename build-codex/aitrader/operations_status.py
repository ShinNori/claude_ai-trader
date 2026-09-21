"""Read-only combined view of saved daily history and current STOP state."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path

from aitrader_ops.models import JST

from .daily_receipt import inspect_daily_rehearsal
from .managed_stop import inspect_managed_stop, managed_stop_policy
from .runner import encoded


_PROGRESS_STATUSES = {'RUNNING', 'FAILED', 'COMPLETED'}
_STAGES = {'initialize', 'acquire_validate', 'generate', 'review',
           'prepare', 'enqueue', 'finalize'}
_SUMMARY_STATUSES = {'CANDIDATES', 'DATA_INCOMPLETE', 'NO_SESSION',
                     'REVIEW_INCOMPLETE', 'REVIEW_INVALID', 'NOT_APPROVED',
                     'NO_SIGNAL'}
_SCENARIOS = {'normal', 'data_missing', 'holiday', 'judge_missing',
              'deadline', 'stop_before_review'}
_ERROR_TYPE = re.compile(r'[A-Za-z_][A-Za-z0-9_.]{0,79}')


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('nowはタイムゾーン付きdatetimeで指定してください')
    return value.astimezone(JST)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _is_link(path):
    try:
        if path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path)):
            return True
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, FileNotFoundError):
        return False


def _progress_unknown(reason):
    return {'status': 'UNKNOWN', 'current_stage': None, 'checkpoint_seq': None,
            'known': False, 'verified': False, 'reasons': [reason]}


def _safe_progress_bytes(path):
    if _is_link(path) or not path.is_file():
        raise OSError('progress missing or unsafe')
    return path.read_bytes()


def _read_progress(home):
    path = home/'daily_progress.json'
    try:
        before = _safe_progress_bytes(path)
        value = json.loads(before.decode('utf-8'), object_pairs_hook=_unique)
        after = _safe_progress_bytes(path)
    except (OSError, UnicodeDecodeError, ValueError, TypeError,
            json.JSONDecodeError, RecursionError):
        return _progress_unknown('PROGRESS_UNREADABLE')
    if before != after:
        return _progress_unknown('PROGRESS_CHANGED')
    status = value.get('status') if isinstance(value, dict) else None
    current_stage = value.get('current_stage') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or not isinstance(status, str)
            or status not in _PROGRESS_STATUSES
            or not isinstance(current_stage, str) or current_stage not in _STAGES
            or type(value.get('checkpoint_seq')) is not int
            or value['checkpoint_seq'] <= 0
            or value.get('mode') != 'mock' or value.get('source') != 'synthetic'
            or value.get('virtual_clock') is not True
            or value.get('real_sent') is not False):
        return _progress_unknown('PROGRESS_INVALID')
    result = {'status': value['status'], 'current_stage': value['current_stage'],
              'checkpoint_seq': value['checkpoint_seq'], 'known': True, 'verified': False,
              'reasons': []}
    if value['status'] == 'COMPLETED':
        terminal = value.get('result_status')
        if (current_stage != 'finalize' or not isinstance(terminal, str)
                or terminal not in _SUMMARY_STATUSES):
            return _progress_unknown('PROGRESS_INVALID')
        result['result_status'] = terminal
    elif value['status'] == 'FAILED':
        failed = value.get('failed_stage')
        error_type = value.get('error_type')
        if (not isinstance(failed, str) or failed not in _STAGES
                or value.get('partial_state_may_exist') is not True
                or value.get('auto_resume') is not False):
            return _progress_unknown('PROGRESS_INVALID')
        safe_error = (error_type if isinstance(error_type, str)
                      and _ERROR_TYPE.fullmatch(error_type) else 'OTHER')
        result.update(failed_stage=failed, error_type=safe_error)
    return result


def _saved_summary(value):
    if not isinstance(value, dict):
        return None
    status, scenario, day = value.get('status'), value.get('scenario'), value.get('execution_day')
    reservation, reserved = value.get('reservation'), value.get('reserved')
    try:
        normalized_day = datetime.strptime(day, '%Y-%m-%d').date().isoformat()
    except (TypeError, ValueError):
        return None
    if (not isinstance(status, str) or status not in _SUMMARY_STATUSES
            or not isinstance(scenario, str) or scenario not in _SCENARIOS
            or day != normalized_day or type(reservation) is not int or reservation < 0
            or type(reserved) is not int or reserved < 0 or reserved != reservation):
        return None
    result = {'status': status, 'scenario': scenario, 'execution_day': day,
              'reservation': reservation, 'reserved': reserved}
    if value.get('managed_stop') is True and value.get('stop_policy') == 'managed-v1':
        result.update(managed_stop=True, stop_policy='managed-v1')
    return result


def _unknown_stop(now, reason, managed):
    return {'status': 'UNKNOWN', 'known': False, 'effective_stop': True,
            'persistent_stopped': None, 'reasons': [reason],
            'observed_at': now.isoformat(), 'control_event_at': None,
            'read_only': True, 'managed': managed}


def _preflight_home(value):
    try:
        home = Path(value).absolute()
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError('INVALID_HOME') from exc
    if any('dropbox' in part.lower() for part in home.parts):
        raise ValueError('DROPBOX_HOME_REJECTED')
    try:
        for component in reversed((home, *home.parents)):
            if _is_link(component):
                raise ValueError('UNSAFE_HOME_LINK')
    except OSError as exc:
        raise ValueError('UNSAFE_HOME_LINK') from exc
    try:
        managed_stop_policy(home)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError('MOCK_MARKER_INVALID') from exc
    progress_path = home/'daily_progress.json'
    try:
        progress_before = _safe_progress_bytes(progress_path)
    except OSError:
        progress_before = None
    return home, progress_before


def build_operations_status(home, *, now):
    """Build a bounded view; real_sent fields describe this display call only."""
    now = _aware(now)
    preflight_reason = None
    try:
        home, progress_before = _preflight_home(home)
    except ValueError as exc:
        home, progress_before = None, None
        preflight_reason = str(exc) or 'INVALID_HOME'
    history_raw = inspect_daily_rehearsal(home) if home is not None else {
        'status': 'NEEDS_RECONCILIATION', 'reason': preflight_reason}
    history = {'status': history_raw.get('status', 'NEEDS_RECONCILIATION')}
    if history['status'] == 'VERIFIED_HISTORY':
        summary = _saved_summary(history_raw.get('saved_summary'))
        if summary is None:
            history = {'status': 'NEEDS_RECONCILIATION',
                       'reason': 'SAVED_SUMMARY_INVALID'}
        elif datetime.strptime(summary['execution_day'], '%Y-%m-%d').date() > now.date():
            history = {'status': 'NEEDS_RECONCILIATION',
                       'reason': 'HISTORY_FROM_FUTURE'}
        else:
            history['summary'] = summary
    else:
        history['reason'] = history_raw.get('reason', 'HISTORY_UNKNOWN')
    progress = (_read_progress(home) if home is not None
                else _progress_unknown(preflight_reason or 'INVALID_HOME'))

    managed = False
    if home is None:
        stop = _unknown_stop(now, preflight_reason or 'INVALID_HOME', False)
    else:
        try:
            policy = managed_stop_policy(home)
            if policy is None:
                stop = _unknown_stop(now, 'LEGACY_STOP_UNMANAGED', False)
            else:
                managed = True
                stop = dict(inspect_managed_stop(home, now=now), managed=True)
        except (OSError, ValueError, RecursionError):
            stop = _unknown_stop(now, 'MANAGED_STOP_UNKNOWN', True)

    if home is not None:
        try:
            progress_after = _safe_progress_bytes(home/'daily_progress.json')
        except OSError:
            progress_after = None
        if progress_before != progress_after:
            progress = _progress_unknown('PROGRESS_CHANGED_DURING_COMPOSITION')
            history = {'status': 'NEEDS_RECONCILIATION',
                       'reason': 'PROGRESS_CHANGED_DURING_COMPOSITION'}

    if not progress['known'] or not stop['known']:
        overall = 'UNKNOWN'
    elif stop['effective_stop']:
        overall = 'STOPPED'
    elif progress['status'] != 'COMPLETED' or history['status'] != 'VERIFIED_HISTORY':
        overall = 'NEEDS_RECONCILIATION'
    else:
        overall = 'CLEAR'
    return {'status': overall, 'read_only': True, 'current_signal': False,
            'auto_resume': False, 'real_sent': False,
            'real_sent_by_this_call': False,
            'observation_atomic': False,
            'observed_at': now.isoformat(),
            'observed_at_source': 'explicit_input', 'history': history,
            'progress': progress, 'stop': stop}


def main(argv=None):
    parser = argparse.ArgumentParser(description='模擬運用状態の読取専用統合表示')
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--now', required=True, help='タイムゾーン付きISO日時')
    parser.add_argument('--format', choices=('json',), default='json')
    args = parser.parse_args(argv)
    try:
        result = build_operations_status(args.home, now=args.now)
    except (ValueError, TypeError):
        parser.error('nowにはタイムゾーン付きISO日時を指定してください')
    print(encoded(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
