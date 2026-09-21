"""審査アダプタ（フェーズ2 順序4、共通仕様_フェーズ2_順序4_修正提案_v0.1 §2）。

判定パケット（build-codex/aitrader/packet.render_packet の JSON 文字列）を Claude / Codex の CLI に渡し、
応答を共通仕様の Verdict に正規化する。本ループでは transport='stub'（固定応答 / record-replay）だけを使い、
実 CLI・ネットワークは呼ばない。`build_cli_request` は起動計画の純粋な生成で、ファイル作成・プロセス起動をしない。

失敗種別と Verdict.decision（優先順位: 入力 hash → 締切 → timeout → process_error → malformed → 応答 ID/hash）:
    timeout / process_error → ABSTAIN、malformed / hash_mismatch / deadline → INVALID。
純粋な審査 ABSTAIN には failure を付けない。
"""
from __future__ import annotations

import json
import math
import os
import re
import uuid
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import JST, Proposal, Verdict, compute_packet_hash

JUDGES = ('claude', 'codex')
TRANSPORTS = ('stub', 'cli')
DECISIONS = ('APPROVE', 'REJECT', 'ABSTAIN')
RESPONSE_KEYS = ('proposal_id', 'packet_hash', 'decision', 'risks', 'reason', 'confidence')
REASON_MAX = 200
NOTIFY_CUTOFF = time(7, 15)                 # 設計書 8 章: 07:15 締切（JST）
STDOUT_LIMIT = 64 * 1024                    # ログに残す stdout/stderr の上限（バイト）
PROMPT_DIR = Path(__file__).resolve().parents[1] / 'config' / 'prompts'
SECRET_ENV = ('LINE_CHANNEL_SECRET', 'LINE_CHANNEL_ACCESS_TOKEN', 'JQUANTS_REFRESH_TOKEN')


class _Malformed(Exception):
    """応答の形式違反（malformed）。"""


def _aware(value: Any, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'{label} はタイムゾーン付き datetime が必要です')
    return value


def _strict_json(text: str) -> Any:
    """重複キー・NaN/Infinity を拒否する JSON 解析。失敗は _Malformed。"""
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise _Malformed('重複キー')
        return dict(items)

    def constant(name):
        raise _Malformed(f'JSON に {name} は許可されない')
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except _Malformed:
        raise
    except (ValueError, TypeError) as exc:
        raise _Malformed(str(exc)) from exc


def _parse_packet(packet: str) -> tuple[Proposal, str]:
    """パケット文字列から Proposal を復元し、14 フィールドの hash を再計算する。戻り値 (proposal, 再計算 hash)。"""
    if not isinstance(packet, str) or not packet.strip():
        raise ValueError('packet は空でない JSON 文字列が必要です')
    body = _strict_json(packet)
    if not isinstance(body, dict) or not isinstance(body.get('proposal'), dict):
        raise ValueError('packet に proposal オブジェクトがありません')
    raw = dict(body['proposal'])
    try:
        raw['as_of'] = date.fromisoformat(raw['as_of']) if isinstance(raw.get('as_of'), str) else raw['as_of']
        expires = raw['expires_at']
        raw['expires_at'] = datetime.fromisoformat(expires) if isinstance(expires, str) else expires
        proposal = Proposal(**{k: raw[k] for k in Proposal.__dataclass_fields__})
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'packet.proposal の形式が不正です: {exc}') from exc
    _aware(proposal.expires_at, 'proposal.expires_at')
    return proposal, compute_packet_hash(proposal)


def deadline_for(proposal: Proposal) -> datetime:
    """締切 = min(expires_at, expires_at の JST 日付の 07:15)。同時刻まで可。"""
    expires = _aware(proposal.expires_at, 'expires_at').astimezone(JST)
    cutoff = datetime.combine(expires.date(), NOTIFY_CUTOFF, tzinfo=JST)
    return min(expires, cutoff)


def _load_record(response: dict | None, replay_path: Path | None) -> dict:
    if (response is None) == (replay_path is None):
        raise ValueError('response と replay_path はどちらか一方だけを指定します')
    if replay_path is not None:
        record = _strict_json(Path(replay_path).read_text(encoding='utf-8'))
    else:
        record = response
    if not isinstance(record, dict):
        raise _Malformed('record はオブジェクトが必要')
    for key in ('stdout', 'final_message', 'exit_code', 'elapsed_seconds'):
        if key not in record:
            raise _Malformed(f'record.{key} が欠損')
    if not isinstance(record['stdout'], str):
        raise _Malformed('record.stdout は文字列')
    if record['final_message'] is not None and not isinstance(record['final_message'], str):
        raise _Malformed('record.final_message は文字列または null')
    if isinstance(record['exit_code'], bool) or not isinstance(record['exit_code'], int):
        raise _Malformed('record.exit_code は整数')
    elapsed = record['elapsed_seconds']
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
        raise _Malformed('record.elapsed_seconds は非負の有限数')
    return record


def _extract_answer(judge: str, record: dict) -> tuple[str | None, str | None]:
    """(failure, 判定 JSON 文字列)。process_error は failure を返し本文は None。"""
    if record['exit_code'] != 0:
        return 'process_error', None
    if judge == 'claude':
        try:
            envelope = _strict_json(record['stdout'])
        except _Malformed:
            raise _Malformed('Claude の stdout が JSON 外包ではない')
        if not isinstance(envelope, dict) or envelope.get('type') != 'result':
            raise _Malformed('Claude の stdout に type=result の外包がない')
        if envelope.get('is_error') is True or envelope.get('subtype') != 'success':
            return 'process_error', None
        if not isinstance(envelope.get('result'), str):
            raise _Malformed('Claude の result 文字列が欠損')
        return None, envelope['result']
    if record['final_message'] is None:
        raise _Malformed('Codex の最終応答（final_message）が欠損')
    return None, record['final_message']


def _validate_answer(text: str) -> dict:
    """応答 JSON の形式検証（additionalProperties=false、型・範囲）。失敗は _Malformed。"""
    answer = _strict_json(text)
    if not isinstance(answer, dict):
        raise _Malformed('応答は JSON オブジェクト 1 個')
    if set(answer) != set(RESPONSE_KEYS):
        raise _Malformed(f'応答キーは {RESPONSE_KEYS} と一致する必要がある')
    if not isinstance(answer['proposal_id'], str) or not answer['proposal_id']:
        raise _Malformed('proposal_id は空でない文字列')
    if not isinstance(answer['packet_hash'], str):
        raise _Malformed('packet_hash は文字列')
    if isinstance(answer['decision'], bool) or answer['decision'] not in DECISIONS:
        raise _Malformed('decision は APPROVE/REJECT/ABSTAIN の完全一致')
    risks = answer['risks']
    if not isinstance(risks, list) or any(not isinstance(r, str) or not r.strip() for r in risks):
        raise _Malformed('risks は空でない文字列の配列')
    if not isinstance(answer['reason'], str) or len(answer['reason']) > REASON_MAX:
        raise _Malformed(f'reason は {REASON_MAX} 文字以内の文字列')
    conf = answer['confidence']
    if conf is not None:
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not math.isfinite(conf) or not 0 <= conf <= 1:
            raise _Malformed('confidence は null または 0〜1 の有限数')
    return answer


def _mask(text: str) -> str:
    for name in SECRET_ENV:
        value = os.environ.get(name)
        if value:
            text = text.replace(value, f'<{name}>')
    if len(text.encode('utf-8')) > STDOUT_LIMIT:
        text = text.encode('utf-8')[:STDOUT_LIMIT].decode('utf-8', 'ignore') + '…<truncated>'
    return text


def _mask_deep(value: Any) -> Any:
    """ログの文字列をマスク。キー衝突時は順序付きエントリ列で全値を保持する。"""
    if isinstance(value, str):
        return _mask(value)
    if isinstance(value, dict):
        entries = [
            {'key': _mask(k) if isinstance(k, str) else k, 'value': _mask_deep(v)}
            for k, v in value.items()
        ]
        keys = [entry['key'] for entry in entries]
        if len(set(keys)) != len(keys):
            # マスク・64KB切詰めで同名になっても後勝ちで監査情報を失わない。
            # 元の秘密キーは保持せず、既存の同名マーカーも一エントリとして残す。
            return {'__masked_key_collision__': entries}
        return {entry['key']: entry['value'] for entry in entries}
    if isinstance(value, (list, tuple)):
        return [_mask_deep(v) for v in value]
    return value


def redact_log(value: Any) -> Any:
    """Return a recursively redacted copy for downstream audit artifacts."""
    return _mask_deep(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _log(log_dir: Path, row: dict) -> None:
    """決定ログ（UTF-8 JSONL、日付別）。保存失敗は例外で止める（承認を外へ返さない）。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    day = row['started_at'][:10].replace('-', '')
    path = log_dir / f'judges-{day}.jsonl'
    line = json.dumps(_jsonable(row), ensure_ascii=False, allow_nan=False)
    with path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(line + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def review(*, packet: str, judge: str, transport: str, response: dict | None, replay_path: Path | None,
           now: datetime, received_at: datetime, timeout_seconds: float, model: str, cli_version: str,
           run_id: str, prompt_version: str, log_dir: Path) -> dict:
    """1 回の審査を Verdict に正規化する。戻り値 {'verdict': Verdict, 'failure': str | None}。"""
    if judge not in JUDGES:
        raise ValueError(f'judge は {JUDGES} のいずれか')
    if transport not in TRANSPORTS:
        raise ValueError(f'transport は {TRANSPORTS} のいずれか')
    if transport == 'cli':
        raise ValueError('transport=cli は隔離環境の確認が未実施のため本ループでは無効（stub を使う）')
    _aware(now, 'now')
    _aware(received_at, 'received_at')
    if received_at < now:
        raise ValueError('received_at は now 以後が必要です')
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not timeout_seconds > 0:
        raise ValueError('timeout_seconds は正の数')
    for name, value in (('model', model), ('cli_version', cli_version), ('run_id', run_id), ('prompt_version', prompt_version)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{name} は空でない文字列')

    proposal, recomputed = _parse_packet(packet)
    failure: str | None = None
    decision = 'INVALID'
    risks: tuple[str, ...] = ()
    reason = ''
    confidence: float | None = None
    record: dict | None = None
    raw_answer: dict | None = None

    if recomputed != proposal.packet_hash:
        failure = 'hash_mismatch'
        reason = '入力パケットの hash 再計算が一致しない'
    else:
        deadline = deadline_for(proposal)
        if now > deadline or received_at > deadline:
            failure = 'deadline'
            reason = f'締切 {deadline.isoformat()} を超過'
        else:
            try:
                record = _load_record(response, replay_path)
                if record['elapsed_seconds'] > timeout_seconds:
                    failure, decision, reason = 'timeout', 'ABSTAIN', '審査がタイムアウト'
                else:
                    failure, text = _extract_answer(judge, record)
                    if failure == 'process_error':
                        decision, reason = 'ABSTAIN', 'CLI の異常終了またはエラー外包'
                    else:
                        raw_answer = _validate_answer(text)
                        if raw_answer['proposal_id'] != proposal.proposal_id or raw_answer['packet_hash'] != proposal.packet_hash:
                            failure, reason = 'hash_mismatch', '応答の proposal_id / packet_hash が対象と一致しない'
                        else:
                            decision = raw_answer['decision']
                            risks = tuple(raw_answer['risks'])
                            reason = raw_answer['reason']
                            confidence = None if raw_answer['confidence'] is None else float(raw_answer['confidence'])
            except _Malformed as exc:
                failure, decision, reason = 'malformed', 'INVALID', f'応答の形式違反: {exc}'

    verdict = Verdict(judge=judge, proposal_id=proposal.proposal_id, packet_hash=proposal.packet_hash,
                      decision=decision, risks=risks, reason=reason, confidence=confidence,
                      received_at=received_at, model=model, cli_version=cli_version, run_id=run_id)
    _log(Path(log_dir), _mask_deep(dict(
        packet=packet, record=record, judge=judge, model=model, cli_version=cli_version, run_id=run_id,
        prompt_version=prompt_version, transport=transport, started_at=now.isoformat(),
        received_at=received_at.isoformat(), failure=failure, verdict=asdict(verdict),
        raw_answer=raw_answer,
    )))
    return {'verdict': verdict, 'failure': failure}


def load_prompt(prompt_version: str) -> str:
    path = PROMPT_DIR / f'{prompt_version}.txt'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', prompt_version) or not path.exists():
        raise ValueError(f'prompt_version {prompt_version!r} のプロンプトがありません')
    return path.read_text(encoding='utf-8')


def build_cli_request(*, judge: str, packet: str, model: str, work_dir: Path, prompt_version: str) -> dict:
    """CLI 起動計画（純粋な生成。ファイル作成・プロセス起動・通信をしない）。"""
    if judge not in JUDGES:
        raise ValueError(f'judge は {JUDGES} のいずれか')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('model は空でない文字列')
    _parse_packet(packet)
    work_dir = Path(work_dir)
    stdin = load_prompt(prompt_version) + '\n---- 判定パケット（JSON） ----\n' + packet + '\n'
    if judge == 'claude':
        argv = ['claude', '-p', '--output-format', 'json', '--max-turns', '1', '--tools', '',
                '--strict-mcp-config', '--mcp-config', str(work_dir / 'empty-mcp.json'), '--model', model]
    else:
        argv = ['codex', 'exec', '-', '--output-schema', str(PROMPT_DIR / f'{prompt_version}.schema.json'),
                '--output-last-message', str(work_dir / f'verdict-{uuid.uuid4().hex}.json'),
                '--sandbox', 'read-only', '--skip-git-repo-check', '--model', model]
    return {'argv': argv, 'stdin': stdin, 'cwd': work_dir, 'shell': False, 'network_access': False,
            'timeout_seconds': 120, 'mcp_config': {'mcpServers': {}},
            'env_allowlist': ('PATH', 'SYSTEMROOT', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LANG')}
