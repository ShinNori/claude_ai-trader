"""通知文生成とローカル outbox（共通仕様_フェーズ2）— Claude 単独ビルド

自動発注は行わない。人間が確認・発注するための通知をローカルディレクトリへ蓄積する。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .models import GateResult, JST, Proposal


def format_notice(p: Proposal, gate: GateResult) -> str:
    side_jp = "買い" if p.side == "BUY" else "売り"
    expiry_jst = p.expires_at.astimezone(JST)
    lines = [
        f"銘柄: {p.code}",
        f"売買: {side_jp}",
        f"数量: {p.qty}株",
        f"寄付指値: {p.limit_price}円",
        f"有効期限: {expiry_jst:%Y-%m-%d %H:%M} JST",
        f"理由: {p.reason}",
    ]
    if gate.allowed:
        lines.append("この提案はゲート判定を通過しました。")
    else:
        lines.append("この提案はゲート判定を通過していません。")
        if gate.reasons:
            lines.append("理由(不許可):")
            for r in gate.reasons:
                lines.append(f"  - {r}")
    lines.append("発注は人間が行います。自動発注なし。")
    return "\n".join(lines)


class Outbox:
    def __init__(self, dir: Path):
        self.dir = Path(dir)

    def _path_for(self, p: Proposal) -> Path:
        return self.dir / p.as_of.isoformat() / f"{p.proposal_id}.json"

    def enqueue(self, p: Proposal, gate: GateResult, text: str | None = None,
                now: datetime | None = None) -> Path:
        if text is None:
            text = format_notice(p, gate)
        queued_at = (now or datetime.now(JST)).isoformat()
        record = {
            "proposal": p.to_json(),
            "allowed": gate.allowed,
            "category": gate.category,
            "reasons": list(gate.reasons),
            "reserve_amount": gate.reserve_amount,
            "text": text,
            "queued_at": queued_at,
        }
        path = self._path_for(p)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def list(self, as_of: date | None = None) -> list[dict]:
        out: list[dict] = []
        if as_of is not None:
            dirs = [self.dir / as_of.isoformat()]
        else:
            dirs = sorted(self.dir.glob("*")) if self.dir.exists() else []
        for d in dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    out.append(json.loads(f.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
        return out
