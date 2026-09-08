"""通知ゲート（共通仕様 フェーズ2 §4）

evaluate() は純粋関数。入力はすべて引数で渡し、DB・ネットワーク・LLM に触れない。
不許可理由は途中で打ち切らず全部列挙する（設計書 6.1「不許可理由の文面が読んで分かる」）。

解釈（README にも記載）:
- 執行日 = expires_at（当日 08:59 JST）の JST 日付。決算回避は「暦日差 <= N」または「営業日差 <= N」のどちらかで該当すれば不許可
  （両方で数えて小さい方を採用 ＝ どちらか一方より必ず保守的。R18。暦日だけでは金→月が3暦日で通ってしまう）。
  営業日は `business_days`（任意引数。Iterable[date]）があればそれを、無ければ月〜金を営業日とみなす。
- SELL（EXIT）は現物の保有株のみ。既存の売却予約株数（ledger_view.reserved_shares）を控除した売却可能株数で判定（R19）。現金予約は 0。STOP / 未確認 / 日次件数 / 日次損失 / DD の各制限は NEW のみに適用。
  ただし二重承認・期限・契約検査・保有数検査は EXIT にも必須。
- 既存保有額は positions[code].qty × avg_price（ビューに時価がないため簿価）。
- 銘柄数 = 保有銘柄 ∪ 予約中銘柄。候補が新しい銘柄なら +1 して max_positions と比較。
- packet_hash は p から再計算し、審査後に改変された Proposal を拒否する（ISSUES #10）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .limits import Limits, D, daily_loss_hit, drawdown_hit, weight_exceeded
from .models import JST, compute_packet_hash, reserve_amount

REQUIRED_JUDGES = ("claude", "codex")


@dataclass
class GateResult:
    allowed: bool
    category: str                      # "NEW" | "EXIT"
    reasons: list[str] = field(default_factory=list)
    reserve_amount: int = 0
    notes: list[str] = field(default_factory=list)   # confidence / risks の転記（判定には使わない）
    reason_codes: list[str] = field(default_factory=list)


def _get(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _contract_errors(p) -> list[str]:
    errs = []
    if _get(p, "side") not in ("BUY", "SELL"):
        errs.append(f"side が BUY/SELL 以外: {_get(p, 'side')!r}（現物限定）")
    if _get(p, "exec_condition") != "OPENING_LIMIT":
        errs.append(f"執行条件が寄付指値以外: {_get(p, 'exec_condition')!r}")
    if _get(p, "account_type") != "CASH":
        errs.append(f"口座区分が現物以外: {_get(p, 'account_type')!r}")
    qty, lot = _get(p, "qty"), _get(p, "lot_size")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        errs.append(f"数量が正の整数でない: {qty!r}")
    if not isinstance(lot, int) or isinstance(lot, bool) or lot <= 0:
        errs.append(f"売買単位が正の整数でない: {lot!r}")
    elif isinstance(qty, int) and not isinstance(qty, bool) and qty > 0 and qty % lot != 0:
        errs.append(f"数量 {qty} が売買単位 {lot} の倍数でない")
    lp = _get(p, "limit_price")
    if not _is_finite_number(lp) or D(lp) <= 0:
        errs.append(f"指値が正の有限な数でない: {lp!r}")
    try:
        if compute_packet_hash(p) != _get(p, "packet_hash"):
            errs.append("packet_hash が内容と一致しない（審査後に改変された候補）")
    except Exception as exc:  # noqa: BLE001
        errs.append(f"packet_hash を再計算できない: {exc}")
    return errs


def _verdict_errors(p, verdicts) -> list[str]:
    errs = []
    by_judge: dict[str, list] = {}
    for v in verdicts or []:
        by_judge.setdefault(str(_get(v, "judge")), []).append(v)
    unknown = [j for j in by_judge if j not in REQUIRED_JUDGES]
    if unknown:
        errs.append(f"想定外の審査者: {unknown}")
    for judge in REQUIRED_JUDGES:
        vs = by_judge.get(judge, [])
        if len(vs) != 1:
            errs.append(f"{judge} の応答がちょうど1件でない（{len(vs)}件）")
            continue
        v = vs[0]
        if _get(v, "proposal_id") != _get(p, "proposal_id"):
            errs.append(f"{judge} の応答が別候補宛て: {_get(v, 'proposal_id')!r}")
        if _get(v, "packet_hash") != _get(p, "packet_hash"):
            errs.append(f"{judge} の応答の packet_hash が候補と一致しない（旧版または改変）")
        if _get(v, "decision") != "APPROVE":
            errs.append(f"{judge} が APPROVE でない: {_get(v, 'decision')!r}")
    return errs


def _event_errors(p, execution_day: date, limits: Limits, business_days=None) -> list[str]:
    errs = []
    events = _get(p, "events")
    if not isinstance(events, dict):
        return ["events が辞書でない（決算・規制情報なし）"]
    if "next_earnings_date" not in events:
        errs.append("次回決算日の情報がない（UNKNOWN 扱いで保留）")
    else:
        ne = events["next_earnings_date"]
        if ne == "UNKNOWN":
            errs.append("次回決算日が UNKNOWN（情報不足のため保留）")
        elif ne is not None:
            if isinstance(ne, str):
                try:
                    ne = date.fromisoformat(ne)
                except ValueError:
                    errs.append(f"次回決算日の形式が不正: {ne!r}")
                    ne = None
            if isinstance(ne, datetime):
                ne = ne.date()
            if isinstance(ne, date):
                cal_days = abs((ne - execution_day).days)
                biz_days = business_days_between(execution_day, ne, business_days)
                if min(cal_days, biz_days) <= int(limits.earnings_blackout_days):
                    errs.append(f"決算発表日 {ne.isoformat()} が執行日 {execution_day.isoformat()} の ±{limits.earnings_blackout_days} 営業日以内（暦日 {cal_days}・営業日 {biz_days}）")
            elif not isinstance(ne, date) and ne is not None:
                errs.append(f"次回決算日の型が不正: {ne!r}")
    if "margin_regulated" not in events:
        errs.append("信用規制の情報がない（UNKNOWN 扱いで保留）")
    else:
        mr = events["margin_regulated"]
        if mr == "UNKNOWN":
            errs.append("信用規制の有無が UNKNOWN（情報不足のため保留）")
        elif mr is True:
            errs.append("信用規制銘柄")
        elif mr is not False:
            errs.append(f"信用規制フラグの型が不正: {mr!r}")
    return errs


def _is_finite_number(v) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
        return False
    try:
        return Decimal(str(v)).is_finite()
    except Exception:  # noqa: BLE001
        return False


def business_days_between(a: date, b: date, business_days=None) -> int:
    """a と b の間の営業日数（a < b のとき b までに含まれる営業日の数。同日は 0）"""
    if a > b:
        a, b = b, a
    if business_days is not None:
        days = {d if isinstance(d, date) and not isinstance(d, datetime) else d.date() for d in business_days}
        return sum(1 for i in range(1, (b - a).days + 1) if (a + timedelta(days=i)) in days)
    return sum(1 for i in range(1, (b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)


def evaluate(p, verdicts, now: datetime, ledger_view, limits: Limits, equity, peak_equity,
             new_sent_today: int, stop_flag: bool, unresolved_unconfirmed: bool,
             business_days=None) -> GateResult:
    reasons: list[str] = []
    notes: list[str] = []
    side = _get(p, "side")
    category = "EXIT" if side == "SELL" else "NEW"

    # 1) 契約検査（数量・単元・執行条件・口座区分・ハッシュ）
    reasons += _contract_errors(p)

    # 2) 二重承認（同一 proposal_id / packet_hash、claude と codex がちょうど1件ずつ）
    reasons += _verdict_errors(p, verdicts)
    for v in verdicts or []:
        conf, risks = _get(v, "confidence"), _get(v, "risks")
        notes.append(f"{_get(v, 'judge')}: confidence={conf} risks={list(risks or [])}")

    # 3) 期限（now <= expires_at。tz-aware 同士で比較）
    expires_at = _get(p, "expires_at")
    try:
        if now.tzinfo is None or expires_at.tzinfo is None:
            reasons.append("now / expires_at にタイムゾーンがない")
            execution_day = expires_at.date() if isinstance(expires_at, datetime) else date.today()
        else:
            if now > expires_at:
                reasons.append(f"有効期限切れ（expires_at={expires_at.isoformat()}, now={now.isoformat()}）")
            execution_day = expires_at.astimezone(JST).date()
    except AttributeError:
        reasons.append("expires_at が datetime でない")
        execution_day = date.today()

    # 4) 構造化イベント検査（決算・規制。UNKNOWN は保留）
    reasons += _event_errors(p, execution_day, limits, business_days)

    # 5) 資金・保有・ハードリミット
    positions = _get(ledger_view, "positions", {}) or {}
    reserved_positions = _get(ledger_view, "reserved_positions", {}) or {}
    code = _get(p, "code")
    qty = _get(p, "qty")
    reserve = 0
    if category == "EXIT":
        held = _get(positions.get(code), "qty", 0) if code in positions else 0
        reserved_sh = int((_get(ledger_view, "reserved_shares", {}) or {}).get(code, 0))
        sellable = held - reserved_sh
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0 or sellable < qty:
            reasons.append(f"売却可能 {sellable} 株（保有 {held} − 売却予約 {reserved_sh}）に対し売却数量 {qty} 株（現物限定・空売り不可）")
    else:
        if isinstance(qty, int) and not isinstance(qty, bool) and qty > 0 and _is_finite_number(_get(p, "limit_price")) and D(_get(p, "limit_price")) > 0:
            reserve = reserve_amount(qty, _get(p, "limit_price"), limits.fee_margin)
        available = _get(ledger_view, "available", 0)
        if available is None or D(available) < 0:
            reasons.append(f"余力が負（available={available}）")
        elif reserve > D(available):
            reasons.append(f"予約額 {reserve:,} 円が余力 {int(D(available)):,} 円を超える（他候補の予約を含む）")
        # 銘柄数（保有 ∪ 予約中）
        names = set(positions.keys()) | set(reserved_positions.keys())
        count_after = len(names) + (0 if code in names else 1)
        if count_after > int(limits.max_positions):
            reasons.append(f"銘柄数が上限 {limits.max_positions} を超える（保有＋予約中 {len(names)} ＋ 本候補）")
        # 集中度（簿価＋予約額）
        existing = 0
        if code in positions:
            pos = positions[code]
            existing = D(_get(pos, "qty", 0)) * D(_get(pos, "avg_price", 0))
        existing += D(reserved_positions.get(code, 0))
        if weight_exceeded(existing, reserve, equity, limits):
            reasons.append(f"1銘柄比率が上限 {limits.max_weight_per_name:.0%} を超える（既存 {int(existing):,} ＋ 予約 {reserve:,} / 資産 {equity}）")
        # 日次件数
        if int(new_sent_today) >= int(limits.max_new_per_day):
            reasons.append(f"本日の新規候補が上限 {limits.max_new_per_day} 件に到達（送信済 {new_sent_today}）")
        # 停止・未確認
        if stop_flag:
            reasons.append("STOP 中（新規候補は停止。手仕舞い候補・警告は継続）")
        if unresolved_unconfirmed:
            reasons.append("未確認の注文が残っている（口座照合が完了するまで新規候補は停止）")
        # 日次損失・ドローダウン
        if daily_loss_hit(_get(ledger_view, "daily_pnl", 0) or 0, _get(ledger_view, "day_start_equity", None), limits):
            reasons.append(f"当日損失が上限 {limits.daily_loss_stop:.0%} に到達（daily_pnl={_get(ledger_view, 'daily_pnl')}）")
        if drawdown_hit(equity, peak_equity, limits):
            reasons.append(f"ドローダウンが上限 {limits.drawdown_stop:.0%} に到達（equity={equity}, peak={peak_equity}）")

    return GateResult(allowed=not reasons, category=category, reasons=reasons,
                      reserve_amount=reserve if (category == "NEW" and not reasons) else (0 if category == "EXIT" else reserve),
                      notes=notes, reason_codes=[_reason_code(r) for r in reasons])


_CODE_RULES = [
    ("packet_hash", "PACKET_MISMATCH"), ("応答がちょうど1件でない", "REVIEW_INCOMPLETE"), ("想定外の審査者", "REVIEW_INVALID"),
    ("APPROVE でない", "REVIEW_NOT_APPROVED"), ("別候補宛て", "REVIEW_INVALID"), ("有効期限切れ", "PROPOSAL_EXPIRED"),
    ("タイムゾーン", "PROPOSAL_INVALID"), ("次回決算日", "EARNINGS_UNKNOWN"), ("決算発表日", "EARNINGS_BLACKOUT"),
    ("信用規制", "MARGIN_REGULATED"), ("events が辞書でない", "EVENTS_MISSING"), ("余力", "INSUFFICIENT_AVAILABLE"),
    ("予約額", "INSUFFICIENT_AVAILABLE"), ("銘柄数", "MAX_POSITIONS"), ("1銘柄比率", "CONCENTRATION"),
    ("新規候補が上限", "DAILY_NEW_LIMIT"), ("STOP", "STOP_NEW"), ("未確認", "RECONCILIATION_PENDING"),
    ("当日損失", "DAILY_LOSS_STOP"), ("ドローダウン", "DRAWDOWN_STOP"), ("売却可能", "INSUFFICIENT_SHARES"),
    ("side が", "CONTRACT_SIDE"), ("執行条件", "CONTRACT_EXEC"), ("口座区分", "CONTRACT_ACCOUNT"),
    ("数量", "CONTRACT_QTY"), ("売買単位", "CONTRACT_LOT"), ("指値", "CONTRACT_PRICE"),
]


def _reason_code(text: str) -> str:
    if "APPROVE でない" in text and "'INVALID'" in text:
        return 'REVIEW_INVALID'
    for key, code in _CODE_RULES:
        if key in text:
            return code
    return "OTHER"
