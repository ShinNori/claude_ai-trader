"""AI判定のパース・実行（共通仕様_フェーズ2 §2, §4.2）— Claude 単独ビルド

parse_verdict: AI の生応答を Verdict に正規化する。数量・価格等の変更提案や
proposal_id / packet_hash の不一致は INVALID として扱う（§4.2）。
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from typing import Protocol

from .models import Proposal, Verdict
from .packet import render_packet

_DECISIONS = ("APPROVE", "REJECT", "ABSTAIN")

# キー: proposal の対応する属性
_GUARD_FIELDS = {
    "qty": "qty",
    "quantity": "qty",
    "limit_price": "limit_price",
    "price": "limit_price",
    "side": "side",
    "code": "code",
    "exec_condition": "exec_condition",
    "account_type": "account_type",
}

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict | None:
    s = raw.strip()
    # ```json ... ``` フェンス除去
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        pass
    m = _JSON_OBJ_RE.search(s)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def parse_verdict(raw, *, judge: str, proposal: Proposal, received_at: datetime,
                   model: str = "", cli_version: str = "", run_id: str = "") -> Verdict:
    reason = ""
    risks: tuple[str, ...] = ()
    confidence = None
    decision = "INVALID"

    data: dict | None = None
    if raw is None:
        reason = "応答なし（タイムアウトまたは出力なし）"
    elif isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        data = _extract_json(raw)
        if data is None:
            reason = "JSON として解釈できない応答"
    else:
        reason = f"不明な応答型: {type(raw).__name__}"

    if data is not None:
        # 変更禁止フィールドのチェック
        changed = []
        for key, attr in _GUARD_FIELDS.items():
            if key in data:
                pv = getattr(proposal, attr)
                dv = data[key]
                if isinstance(pv, float) and isinstance(dv, (int, float)) and not isinstance(dv, bool):
                    if float(dv) != float(pv):
                        changed.append(key)
                else:
                    if dv != pv:
                        changed.append(key)
        for key in ("proposal_id", "packet_hash"):
            if key in data and data[key] != getattr(proposal, key):
                changed.append(key)

        if changed:
            decision = "INVALID"
            reason = f"AIが変更不可フィールドを変更しようとした: {', '.join(changed)}"
        else:
            raw_decision = data.get("decision")
            if isinstance(raw_decision, str) and raw_decision.strip().upper() in _DECISIONS:
                decision = raw_decision.strip().upper()
                reason = str(data.get("reason", ""))
            else:
                decision = "INVALID"
                reason = f"未知または欠落した decision: {raw_decision!r}"

            risks_raw = data.get("risks", ())
            if isinstance(risks_raw, (list, tuple)):
                risks = tuple(str(r) for r in risks_raw)

            conf_raw = data.get("confidence")
            if isinstance(conf_raw, (int, float)) and not isinstance(conf_raw, bool):
                confidence = float(conf_raw)
                if not (0.0 <= confidence <= 1.0):
                    confidence = None

    return Verdict(
        judge=judge,
        proposal_id=proposal.proposal_id,
        packet_hash=proposal.packet_hash,
        decision=decision,
        risks=risks,
        reason=reason,
        confidence=confidence,
        received_at=received_at,
        model=model,
        cli_version=cli_version,
        run_id=run_id,
    )


class Judge(Protocol):
    name: str

    def review(self, p: Proposal, packet_text: str) -> str | dict | None:
        ...


class MockJudge:
    def __init__(self, name: str, decision: str = "APPROVE", reason: str = "mock",
                 risks: tuple = (), raw_override=None):
        self.name = name
        self.decision = decision
        self.reason = reason
        self.risks = risks
        self.raw_override = raw_override

    def review(self, p: Proposal, packet_text: str):
        if self.raw_override is not None:
            return self.raw_override
        return {"decision": self.decision, "reason": self.reason, "risks": list(self.risks)}


class CommandJudge:
    def __init__(self, name: str, command: list[str], timeout_s: float = 120.0,
                 model: str = "", cli_version: str = ""):
        self.name = name
        self.command = command
        self.timeout_s = timeout_s
        self.model = model
        self.cli_version = cli_version

    def review(self, p: Proposal, packet_text: str):
        try:
            res = subprocess.run(
                self.command, input=packet_text, capture_output=True, text=True,
                timeout=self.timeout_s,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if res.returncode != 0:
            return None
        return res.stdout


def run_judges(p: Proposal, judges: list, now: datetime, market_context: dict | None = None) -> list[Verdict]:
    packet_text = render_packet(p, market_context)
    out: list[Verdict] = []
    for judge in judges:
        raw = judge.review(p, packet_text)
        run_id = f"{judge.name}-{p.proposal_id}-{now:%Y%m%dT%H%M%S}"
        model = getattr(judge, "model", "")
        cli_version = getattr(judge, "cli_version", "")
        v = parse_verdict(raw, judge=judge.name, proposal=p, received_at=now,
                           model=model, cli_version=cli_version, run_id=run_id)
        out.append(v)
    return out
