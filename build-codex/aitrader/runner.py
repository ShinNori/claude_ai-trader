"""Mock-only phase-2 runner. No model processes, network or delivery adapter."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from aitrader_ops.gate import evaluate
from aitrader_ops.judges import redact_log
from aitrader_ops.ledger import Ledger
from aitrader_ops.limits import Limits
from aitrader_ops.models import JST, Proposal, Verdict, compute_packet_hash
from .db import _runtime_path_guard, connect


class RunError(ValueError):
    pass


def _run_output_guard(folder):
    """Reject unsafe known run outputs before opening or mutating the journal."""
    folder = Path(folder).absolute()
    outputs = ('manifest.json', 'proposals.json', 'verdicts.json',
               'gate_results.json', 'result.json', 'failure.json')
    paths = [(parent, True) for parent in reversed((folder, *folder.parents))]
    paths.extend((folder/name, False) for name in outputs)
    for path, directory in paths:
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise RunError('模擬実行の成果物保存先を安全に確認できません') from None
        unsafe = (stat.S_ISLNK(info.st_mode)
                  or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))
                  or bool(getattr(info, 'st_file_attributes', 0)
                          & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)))
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if unsafe or not valid:
            raise RunError('模擬実行の成果物保存先を安全に確認できません')


def _runner_home_guard(home, *, extra_files=()):
    """Validate raw runner paths before any read, create, or DB connection."""
    try:
        home = _runtime_path_guard(home)
    except (TypeError, ValueError, OSError):
        raise RunError('模擬実行先を安全に確認できません') from None

    def observe(path, directory=False):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise RunError('模擬実行先を安全に確認できません') from None
        unsafe = (stat.S_ISLNK(info.st_mode)
                  or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))
                  or bool(getattr(info, 'st_file_attributes', 0)
                          & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)))
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if unsafe or not valid:
            raise RunError('模擬実行先を安全に確認できません')
        return True

    if (not isinstance(extra_files, tuple)
            or any(not isinstance(name, str) or not name or Path(name).name != name
                   for name in extra_files)):
        raise RunError('模擬実行先を安全に確認できません')
    sqlite_names = ('ledger.sqlite', 'orchestration.sqlite', 'runner-lock.sqlite')
    for name in (('mock-runner.json', *sqlite_names) + extra_files):
        observe(home / name)
    for name in sqlite_names + tuple(name for name in extra_files if name.endswith('.sqlite')):
        for suffix in ('-wal', '-shm', '-journal'):
            observe(home / (name + suffix))
    observe(home / 'runs', directory=True)
    return home


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (date, datetime, Decimal)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    return value


def encoded(value):
    return json.dumps(plain(value), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def write_json(path, value, *, redact=True):
    if type(redact) is not bool:
        raise RunError('redactはboolで指定してください')
    path = Path(path).absolute()
    body = encoded(redact_log(plain(value)) if redact else plain(value)).encode('utf-8')

    def reject_unsafe(item, *, directory=False):
        try:
            info = item.lstat()
        except FileNotFoundError:
            return False
        is_reparse = (item.is_symlink()
                      or (hasattr(os.path, 'isjunction') and os.path.isjunction(item))
                      or bool(getattr(info, 'st_file_attributes', 0)
                              & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)))
        expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if is_reparse or not expected:
            raise RunError('JSON保存先を安全に確認できません')
        return True

    absolute_parent = path.parent
    for parent in reversed((absolute_parent, *absolute_parent.parents)):
        reject_unsafe(parent, directory=True)
    reject_unsafe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in reversed((absolute_parent, *absolute_parent.parents)):
        if not reject_unsafe(parent, directory=True):
            raise RunError('JSON保存先を安全に確認できません')
    reject_unsafe(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix='.'+path.name+'.', suffix='.tmp', dir=path.parent)
    temporary = Path(temporary_name)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(body)
    os.replace(temporary, path)


def aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise RunError('日時にはタイムゾーンが必要です')
    return value.astimezone(JST)


def normalize_proposal(value):
    fields = asdict(value) if is_dataclass(value) else dict(value)
    if isinstance(fields.get('as_of'), str):
        fields['as_of'] = date.fromisoformat(fields['as_of'])
    fields['expires_at'] = aware(fields.get('expires_at'))
    p = Proposal(**fields)
    if compute_packet_hash(p) != p.packet_hash:
        raise RunError('パケットの内容とhashが一致しません')
    return p


@dataclass(frozen=True)
class Valuation:
    """Caller supplies reconciled start/history values, never implicit zeroes."""
    day_start_equity: int
    previous_peak: int
    net_external_flow: int = 0

    def calculate(self, view, marks):
        for value in (self.day_start_equity, self.previous_peak, self.net_external_flow):
            if isinstance(value, bool) or not isinstance(value, int):
                raise RunError('評価基準と入出金は整数円が必要です')
        if self.day_start_equity <= 0 or self.previous_peak <= 0:
            raise RunError('評価基準・過去高値がありません')
        total = Decimal(view.cash)
        for code, pos in view.positions.items():
            mark = Decimal(str(marks.get(code, 'NaN')))
            if not mark.is_finite() or mark <= 0:
                raise RunError(f'{code} の評価価格がありません')
            total += Decimal(pos.qty) * mark
        # external cash flow adjusted values share the day's starting capital scale.
        equity = int(total.to_integral_value(rounding='ROUND_HALF_UP'))
        adjusted = equity-self.net_external_flow
        if adjusted <= 0:
            raise RunError('入出金調整後の資産が正ではありません')
        return dict(equity=equity, adjusted_equity=adjusted,
                    daily_pnl=adjusted-self.day_start_equity,
                    day_start_equity=self.day_start_equity,
                    peak_equity=max(self.previous_peak, adjusted))


def initialize_mock(home, cash, positions, at):
    """Explicitly create a fresh simulation ledger; never adopt an existing ledger."""
    home = _runner_home_guard(home)
    home.mkdir(parents=True, exist_ok=True)
    home = _runner_home_guard(home)
    if (home/'ledger.sqlite').exists():
        raise RunError('既存台帳を模擬台帳として初期化できません')
    ledger = Ledger(home/'ledger.sqlite')
    try:
        ledger.init_snapshot(cash, positions, [], aware(at))
    finally:
        ledger.close()
    write_json(home/'mock-runner.json', {'mode': 'mock', 'version': 1})


def mock_verdicts(proposals, run_id, received_at, decisions=None):
    """Explicit deterministic fixtures; never a fallback for real model failures."""
    return {p.proposal_id: [Verdict(j, p.proposal_id, p.packet_hash,
                                   (decisions or {}).get(j, 'APPROVE'), (), '模擬判定', None,
                                   aware(received_at), 'mock', 'mock', run_id)
                            for j in ('claude', 'codex')] for p in proposals}


def _prior_candidate(journal, proposal, run_id, execution_day):
    prior = journal.execute(
        'SELECT hash,state,result,owner,day,side FROM candidates WHERE pid=?',
        [proposal.proposal_id]).fetchone()
    if prior is None:
        return None
    if prior[0] != digest(proposal):
        raise RunError('既存候補の内容が変わっています')
    if prior[3] != run_id:
        raise RunError('別runの候補を再利用できません')
    if prior[4] != execution_day.isoformat() or prior[5] != proposal.side:
        raise RunError('既存候補の執行日または売買方向が一致しません')
    if prior[1] not in ('INTENT', 'APPROVED', 'REJECTED'):
        raise RunError('既存候補の状態を確認できません')
    try:
        item = json.loads(prior[2])
        gate = item['gate']
        expected_status = 'REJECTED' if prior[1] == 'REJECTED' else 'APPROVED'
        valid = (isinstance(item, dict)
                 and item.get('proposal_id') == proposal.proposal_id
                 and item.get('status') == expected_status
                 and isinstance(gate, dict)
                 and type(gate.get('allowed')) is bool
                 and isinstance(gate.get('reason_codes'), list)
                 and all(isinstance(code, str) for code in gate['reason_codes'])
                 and ((item['status'] == 'APPROVED') == gate['allowed']))
    except (TypeError, ValueError, KeyError):
        valid = False
    if not valid:
        raise RunError('既存候補の判定原本を確認できません')
    body = None
    if prior[1] == 'APPROVED':
        key = f'{execution_day}:{proposal.proposal_id}:{proposal.packet_hash}:CANDIDATE'
        row = journal.execute('SELECT body FROM outbox WHERE key=?', [key]).fetchone()
        try:
            body = json.loads(row[0]) if row else None
            body_proposal = normalize_proposal(body['proposal'])
            valid_body = (body.get('key') == key and body.get('mode') == 'mock'
                          and body.get('delivery') == 'NOT_SENT'
                          and body.get('kind') == 'CANDIDATE'
                          and digest(body_proposal) == digest(proposal))
        except (TypeError, ValueError, KeyError, AttributeError):
            valid_body = False
        if not valid_body:
            raise RunError('承認済み候補のoutbox原本を確認できません')
    return prior[1], item, body


def _calendar(home, day, proposals):
    if not (home/'market.duckdb').exists():
        raise RunError('市場DBがありません')
    with connect(home) as con:
        rows = con.execute('SELECT date,is_business_day FROM calendar ORDER BY date').fetchall()
        price_rows = con.execute('SELECT code,close FROM prices_daily WHERE date=?',
                                 [proposals[0].as_of] if proposals else [day-timedelta(days=1)]).fetchall()
    calendar = dict(rows)
    if calendar.get(day) is not True:
        raise RunError('執行日が確認済み営業日ではありません')
    if len({p.as_of for p in proposals}) > 1:
        raise RunError('候補の基準日が揃っていません')
    marks = dict(price_rows)
    for p in proposals:
        mark = Decimal(str(marks.get(p.code, 'NaN')))
        if not mark.is_finite() or mark <= 0:
            raise RunError(f'{p.code} の基準日当日の評価価格がありません')
        if p.expires_at != datetime.combine(day, time(8, 59), JST):
            raise RunError('候補の注文期限が本日08:59ではありません')
        points = [p.as_of, day]
        earnings = p.events.get('next_earnings_date')
        if earnings not in (None, 'UNKNOWN'):
            points.append(date.fromisoformat(earnings) if isinstance(earnings, str) else earnings)
        lo, hi = min(points), max(points)
        if any(lo+timedelta(days=i) not in calendar for i in range((hi-lo).days+1)):
            raise RunError('営業日カレンダーに未確認の日があります')
        if not calendar.get(p.as_of) or any(calendar[d] for d in calendar if p.as_of < d < day):
            raise RunError('候補の基準日が前営業日ではありません')
    return [d for d, business in rows if business], marks, rows


def run(home, run_id, execution_day, proposals, verdicts, now, valuation,
        limits=None, unresolved_unconfirmed=False):
    """Serialize candidates, persist inputs and prepare local NOT_SENT outbox.

    A completed run is immutable. Resume can finish a saved INTENT only when no
    ledger notice exists; an uncertain cross-DB write stops for reconciliation.
    """
    home = _runner_home_guard(home)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise RunError('run_idが不正です')
    if not (home/'mock-runner.json').exists() or not (home/'ledger.sqlite').exists():
        raise RunError('initialize_mockで作った専用台帳が必要です')
    if json.loads((home/'mock-runner.json').read_text(encoding='utf-8')).get('mode') != 'mock':
        raise RunError('模擬モード以外は実装していません')
    from .managed_stop import managed_stop_policy, inspect_managed_stop
    policy = managed_stop_policy(home)
    now = aware(now)
    if now.date() != execution_day:
        raise RunError('実行日時と執行日が一致しません')
    ps = sorted([normalize_proposal(p) for p in proposals], key=lambda p: p.proposal_id)
    if len({p.proposal_id for p in ps}) != len(ps):
        raise RunError('候補IDが重複しています')
    limits = limits or Limits()
    request = dict(proposals=ps, verdicts=verdicts, valuation=valuation,
                   limits=limits, execution_day=execution_day, unresolved=unresolved_unconfirmed)
    if policy is not None:
        request['managed_stop_policy'] = {
            key: ([str(v) for v in value] if isinstance(value, tuple) else
                  str(value) if isinstance(value, Path) else value)
            for key, value in policy.items()}
    request_hash = digest(request)
    run_dir = home/'runs'/execution_day.isoformat()/run_id
    _run_output_guard(run_dir)
    # Separate lock DB keeps the process lock while durable journal entries commit.
    lock = sqlite3.connect(home/'runner-lock.sqlite', isolation_level=None, timeout=5)
    try:
        journal = sqlite3.connect(home/'orchestration.sqlite', isolation_level=None)
    except BaseException:
        try:
            lock.close()
        except Exception:
            pass
        raise
    ledger = None
    try:
        lock.execute('BEGIN IMMEDIATE')
        _runner_home_guard(home)
        _run_output_guard(run_dir)
        if managed_stop_policy(home) != policy:
            raise RunError('ロック取得中に停止管理の設定が変わりました')
        journal.executescript('''
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, hash TEXT, manifest TEXT, result TEXT);
            CREATE TABLE IF NOT EXISTS candidates(pid TEXT PRIMARY KEY, hash TEXT, day TEXT,
                side TEXT, state TEXT, result TEXT, owner TEXT);
            CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY, body TEXT);
        ''')
        saved = journal.execute('SELECT hash,manifest,result FROM runs WHERE id=?', [run_id]).fetchone()
        if saved and saved[0] != request_hash:
            raise RunError('同じrun_idの入力変更はできません')
        if saved and saved[2]:
            result = json.loads(saved[2])
            _artifacts(run_dir, json.loads(saved[1]), request, result)
            return result
        business_days, marks, calendar = _calendar(home, execution_day, ps)
        ledger = Ledger(home/'ledger.sqlite')
        if Decimal(ledger.policy()['fee_margin']) != Decimal(str(limits.fee_margin)):
            raise RunError('台帳とゲートの予約率が一致しません')
        manifest = dict(mode='mock', delivery='NOT_SENT', run_id=run_id,
                        execution_day=execution_day.isoformat(), started_at=now.isoformat(),
                        request_hash=request_hash, calendar_hash=digest(calendar), marks=marks,
                        valuation=plain(valuation), initial_ledger_seq=ledger.seq())
        if policy is not None:
            manifest['managed_stop_policy'] = plain(request['managed_stop_policy'])
        manifest['saved_artifact_hashes'] = {
            'proposals': digest(redact_log(plain(ps))),
            'verdicts': digest(redact_log(plain(verdicts))),
        }
        if saved:
            old = json.loads(saved[1])
            if old['calendar_hash'] != manifest['calendar_hash'] or old['marks'] != plain(marks):
                raise RunError('再開時に参照データが変わっています')
            manifest = old
        else:
            journal.execute('INSERT INTO runs VALUES(?,?,?,NULL)', [run_id, request_hash, encoded(manifest)])
        _artifacts(run_dir, manifest, request, {'status': 'RUNNING', 'candidates': [], 'outbox': []})
        deadline = datetime.combine(execution_day, time(7, 15), JST)
        if now < datetime.combine(execution_day, time(7), JST):
            raise RunError('審査開始は07:00以降です')
        results, outbox = [], []
        for p in ps:
            prior = _prior_candidate(journal, p, run_id, execution_day)
            if prior:
                state, prior_result, prior_body = prior
                if state != 'INTENT':
                    results.append(prior_result)
                    if prior_body is not None:
                        outbox.append(prior_body)
                    continue
            try:
                ledger.notice(p.proposal_id)
            except KeyError:
                pass
            else:
                raise RunError('台帳だけに通知が存在します。照合が必要です')
            vs = verdicts.get(p.proposal_id, [])
            received = []
            invalid = False
            try:
                for v in vs:
                    if not isinstance(v, Verdict) or v.run_id != run_id or v.model != 'mock' or v.cli_version != 'mock':
                        invalid = True
                        continue
                    at = aware(v.received_at)
                    if at.date() != execution_day or at > now or at >= deadline:
                        invalid = True
                    if v.decision == 'INVALID':
                        invalid = True
                    received.append(v)
            except (ValueError, TypeError):
                invalid = True
            view = ledger.view()
            values = valuation.calculate(view, marks)
            view.daily_pnl = values['daily_pnl']
            view.day_start_equity = values['day_start_equity']
            slots = journal.execute("SELECT count(*) FROM candidates WHERE day=? AND side='BUY' AND state IN ('INTENT','APPROVED') AND pid<>?",
                                    [execution_day.isoformat(), p.proposal_id]).fetchone()[0]
            incomplete = now >= deadline or len(received) < 2
            observations = []
            if policy is not None:
                observations.append(inspect_managed_stop(home, now=now))
            stopped = observations[-1]['effective_stop'] if observations else (home/'STOP').exists()
            result = evaluate(p, received, now, view, limits, values['equity'],
                              values['equity'], 0,
                              stopped,
                              unresolved_unconfirmed or bool(ledger.unconfirmed(now)) or bool(ledger.pending_rows()),
                              business_days=business_days)
            extra = []
            if p.side == 'BUY' and observations and not observations[-1]['known']:
                extra.append('STOP_STATE_UNKNOWN')
            if incomplete:
                extra.append('REVIEW_INCOMPLETE')
            if invalid:
                extra.append('REVIEW_INVALID')
            if p.side == 'BUY' and slots >= limits.max_new_per_day:
                extra.append('DAILY_NEW_LIMIT')
            # Gate receives actual equity for concentration. DD uses flow-adjusted
            # capital on a separate scale here; do not distort the concentration denominator.
            if p.side == 'BUY' and Decimal(values['adjusted_equity']) <= Decimal(values['peak_equity'])*(1-Decimal(str(limits.drawdown_stop))):
                extra.append('DRAWDOWN_STOP')
            if extra:
                result.allowed = False
                result.reason_codes.extend(extra)
            # Managed controls share this lock; direct external writers do not.
            if result.allowed and p.side == 'BUY' and policy is not None:
                observations.append(inspect_managed_stop(home, now=now))
                latest = observations[-1]
                if latest['effective_stop']:
                    result.allowed = False
                    result.reason_codes.append('STOP_NEW')
                    if not latest['known']:
                        result.reason_codes.append('STOP_STATE_UNKNOWN')
            item = dict(proposal_id=p.proposal_id, status='APPROVED' if result.allowed else 'REJECTED',
                        gate=plain(result), valuation=values, ledger_seq=ledger.seq())
            if policy is not None:
                item['stop_observations'] = observations
            if result.allowed:
                journal.execute('INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?)',
                                [p.proposal_id, digest(p), execution_day.isoformat(), p.side, 'INTENT', encoded(item), run_id])
                ledger.create_notice(p, at=now)
                ledger.set_notice_state(p.proposal_id, 'APPROVED', now)
                item['ledger_seq'] = ledger.seq()
                body = dict(key=f'{execution_day}:{p.proposal_id}:{p.packet_hash}:CANDIDATE',
                            mode='mock', delivery='NOT_SENT', kind='CANDIDATE', proposal=plain(p),
                            message='模擬候補です。実際の注文には使用しないでください。')
                journal.execute('BEGIN')
                try:
                    journal.execute('INSERT INTO outbox VALUES(?,?)', [body['key'], encoded(body)])
                    journal.execute("UPDATE candidates SET state='APPROVED',result=? WHERE pid=?", [encoded(item), p.proposal_id])
                    journal.execute('COMMIT')
                except BaseException:
                    try:
                        journal.execute('ROLLBACK')
                    except BaseException:
                        pass
                    raise
                outbox.append(body)
            else:
                journal.execute('INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?)',
                                [p.proposal_id, digest(p), execution_day.isoformat(), p.side, 'REJECTED', encoded(item), run_id])
            results.append(item)
        codes = {c for r in results for c in r['gate']['reason_codes']}
        status = ('REVIEW_INCOMPLETE' if 'REVIEW_INCOMPLETE' in codes else
                  'REVIEW_INVALID' if 'REVIEW_INVALID' in codes else
                  'CANDIDATES' if any(r['status'] == 'APPROVED' for r in results) else
                  'NOT_APPROVED' if results else 'NO_SIGNAL')
        summary = dict(key=f'{execution_day}:{run_id}:STATUS', mode='mock', delivery='NOT_SENT', kind=status,
                       message={'REVIEW_INCOMPLETE': '締切までに審査が完了しませんでした。未完了候補は見送ります。',
                                'REVIEW_INVALID': '審査結果を確認できないため候補を保留しました。',
                                'NO_SIGNAL': '本日サインなし（模擬実行）。'}.get(status, '模擬実行の判定結果です。'))
        journal.execute('INSERT OR IGNORE INTO outbox VALUES(?,?)', [summary['key'], encoded(summary)])
        result = dict(status=status, candidates=results, outbox=outbox+[summary], mode='mock', delivery='NOT_SENT')
        journal.execute('UPDATE runs SET result=? WHERE id=?', [encoded(result), run_id])
        _artifacts(run_dir, manifest, request, result)
        return result
    except Exception as exc:
        write_json(run_dir/'failure.json', dict(status='SYSTEM_ERROR', mode='mock', delivery='NOT_SENT',
                                              message='模擬処理を停止しました。照合が必要です。', detail=str(exc)))
        raise
    finally:
        try:
            if ledger:
                ledger.close()
        finally:
            try:
                journal.close()
            finally:
                try:
                    if lock.in_transaction:
                        lock.execute('ROLLBACK')
                finally:
                    lock.close()


def _artifacts(folder, manifest, request, result):
    write_json(folder/'manifest.json', manifest)
    write_json(folder/'proposals.json', request['proposals'])
    write_json(folder/'verdicts.json', request['verdicts'])
    write_json(folder/'gate_results.json', result['candidates'])
    write_json(folder/'result.json', result)
