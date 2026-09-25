"""通知ゲート（共通仕様_フェーズ2 §4）— Claude 単独ビルド

evaluate() は不許可理由を全部列挙する。confidence / risks は判定に使わない。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from .models import GateResult, Limits, Proposal, Verdict, packet_hash, reserve_amount
from .packet import next_business_day


def _g(obj, name, default=None):
    return getattr(obj, name, default) if obj is not None else default


def evaluate(p: Proposal, verdicts: list[Verdict], now: datetime, ledger_view, limits: Limits,
             equity: int, peak_equity: int, new_sent_today: int, stop_flag: bool,
             unresolved_unconfirmed: bool) -> GateResult:
    reasons: list[str] = []
    notes: list[str] = []
    category = "EXIT" if p.side == "SELL" else "NEW"

    # 1-2. 合議: claude と codex がちょうど1件ずつ、両方 APPROVE、id と hash が一致
    by_judge: dict[str, list[Verdict]] = {}
    for v in verdicts:
        by_judge.setdefault(v.judge, []).append(v)
    for j in ("claude", "codex"):
        vs = by_judge.get(j, [])
        if len(vs) != 1:
            reasons.append(f"審査 {j} の応答が {len(vs)} 件（ちょうど 1 件が必要）")
            continue
        v = vs[0]
        if v.decision != "APPROVE":
            reasons.append(f"審査 {j} が {v.decision}: {v.reason}")
        if v.proposal_id != p.proposal_id:
            reasons.append(f"審査 {j} の proposal_id が候補と不一致（{v.proposal_id}）")
        if v.packet_hash != p.packet_hash:
            reasons.append(f"審査 {j} の packet_hash が候補と不一致（旧版または改変後の候補への承認）")
        notes.extend(f"{j}: risk={r}" for r in v.risks)
        if v.confidence is not None:
            notes.append(f"{j}: confidence={v.confidence}（判定には未使用）")
    if packet_hash(p) != p.packet_hash:
        reasons.append("候補の packet_hash が内容と一致しない（審査後に改変された疑い）")

    # 3. 期限
    if now > p.expires_at:
        reasons.append(f"期限切れ（expires_at={p.expires_at.isoformat()}, now={now.isoformat()}）")

    # 4. 注文契約
    if p.exec_condition != "OPENING_LIMIT":
        reasons.append(f"執行条件 {p.exec_condition} は無効（OPENING_LIMIT のみ）")
    if p.account_type != "CASH":
        reasons.append(f"口座区分 {p.account_type} は無効（CASH のみ）")
    if p.side not in ("BUY", "SELL"):
        reasons.append(f"売買区分 {p.side} は無効")
    if not isinstance(p.lot_size, int) or p.lot_size <= 0:
        reasons.append(f"売買単位 {p.lot_size} は無効")
    elif p.qty <= 0 or p.qty % p.lot_size != 0:
        reasons.append(f"数量 {p.qty} が売買単位 {p.lot_size} の正の整数倍でない")
    if not isinstance(p.limit_price, (int, float)) or p.limit_price <= 0:
        reasons.append(f"指値 {p.limit_price} は無効")

    # 5. 現物・余力
    positions = _g(ledger_view, "positions", {}) or {}
    reserved_positions = _g(ledger_view, "reserved_positions", {}) or {}
    reserve = 0
    if p.side == "SELL":
        held = _g(positions.get(p.code), "qty", 0) if p.code in positions else 0
        if held < p.qty:
            reasons.append(f"保有 {held} 株 < 売却 {p.qty} 株（現物限定・空売り不可）")
    elif p.side == "BUY" and p.qty > 0 and p.limit_price > 0:
        reserve = reserve_amount(p.qty, p.limit_price, limits.fee_margin)
        available = _g(ledger_view, "available", 0)
        if reserve > available:
            reasons.append(f"予約額 {reserve} 円 > 余力 {available} 円（既存予約 {_g(ledger_view, 'reserved', 0)} 円を含む）")

    # 6. イベント
    ev = p.events or {}
    ne = ev.get("next_earnings_date", "UNKNOWN")
    mr = ev.get("margin_regulated", "UNKNOWN")
    exec_day = next_business_day(p.as_of)
    if ne == "UNKNOWN":
        reasons.append("次回決算日が UNKNOWN（保留）")
    elif isinstance(ne, date) and abs((ne - exec_day).days) <= limits.earnings_blackout_days:
        reasons.append(f"決算日 {ne} が執行日 {exec_day} の ±{limits.earnings_blackout_days} 日以内")
    if mr is True:
        reasons.append("信用規制銘柄")
    elif mr != False:  # noqa: E712  (UNKNOWN やその他)
        reasons.append("信用規制の有無が UNKNOWN")

    # 7-10. NEW のハードリミット
    if category == "NEW":
        names = set(positions) | set(reserved_positions)
        if p.code not in names and len(names) >= limits.max_positions:
            reasons.append(f"保有 {len(positions)} + 予約中 {len(reserved_positions)} 銘柄が上限 {limits.max_positions} に到達")
        elif p.code in names and len(names) > limits.max_positions:
            reasons.append(f"保有・予約中 {len(names)} 銘柄が上限 {limits.max_positions} を超過")
        pos = positions.get(p.code)
        held_value = Decimal(_g(pos, "qty", 0)) * Decimal(str(_g(pos, "avg_price", 0))) if pos is not None else Decimal(0)
        held_value += Decimal(reserved_positions.get(p.code, 0))
        if equity <= 0:
            reasons.append(f"資産 {equity} 円が 0 以下")
        elif (held_value + Decimal(reserve)) / Decimal(equity) > Decimal(str(limits.max_weight_per_name)):
            reasons.append(f"銘柄集中度 ({held_value}+{reserve})/{equity} > {limits.max_weight_per_name}")
        if new_sent_today >= limits.max_new_per_day:
            reasons.append(f"本日の新規通知 {new_sent_today} 件が上限 {limits.max_new_per_day} に到達")
        if stop_flag:
            reasons.append("STOP 中は新規候補を通知しない")
        if unresolved_unconfirmed:
            reasons.append("未確認の取引が残っているため新規候補を停止")
        if peak_equity > 0 and Decimal(equity) / Decimal(peak_equity) - 1 <= -Decimal(str(limits.drawdown_stop)):
            reasons.append(f"ドローダウン {equity}/{peak_equity} が上限 {limits.drawdown_stop} に到達")
        dse = _g(ledger_view, "day_start_equity", None)
        pnl = _g(ledger_view, "daily_pnl", 0) or 0
        if dse and Decimal(pnl) / Decimal(dse) <= -Decimal(str(limits.daily_loss_stop)):
            reasons.append(f"当日損益 {pnl}/{dse} が日次損失上限 {limits.daily_loss_stop} に到達")

    return GateResult(allowed=not reasons, category=category, reasons=reasons,
                      reserve_amount=reserve if not reasons else reserve, notes=notes)
