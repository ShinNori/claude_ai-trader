"""Connect recorded stub judge responses to the existing mock daily runner."""
from pathlib import Path
import json
import re
import sqlite3
from contextlib import closing
from datetime import time
from aitrader_ops.judges import review
from .packet import render_packet
from .runner import RunError, aware, normalize_proposal, run, digest, _runner_home_guard


def run_reviewed_mock(home, run_id, execution_day, proposals, responses, now,
                      valuation, *, started_at, market_context, limits=None,
                      unresolved_unconfirmed=False):
    """Responses: {proposal_id: {judge: {record: dict, received_at: aware datetime}}}.

    Only explicitly supplied recordings are considered. Missing judges remain
    missing; no automatic APPROVE or real transport fallback is available.
    """
    home = _runner_home_guard(home, extra_files=('review-lock.sqlite', 'review-inputs.sqlite'))
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise RunError('run_idが不正です')
    if any('dropbox' in part.lower() for part in home.parts):
        raise RunError('実行先はDropbox外にしてください')
    marker = home / 'mock-runner.json'
    if not marker.is_file() or not (home / 'ledger.sqlite').is_file():
        raise RunError('initialize_mockで作った専用台帳が必要です')
    if json.loads(marker.read_text(encoding='utf-8')).get('mode') != 'mock':
        raise RunError('模擬モード以外は実装していません')
    now, started_at = aware(now), aware(started_at)
    if started_at > now or started_at.date() != execution_day or now.date() != execution_day:
        raise RunError('審査開始・完了と執行日が一致しません')
    if started_at.time() < time(7):
        raise RunError('審査開始は07:00以降です')
    ps = [normalize_proposal(p) for p in proposals]
    ids = {p.proposal_id for p in ps}
    if len(ids) != len(ps) or not isinstance(responses, dict) or set(responses) - ids:
        raise RunError('候補IDの重複または未知の応答があります')
    # Validate every envelope and render all packets before logging or reserving.
    packets, entries = {}, {}
    for p in ps:
        packets[p.proposal_id] = render_packet(p, market_context)
        entries[p.proposal_id] = []
        records = responses.get(p.proposal_id, {})
        if not isinstance(records, dict) or set(records) - {'claude', 'codex'}:
            raise RunError('未知のjudgeがあります')
        for judge, item in records.items():
            if not isinstance(item, dict) or set(item) != {'record', 'received_at'}:
                raise RunError('応答にはrecordとreceived_atが必要です')
            received = aware(item['received_at'])
            if not started_at <= received <= now:
                raise RunError('応答の受信時刻が実行範囲外です')
            entries[p.proposal_id].append((judge, item['record'], received))
    fingerprint = digest(dict(packets=packets, responses=responses,
                              started_at=started_at, execution_day=execution_day,
                              valuation=valuation, limits=limits,
                              unresolved=unresolved_unconfirmed))
    # Separate process lock keeps the durable input binding across failures.
    with closing(sqlite3.connect(home/'review-lock.sqlite', timeout=5,
                                 isolation_level=None)) as lock:
        lock.execute('BEGIN IMMEDIATE')
        with closing(sqlite3.connect(home/'review-inputs.sqlite',
                                     isolation_level=None)) as bindings:
            bindings.execute('CREATE TABLE IF NOT EXISTS inputs(id TEXT PRIMARY KEY, hash TEXT)')
            saved = bindings.execute('SELECT hash FROM inputs WHERE id=?', [run_id]).fetchone()
            if saved and saved[0] != fingerprint:
                raise RunError('同じrun_idの審査入力変更はできません')
            bindings.execute('INSERT OR IGNORE INTO inputs VALUES(?,?)', [run_id, fingerprint])
            votes = {}
            for p in ps:
                votes[p.proposal_id] = []
                for judge, record, received in entries[p.proposal_id]:
                    checked = review(packet=packets[p.proposal_id], judge=judge,
                        transport='stub', response=record, replay_path=None,
                        now=started_at, received_at=received, timeout_seconds=120,
                        model='mock', cli_version='mock', run_id=run_id,
                        prompt_version='review-v1',
                        log_dir=home/'runs'/execution_day.isoformat()/run_id/'reviews')
                    votes[p.proposal_id].append(checked['verdict'])
            return run(home, run_id, execution_day, ps, votes, now, valuation,
                       limits=limits, unresolved_unconfirmed=unresolved_unconfirmed)
