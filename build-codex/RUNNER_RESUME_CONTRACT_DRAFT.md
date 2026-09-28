# 異常終了・PC 停止からの明示的な再開 — 最小契約案（工程 5、D16-01〜06）

2026-09-29。Claude（Claude Code、`D:\work\ai-trader`、Fable 5.1、サブエージェント 0 体）作成。共通指示.md のキュー #6。採否と実装は Codex。本書は未決契約の整理で、実装・試験実行・公開は行っていない。製品・既存契約・試験・共通仕様は変更していない。

読んだもの: [RUNNER_RECOVERY_PLAN.md](RUNNER_RECOVERY_PLAN.md)（状態表・照合・時刻・診断の追記まで全文）、[RUNNER_RECOVERY_CONTRACT_PROPOSAL.md](RUNNER_RECOVERY_CONTRACT_PROPOSAL.md)（Codex の 3 契約案、全文）、`aitrader/runner.py`（`_prior_candidate`・`run` の全経路・journal schema）、`aitrader/runner_diagnostics.py` の分類、`ops/aitrader_ops/ledger.py`（`_on_notice_state`・`notice()`・`proposal()`・`_new_notice`・history）、`ops/aitrader_ops/models.py`（`reserve_amount`）、`COMPLETION_ROADMAP.md` 工程 5。

## 結論

**Codex 提案の 3 契約（所有 receipt・除外 view・締切後修復）のうち、ops を変えずに今すぐ実装できる「Tier A」を切り出し、INTENT/CREATED と INTENT/APPROVED の 2 状態だけを、同じ run_id・同じ入力・執行日当日・締切前に限って明示的に再開できるようにする。** 根拠は工程 3 で確定した T1（通知は runner だけが作る）と、台帳の公開 API だけで得られる証拠（`proposal`・`notice` の履歴・予約額・約定なし）。台帳 receipt API（Tier B）は ops 変更が許可された時点で置き換える。それ以外の状態は従来どおり停止（予約保持）し、人が診断結果を見て判断する。

| 判断 | 採否案 | 要点 |
|---|---|---|
| D16-01 対象と前提 | 採用 | 対象は journal `INTENT` × 台帳 `CREATED` / `APPROVED` の 2 状態。同 run_id・同 request_hash・執行日当日・`manifest.started_at ≤ now < 07:15`・STOP でない。他は `NEEDS_RECONCILIATION` で停止（予約保持） |
| D16-02 所有の証拠（Tier A） | 採用 | T1 の下で、journal 行（owner・hash）と台帳 `proposal`/`notice` の 6 項目一致を所有の証拠とする。1 つでも違えば停止。Tier B（台帳 receipt）は将来 |
| D16-03 二重評価の回避 | 採用 | `Ledger.view()` から**自分の通知の予約だけ**を引いた `LedgerView` の写しを build-codex 側で作り gate へ渡す。seq を前後で照合し、進んでいれば書かずに停止 |
| D16-04 再開の操作 | 採用 | CREATED: 再評価して許可なら `APPROVED` → journal 1 トランザクション（outbox＋candidates）。不許可なら**何も書かず**停止。APPROVED: 再評価せず、締切・STOP・二承認の再確認だけして journal 反映 |
| D16-05 記録 | 採用 | `manifest.resumes[]` と candidates の `result` に元判定・再開時刻・再開前状態を別欄で残す。元の時刻を書き換えない |
| D16-06 限界と操作手順 | 採用 | 電源断・外部書込・WAL は保証外として明記。人の手順は 診断 → 条件一致なら同 run_id で再実行 → それ以外は手動照合 |

## 1. D16-01 対象と前提条件

現行 `run()` は候補ごとに「journal に INTENT → `create_notice`（CREATED）→ `set_notice_state(APPROVED)` → journal 1 トランザクション（outbox INSERT＋candidates=APPROVED）」の順で進む。台帳と journal は別 DB で原子性がないため、中断は次の 3 か所で「片側保存」を残す。

| 中断点 | journal | 台帳 | 現行の挙動 | 本契約 |
|---|---|---|---|---|
| C1: INTENT 保存後・`create_notice` 前 | INTENT | なし | 同 run 再実行で再評価（既存経路） | 変更なし |
| C2: `create_notice` 後・`APPROVED` 前 | INTENT | CREATED（予約あり） | 「台帳だけに通知が存在します」で停止 | **再開対象** |
| C3: `APPROVED` 後・journal トランザクション前 | INTENT | APPROVED（予約あり） | 同上 | **再開対象** |
| その他（SENT 以降、取消・約定あり、通知あり INTENT なし、outbox 欠損、別 run、hash 差） | — | — | 停止 | 停止のまま（`NEEDS_RECONCILIATION`） |

前提条件（すべて満たさなければ書込なしで停止し、理由を返す）:

| 条件 | 理由コード |
|---|---|
| 同じ `run_id`、`runs.hash == request_hash`（入力不変。既存検査） | `RUN_INPUT_CHANGED` |
| `manifest.calendar_hash`・`marks` 不変（既存検査） | `REFERENCE_CHANGED` |
| `now.date() == execution_day` かつ `manifest.started_at ≤ now < 07:15 JST` | `RESUME_WINDOW_CLOSED` |
| 管理 STOP が有効でない（`inspect_managed_stop(home, now)`。不明も不可） | `STOP_ACTIVE` / `STOP_STATE_UNKNOWN` |
| 対象候補の journal 行が `state='INTENT'`、`owner == run_id`、`hash == digest(proposal)`、`day`・`side` 一致（既存 `_prior_candidate`） | 既存の `RunError` |
| 未確認約定・保留行がない（`ledger.unconfirmed(now)`・`pending_rows()` が空）。既存 gate の `unresolved` と同じ扱い | `UNRESOLVED_LEDGER` |

## 2. D16-02 所有の証拠（Tier A、ops 変更なし）

信頼前提 T1（通知の作成・状態変更は runner の直列化ロック内でのみ行う。runner 外の直接書込は運用で禁止）の下で、次の **6 項目がすべて一致**するとき「この INTENT が作った通知」とみなす。台帳の公開 API `proposal(pid)`・`notice(pid)`・`policy()` だけを使う。

| # | 証拠 | 判定 |
|---|---|---|
| E1 | `digest(normalize_proposal(ledger.proposal(pid))) == candidates.hash` | 候補全体（14 項目＋events）が一致 |
| E2 | `notice.history[0] == ('CREATED', t0)` かつ `manifest.started_at ≤ t0 ≤ now` | この run の開始後に作られた |
| E3 | `notice.notice_state ∈ {CREATED, APPROVED}`、`history` の長さは CREATED なら 1、APPROVED なら 2 で `history[1] == ('APPROVED', t1)`、`t0 ≤ t1 ≤ now` | 後続操作がない |
| E4 | `notice.filled_qty == 0`、`fills == []`、`trade_state == 'UNCONFIRMED'` | 約定・発注報告がない |
| E5 | BUY: `notice.reserve == reserve_amount(qty, limit_price, policy.fee_margin)`／SELL: `notice.reserved_shares == qty` | 予約が提案どおり |
| E6 | 同銘柄・同 run の他の INTENT 候補が台帳に通知を持たない（1 候補 = 1 通知） | 二重作成でない |

1 つでも違えば `OWNERSHIP_UNPROVEN` で停止（予約は保持、書込なし）。これは暗号学的な所有証明ではなく、T1 の下での整合検査である（Codex 提案 §1 の却下理由「後付け owner」とは異なり、**所有を書き足さない**。証拠が揃わなければ止まるだけ）。

Tier B（将来）: Codex 提案 §1 の「台帳と同一トランザクションの操作受領記録（operation_id・receipt）」。ops の schema/API 変更が許可された時点で E1〜E6 を receipt 照合に置き換える。それまで Tier A を mock/managed-v1 home に限定して使う。

## 3. D16-03 二重評価の回避（除外 view の写し）

C2 の再評価では、自分の通知の予約が `Ledger.view()` に含まれているため、そのまま gate に渡すと余力・予約銘柄・売却予約株数を二重に数える。ops の view API を足す代わりに、build-codex 側で**読取専用の写し**を作る。

```text
adjusted = LedgerView(
  cash = view.cash,
  reserved = view.reserved − notice.reserve,
  available = view.available + notice.reserve,
  positions = view.positions,                                  # 変更しない
  reserved_positions = view.reserved_positions から code の寄与 notice.reserve を引く（0 なら削除）,
  reserved_shares    = view.reserved_shares から code の寄与 notice.reserved_shares を引く（0 なら削除）,
  daily_pnl / day_start_equity = 元の実状態から計算した値（評価資産に予約除外を反映しない）
)
```

- 引いた結果が負、または `reserved_positions[code] < notice.reserve` 等なら `RESERVATION_MISMATCH` で停止（Codex 提案 §2 の「負の残額や不整合は拒否」）。
- 予約額は Proposal から再計算して置き換えず、`notice()` の現在値を使う（同 §2）。
- **seq の照合**: `seq_before = ledger.seq()` を `view()` の直前に取り、gate 判定後・`set_notice_state` の直前に `ledger.seq() == seq_before` を確認する。進んでいれば書かずに `SEQ_CHANGED` で停止。台帳側の比較更新（compare-and-set）は ops 変更になるため Tier B。
- 日次枠 `slots` は既存 SQL（`pid<>対象`）で自分を一度だけ数える。二承認の再検証は `run()` と同じ規則（同 run_id・mock・受信が執行日・`now` 以前・締切前・INVALID なし）。
- 純粋 gate（`ops.gate.evaluate`）は変更しない。台帳内部属性を gate に見せない。

## 4. D16-04 再開の操作

| 状態 | 手順 | 書込 |
|---|---|---|
| C2: INTENT / CREATED | 前提条件 → E1〜E6 → 除外 view で `evaluate` ＋ STOP/枠/二承認の追加判定（`run()` と同じ `extra`）→ 許可なら seq 照合 → `set_notice_state(APPROVED, now)` → journal 1 トランザクション（outbox INSERT、candidates を APPROVED、`result` に再開記録） | 台帳 1 イベント、journal 1 トランザクション |
| C2 で不許可 | **何も書かない**（予約保持）。`candidates` は INTENT のまま。`failure.json` に `NEEDS_RECONCILIATION` と理由（gate の reason_codes）を残す | なし |
| C3: INTENT / APPROVED | 前提条件 → E1〜E6 → 二承認の再検証と締切・STOP の再確認のみ（gate 再評価はしない。既承認の履歴を消さない）→ journal 1 トランザクション | journal のみ |
| 上記以外 | 停止。既存メッセージ「台帳だけに通知が存在します。照合が必要です」を `NEEDS_RECONCILIATION` と理由コードに置き換える（文言は Codex） | なし |

- `create_notice` は再開経路で**絶対に呼ばない**（予約の取消・再作成も禁止。Codex 提案 §2 の却下案）。
- 締切（07:15）以後の INTENT は再開しない（Codex 提案 §3 初版どおり）。翌日に残った予約の扱いは別契約（`HISTORY_RECONCILED` 案）で、本契約では停止・保持のみ。
- 同 run で複数候補が混在する場合、候補ごとに上記を適用し、1 候補でも `NEEDS_RECONCILIATION` になれば **その候補以降を処理せず** run を停止する（部分再開で日次枠がずれるのを避ける）。既に完了した候補（APPROVED/REJECTED）は既存の fast path どおり参照のみ。

## 5. D16-05 記録

- `manifest.resumes = [{at, from_states: {pid: 'CREATED'|'APPROVED'}, seq_before, seq_after}]` を追記（`started_at`・`initial_ledger_seq` は不変）。
- candidates の `result` の item に `resume = {at, from_state, gate_reevaluated: bool, original_created_at: t0}` を追加。元の `gate` は上書きせず、C2 の再評価結果は `gate` を置き換えるが `resume.original_gate` に元判定があれば保持（C2 では元判定は無い＝INTENT の保存判定）。
- `_artifacts` は既存どおり再生成。`failure.json` は停止時のみ。
- 監査: 再開で作る台帳イベントは `NOTICE_STATE`（APPROVED）1 件のみ。journal は outbox 1 行＋candidates 1 行更新。

## 6. D16-06 限界と操作手順

保証しないもの（明記）:
- **電源断**: journal は SQLite の既定ロールバック journal で文単位・トランザクション単位の原子性はあるが、台帳と journal を跨ぐ原子性はない（本契約はそれを前提に片側保存を扱う）。ディレクトリ耐久・ストレージの書込順序は保証外。
- **外部書込**: T1 違反（runner 外で通知や約定を書く）は E1〜E6 と seq 照合で**多くは**止まるが、全てを検出する保証はない（工程 3 と同じ「非保証」）。
- **稼働中 DB**: 診断（`runner_diagnostics`）は WAL/SHM がある DB を読まない。再開は runner のロック内で行うため診断とは別経路。
- **legacy home / 共有ロック不参加の writer**: 対象外（RUNNER_RECOVERY_PLAN §時刻・停止と同じ）。

人の操作手順（則光向け。実 API・実通知は関係しない）:

1. 停止した run の診断を実行する（停止中 DB のみ）: `python -m aitrader.runner_diagnostics --home <home> --run-id <run_id> --day <YYYY-MM-DD>`。
2. 分類が `NEEDS_RECONCILIATION` で、候補別の理由が「台帳片側保存（CREATED/APPROVED、約定なし）」だけなら、**同じ run_id・同じ入力ファイル**で runner を再実行する（`run()` の再開経路が D16-01〜05 を適用）。締切 07:15 を過ぎていれば再開しない。
3. 分類が `CONFLICT`・`MISSING`、または理由に約定・取消・外部注文・hash 差が含まれる場合は再開せず、台帳の照合（人手）へ。予約は解放しない。
4. 再開後、`result.json` の `resume` 欄と `manifest.resumes` を確認し、outbox に同 key が 1 件だけであることを確認する。

## 7. 固定例（人工）

- **C2 再開・許可**: 09/29 07:05 に run `r1` が候補 A（BUY 100 株、指値 1000、fee_margin 0.002 → 予約 100,200 円）を INTENT 保存後 CREATED まで進んで中断。07:08 に同 run_id・同入力で再実行。E1〜E6 一致、除外 view で `available` が元に戻り、gate 許可、seq 不変 → `APPROVED`、outbox 1 件、`resume.from_state='CREATED'`。台帳イベントは `NOTICE_STATE` 1 件だけ増える。
- **C2 再開・不許可（出金）**: 同上だが中断後に出金で余力不足 → 除外 view でも `INSUFFICIENT_AVAILABLE` → 書込なし、INTENT/CREATED のまま、`NEEDS_RECONCILIATION`（予約 100,200 円は保持）。
- **C3 再開**: APPROVED まで進んで中断 → 07:10 再実行 → gate 再評価なし、二承認・締切・STOP を再確認 → journal 反映のみ。台帳イベント増加 0。
- **締切後**: 07:16 の再実行 → `RESUME_WINDOW_CLOSED`、書込なし。
- **外部書込の疑い**: 中断中に台帳へ同 pid の SELL 通知が外部作成された（E6 違反）→ `OWNERSHIP_UNPROVEN`、書込なし。

## 8. 反証表（実装時。上限 30 件目安）

| # | 条件 | 期待 |
|---|---|---|
| 1 | C2 で例外注入 → 同 run 再実行 | APPROVED・outbox 1 件・`create_notice` 呼出 0・予約増分 0 |
| 2 | C3 で例外注入 → 同 run 再実行 | journal のみ反映・台帳イベント増加 0 |
| 3 | C1 で例外注入 → 再実行 | 既存経路（再評価して create_notice 1 回） |
| 4 | 再実行を 2 回繰り返す | 2 回目は完了結果の参照のみ。outbox・イベント不変 |
| 5 | 別 run_id で再実行 | `RunError`（既存）、書込なし |
| 6 | 同 pid・内容違いの candidates.hash | `RunError`（既存） |
| 7 | 中断中に通知が SENT へ進んでいた | `NEEDS_RECONCILIATION`、書込なし |
| 8 | 中断中に部分約定が入った | 同上（E4） |
| 9 | 中断中に外部で同 pid の別通知（E6） | `OWNERSHIP_UNPROVEN` |
| 10 | 予約額が `reserve_amount` と異なる（E5） | `OWNERSHIP_UNPROVEN` |
| 11 | `history[0].at < manifest.started_at`（E2） | `OWNERSHIP_UNPROVEN` |
| 12 | 07:15 ちょうど／07:14:59 の再開 | 閉／開 |
| 13 | STOP 有効／不明 | `STOP_ACTIVE` / `STOP_STATE_UNKNOWN`、書込なし |
| 14 | 出金で余力不足 | 不許可、書込なし、予約保持 |
| 15 | 他候補の予約が同銘柄にある | 除外 view で自分の分だけ引かれ、他候補分は残る |
| 16 | SELL 候補の C2（売却予約株数の除外） | `reserved_shares` から自分の分だけ引く |
| 17 | seq が gate 後・書込前に進む | `SEQ_CHANGED`、書込なし |
| 18 | 日次枠: 自分を含めて上限ちょうど | 許可（自分は一度だけ数える） |
| 19 | 二承認の片方欠落／INVALID／未来受信 | 不許可（`REVIEW_INCOMPLETE`/`REVIEW_INVALID`） |
| 20 | 未確認約定あり | `UNRESOLVED_LEDGER` |
| 21 | 複数候補で 2 件目が `NEEDS_RECONCILIATION` | 1 件目は反映済み、2 件目以降未処理、run は停止 |
| 22 | `manifest.resumes` と `result.resume` の記録 | 元時刻不変、再開時刻・前状態が記録される |
| 23 | 診断 → 再開 → 診断 | 再開後の診断が `COMPLETE` |
| 24 | outbox 同 key が既にある（C3 で outbox だけ先に書けていた仮定） | トランザクション失敗で停止、二重通知なし |

## 9. 未確定として残すもの

- 停止メッセージと理由コードの最終文言（既存の日本語文言との整合は Codex）。
- C3 で gate を再評価しない判断（Codex 提案どおり）。STOP・締切・二承認のみ再確認する範囲で足りるか。
- 翌日に残った INTENT/CREATED 予約の扱い（`HISTORY_RECONCILED` 案）。本契約では停止・保持。
- Tier B（台帳 receipt）へ移行する時期と ops 変更の許可。

## 10. 範囲外

自動再開（見張りからの再実行）、予約の自動解放、締切後の履歴修復、legacy home、実通信、通知キュー側（`reconcile_prepared_mock`）の変更。

更新時刻: 2026-09-29 08:46 JST。保存のみ・未公開。

## 11. Codex 採否（2026-09-29、依頼 34a88788）

**現案は不採用（再開経路は未実装）。不足点は停止条件の適用対象の1点。** Tier A の所有照合、予約除外 view、C2/C3 の状態遷移自体は、ops・gate を変えず実装可能。以下を整合させた改訂案で再判定する。工程5や案件全体の完了を意味しない。

### 不足点 D16-R1：SELL の停止条件が既存契約と不一致

§1 は STOP・未確認約定・pending を方向によらない再開禁止条件とし、§4 の C3 も STOP を必須にしている。一方、共通仕様フェーズ2 §4.1(8)(9)、gate.py の NEW/EXIT 分岐、runner.py の BUY 専用 STOP 再確認、および RUNNER_RECOVERY_PLAN.md「時刻・停止と未決事項」は、NEW を停止し EXIT は継続対象とし、復旧だけに独自の停止仕様を入れないとしている。

例：managed-v1 の SELL が INTENT/CREATED で中断し、保有・予約・二承認・時刻が正常なまま管理 STOP が有効になった場合、D16 は STOP_ACTIVE で必ず停止するが、既存の EXIT 規則では STOP 自体は不許可理由にならない。未確認注文についても同じ適用対象差がある。これはメッセージの修正ではなく、再開可否の変更である。

採用推奨は STOP_ACTIVE / STOP_STATE_UNKNOWN / UNRESOLVED_LEDGER を BUY に限定し、SELL は所有・履歴・時刻・二承認・売却可能株数等を検証する形。今回固定された「STOP なし」を Codex が無言で BUY 限定へ読み替えず、Claude に D16-01/04 と反証表の整合を求める。現行の停止動作は変更しない。

### §9 の4項目への判断

1. **理由の表現**：既存 RunError と failure.json の `status=SYSTEM_ERROR` を維持し、再開拒否の `detail` を `NEEDS_RECONCILIATION: <reason_code>` とする案を採用推奨。読取診断の `classification` と例外成果物の `status` は別物。D16 は failure.json のトップレベル status を明記していないため、外側 except の変更は必須ではなく、これを独立した不採用理由には数えない。入力・参照データの既存エラー文言は維持。優先順は既存入力検査、対象/所有、時刻、BUY停止/未照合、二承認/再評価、seq照合。
2. **C3 の再評価**：gate 全体の再評価なしを採用推奨。保存された元の許可判定を維持し、両 judge が各1件、同候補/hash、APPROVE、同run/mock、執行日受信、未来でなく締切前であることを再確認する。時刻・所有・履歴・seq と BUY の停止/未照合も確認する。STOP/未照合の方向別適用は D16-R1 の整合が前提。
3. **翌日予約**：停止・保持を採用。HISTORY_RECONCILED、自動解放、締切後 outbox 復元は未採用。
4. **Tier B**：今回移行しない。ops の receipt 保存・比較更新 API が別依頼で許可・実装・検証され、新規 mock run の移行契約が定まった後に再判定する。日時を推測しない。旧 run へ所有 receipt を補完しない。

### 実装可能性確認と、改訂時に含める局所整合

- E1〜E5 は公開 proposal/notice/policy で確認できる。history は `(state, ISO日時文字列)` の列なので aware() で比較する。E6 は本文表どおり「同runの他INTENT・同銘柄」の通知照会なら実装可能。全台帳の他通知がない証明ではない。別runやAPPROVEDの同銘柄予約は除外 view に残す。同pidの別SELL通知という固定例は Ledger が重複pidを拒否するため、別pid・同銘柄の他INTENTへ直す。
- T1 に依存する整合照合であり、receipt や原子的比較更新の代替保証ではない。seq は所有情報の読取前から採り、C2のAPPROVED書込直前、C3のjournal反映直前に照合する。非協調 writer の競合窓は残る。旧 Codex 提案の台帳内比較更新要件を緩める Tier A の限定であることを明記する。
- 除外 view は公開 LedgerView の写しとして実装できる。元view・実保有・評価資産を変更せず、自分の現在予約だけを差し引く。負値/不足は拒否。他通知の予約は維持。
- `manifest.resumes` は成果物JSONだけでなく `runs.manifest` に保存する必要がある。C2/C3 の同一journalトランザクションを outbox INSERT＋candidates UPDATE＋runs.manifest UPDATE と明記する。途中失敗で再開記録だけ進めない。
- INTENT には既に元の allowed gate 判定がある（runner.py の item 保存）。「C2では元判定は無い」は訂正し、C2は resume.original_gate に保存、C3は元gateを保持する。
- failure.json は失敗時だけ作られる既存ラッパーを維持し、成功再試行後に残る過去failureを現在の失敗と解釈しない。診断の COMPLETE は単純な台帳履歴の正常例に限定して試験する。ADJUST等を含む履歴は現診断が NEEDS_RECONCILIATION とするため、再開成功を理由に診断を弱めない。
- 既存の「台帳だけ」assert は test_runner.py:161（INTENTなし）、:184（legacy C2）、test_runner_crash_boundaries.py:77（legacy C3）。managed-v1限定なら維持可能。今回は変更箇所0、期待値変更0。

確認方法：Windows上のコード・契約の読取照合。製品・試験変更なし、pytest・Windows動作実測・全体試験は未実施（不採用時は依頼1と5のみ）。親が採否・文書、Sol 1体が読取実装可能性確認を担当。Claude独立確認済みとは扱わない。設計書は D:/work 直上には無く、従来の 999_投資関係 フォルダにある実在版の STOP/手仕舞い・12章を参照した。

依頼hash: 34a887889a1d8feea9bc9637feeff437671c2a115d30f2922eede88c9864a6aa。
