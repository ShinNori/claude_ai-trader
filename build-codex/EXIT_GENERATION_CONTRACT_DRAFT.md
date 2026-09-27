# 実保有からの SELL（EXIT）候補生成 — 最小契約案（工程 3、D15-01〜05）

2026-09-28。Claude（Claude Code、Fable 5.1、サブエージェント 0 体）作成。共通指示.md のキュー #3。採否と実装は Codex。本書は未決契約の整理で、実装・試験実行・公開は行っていない。製品・既存契約・試験・共通仕様は変更していない。

読んだもの: 設計書 §4.2〜4.4・§7.5、戦略カタログ v0.2 §1〜3、`common/共通仕様_フェーズ1.md`（holding_days=20）、`common/共通仕様_フェーズ2.md`（SELL は保有株のみ、gate 5・8）、`common/ISSUES.md` #8・#27、`PHASE2_INTEGRATION.md` §3〜4、`DATA_INPUT_READINESS.md` 候補生成の入力、`packet.py build_proposals`、`ops/aitrader_ops/gate.py`・`ledger.py`（`_recalc`・`_apply_fill`・`_assert_invariants`）・`models.py`。

## 結論

**BUY 生成（`build_proposals`）と対になる純粋関数 `build_exit_proposals` を新設し、入力は「台帳から導いた保有ロット」「前日終値」「単元」「営業日」「イベント」の検証済み値だけにする。** 戦略の出口ルールは A1 の「保有期間満了（20 営業日、取得日を 0 日目）」のみを対象にし、損切り（証券側逆指値）とトレーリング（B2）は今回生成しない。売却数量は予算から作らず、保有ロットの数量と売却可能株数から決める（ISSUES #8 の懸念に直接答える）。

| 判断 | 採否案 | 要点 |
|---|---|---|
| D15-01 保有ロットの由来 | 台帳の FILLED/PARTIAL な BUY 通知から導出（`entry_date` = 最初の約定 `effective_at` の JST 日付、`strategy`/`strategy_version`/`proposal_id` は通知の proposal から） | `Position` は銘柄ごとの合算（qty・avg_price）でロット情報を持たないため、通知単位に戻す。初期スナップショットの保有は `entry_date` 不明 → **生成対象外**（既定）。台帳を変更しない |
| D15-02 出口条件 | `as_of` が `scheduled_exit` 以上のロットを EXIT 候補にする。`scheduled_exit` = `entry_date` から `holding_days` 営業日後（取得日を 0 日目、検証済み `business_days` で数える） | 営業日は入力。カレンダーを推測しない。`business_days` が `scheduled_exit` まで届かなければ候補を作らず `INSUFFICIENT_CALENDAR` |
| D15-03 数量 | `qty = min(ロット残数量, 売却可能株数)` を単元で切り捨て。売却可能 = `positions[code].qty − reserved_shares[code]`。同銘柄に未決の SELL 通知があれば生成しない。未約定 BUY は加えない | 0 株なら候補にしない（理由付きで除外一覧へ）。1 銘柄 1 候補/日 |
| D15-04 売り指値 | `prev_close × (1 − 0.005)` を呼値の刻みで**切り上げ**、`OPENING_LIMIT`・`CASH`。期限は BUY と同じ翌営業日 08:59 JST | BUY は切り捨てで上限を守る。SELL は切り上げで下限を守る（対称） |
| D15-05 保存接続 | 生成入力（保有ロット・終値・単元・営業日・イベント）を 1 束の JSON にまとめ、`bundle_sha256`（生バイト）を `snapshot_id` に使う。保存は既存 `evidence_bundle_store.put` の**別 mode**として後続。今回は `snapshot_id` の意味だけ固定 | 保存 API の拡張は D13 の流儀に従い別依頼。研究経路（backtest）と混同しない |

## 1. 入力（すべて検証済み値。JSON 由来の素の値）

```text
build_exit_proposals(holdings, as_of, prev_close, lot_sizes, events, snapshot_id, policy_version,
                     holding_days, *, business_days, tz='Asia/Tokyo') -> ExitBuildResult
```

| 引数 | 形 | 検証 |
|---|---|---|
| `holdings` | list。要素は `{code, source_proposal_id, strategy, strategy_version, entry_date, lot_qty, held_qty, reserved_shares, open_sell_notice}` | code は `[0-9A-Z]{4,5}`。`lot_qty`/`held_qty`/`reserved_shares` は bool 以外の非負 int、`lot_qty ≤ held_qty`。`entry_date` は `as_of` 以下の日付。同 `(code, source_proposal_id)` の重複は拒否 |
| `as_of` | date | BUY と同じ |
| `prev_close` / `lot_sizes` / `events` | BUY と同じ dict | 欠損・非有限・非正は拒否（BUY と同文言） |
| `holding_days` | int ≥ 1 | 戦略 config から。A1 は 20 |
| `business_days` | 必須（BUY では任意だが EXIT では必須） | `entry_date` から `scheduled_exit` を数え、`as_of` の翌営業日を決める |

`holdings` は台帳から導くが、導出関数 `derive_holdings(ledger_view, notices)` は本契約の範囲外（Codex の実装判断。台帳 API は変更しない）。導出規則だけ固定する: BUY 通知のうち `filled_qty > 0` を 1 ロットとし、`lot_qty = filled_qty − その通知に紐づく既売却数`。既売却数は SELL 通知の `reason` ではなく `events` の `source_proposal_id`（本契約で新設、下記）で紐づける。紐づけ不能な売却（初期スナップショット・外部注文）は銘柄合計から FIFO で差し引く。

## 2. 出力

`ExitBuildResult = (proposals: list[Proposal], excluded: list[{code, source_proposal_id, reason_code}])`

- `Proposal` は既存 dataclass をそのまま使う。`side='SELL'`、`exec_condition='OPENING_LIMIT'`、`account_type='CASH'`、`strategy`/`strategy_version` はロット由来、`as_of`、`snapshot_id`、`policy_version`、`expires_at` = 翌営業日 08:59 JST、`reason` = `"保有期間満了: entry YYYY-MM-DD, 20営業日, scheduled_exit YYYY-MM-DD, lot <source_proposal_id>"`。
- `events` は BUY と同じ 2 キー（`next_earnings_date`, `margin_regulated`）に **`source_proposal_id` を加えた 3 キー**（`packet_hash` の対象外である `events` に置き、hash 契約を変えない）。
- `proposal_id` = `X1-{execution_day:%Y%m%d}-{code}-{seq:02d}`（`X` は EXIT、`1` は A1。BUY の `A1-…` と衝突しない）。並びは `(strategy, version, code, source_proposal_id)` の昇順で決定的。
- `excluded` の `reason_code` は閉じた語彙: `NOT_DUE`（満了前） / `NO_SELLABLE_SHARES`（売却可能 0） / `BELOW_LOT`（単元未満） / `OPEN_SELL_EXISTS`（同銘柄の未決 SELL） / `INSUFFICIENT_CALENDAR` / `MISSING_PRICE` / `MISSING_LOT_SIZE`。価格・単元の欠損は BUY と同じく**例外**にせず除外にする案と、BUY と同じ例外にする案があり、**除外（生成は続行、理由を残す）を推奨**。売り遅れを防ぐため 1 銘柄の欠損で全体を止めない。

## 3. 段と理由順（1 ロットごと）

1. `open_sell_notice` → `OPEN_SELL_EXISTS`（同銘柄 2 件目以降のロットも同じ）
2. `scheduled_exit` を算出できない → `INSUFFICIENT_CALENDAR`／`as_of < scheduled_exit` → `NOT_DUE`
3. `prev_close` 欠損 → `MISSING_PRICE`／`lot_sizes` 欠損 → `MISSING_LOT_SIZE`
4. `qty = floor(min(lot_qty, held − reserved) / lot) × lot`。0 → `NO_SELLABLE_SHARES`（`held − reserved ≤ 0`）または `BELOW_LOT`
5. 同銘柄の複数ロットが同日に満了 → **1 候補に合算**（`qty` は合算後に 4 を適用、`source_proposal_id` は最古のロット、`reason` に全ロット ID）。gate が 1 銘柄 1 候補を前提にしているため

## 4. 固定例（人工。営業日は入力として与える）

前提: `holding_days=20`、`business_days` = 2026-09-01〜2026-10-30 の平日（祝日なし、人工）、`as_of=2026-09-29`。

| ロット | entry_date | scheduled_exit（20 営業日後） | held/reserved | prev_close | 結果 |
|---|---|---|---|---|---|
| 7203 / A1-20260901-7203-01 | 2026-09-01 | 2026-09-29 | 100 / 0 | 2000 | 候補: qty 100、指値 1990（2000×0.995=1990、刻み 5、切り上げ不要） |
| 6857 / A1-20260908-6857-01 | 2026-09-08 | 2026-10-06 | 100 / 0 | 1234 | `NOT_DUE` |
| 9984 / A1-20260901-9984-02 | 2026-09-01 | 2026-09-29 | 200 / 200 | 3000 | `NO_SELLABLE_SHARES`（全量が売却予約中） |
| 7203 / A1-20260901-7203-01（同上） | — | — | — | — | 上と同一ロットの重複投入は入力検証で拒否 |

候補 1 件の固定値: `proposal_id=X1-20260930-7203-01`、`side=SELL`、`qty=100`、`lot_size=100`、`limit_price=1990.0`、`expires_at=2026-09-30T08:59:00+09:00`、`events={"next_earnings_date": null, "margin_regulated": false, "source_proposal_id": "A1-20260901-7203-01"}`。`packet_hash` は実装後に `packet_hash(p)` で確定し、期待値ファイルへ固定する（例から生成して上書きしない）。

切り上げの例: `prev_close=1234` → `1234×0.995=1227.83` → 刻み 5（1000〜5000）→ **1230**。`prev_close=999` → `994.005` → 刻み 1 → **995**。

## 5. 反証表（実装時。上限 30 件目安）

| # | 条件 | 期待 |
|---|---|---|
| 1 | 固定例 4 ロット | 候補 1・除外 2・入力拒否 1 |
| 2 | `as_of` = `scheduled_exit` ちょうど | 候補（当日満了は含む） |
| 3 | `as_of` = `scheduled_exit` の前営業日 | `NOT_DUE` |
| 4 | 祝日を含む `business_days`（人工で 1 日抜く） | `scheduled_exit` が 1 日後ろへずれる |
| 5 | `business_days` が `scheduled_exit` まで無い | `INSUFFICIENT_CALENDAR` |
| 6 | `held − reserved` が単元未満 | `BELOW_LOT` |
| 7 | 部分売却済みロット（`lot_qty < held`） | `qty = lot_qty` |
| 8 | 同銘柄 2 ロット同日満了 | 1 候補に合算、`reason` に両 ID |
| 9 | 同銘柄に未決 SELL | `OPEN_SELL_EXISTS`、候補 0 |
| 10 | 指値の刻み 3 帯（<1000 / ≤5000 / >5000）と切り上げ | 995 / 1230 / 5970（6000×0.995=5970）|
| 11 | `prev_close` 欠損・非正・NaN | `MISSING_PRICE`（例外にしない） |
| 12 | `events` 欠損 | BUY と同じ UNKNOWN 補完（gate 側で拒否） |
| 13 | `packet_hash` が `events` を含まない | `source_proposal_id` を変えても hash 不変 |
| 14 | 出力の決定性 | 同じ入力で同じ順序・ID |
| 15 | 入力不変 | `holdings`・`prev_close` を変更しない |
| 16 | 生成した SELL を `ops.gate.evaluate` に通す | EXIT として許可（保有 ≥ qty、STOP 中も許可） |
| 17 | 生成した SELL を `Ledger.create_notice` に通す | `reserved_shares[code] == qty`、二重売却の不変条件を満たす |
| 18 | `entry_date > as_of` | 入力拒否 |
| 19 | `lot_qty > held_qty` | 入力拒否 |
| 20 | 初期スナップショットの保有（ロット無し） | 生成対象外（`holdings` に現れない） |

## 6. 未確定として残すもの（Codex の判断か則光の決定）

- 初期スナップショットの保有をいつ・どう EXIT 対象にするか（`entry_date` を手入力で与える契約が別途必要）。
- `derive_holdings` の置き場所（ops 側か build-codex 側か）と、SELL 通知と BUY ロットの紐づけ（`events.source_proposal_id` を台帳側でも保持するか）。
- 売り未約定時の再通知（翌日に再生成するか、同 ID で再送か）。カタログ v0.2 が「確定してから v2」としている点。
- 保存接続（D15-05）の mode 名と 13 キー出力の流用可否。

## 7. 範囲外

損切り・利確・トレーリング（B2）の生成、時価評価、実データ取得、実口座・実通知、研究経路の変更、台帳 API の変更。

更新時刻: 2026-09-28 08:38 JST。保存のみ・未公開。

## Codex 採否（2026-09-28、対象 599f803 のローカル作業ツリー）

**現行案全体は不採用（契約修正後に再判定）。build_exit_proposals は実装しない。** 予算を使わない SELL 生成自体は実装可能だが、D15-01 と D15-03 の組合せでは売却後のロット残を一意に復元できない。以下はコード読取による確認で、実行試験・独立Claudeレビュー済みという意味ではない。

| 判断 | 採否と理由 |
|---|---|
| D15-01 | 不採用。A/B各100株を合算して200株売る場合、events.source_proposal_id=Aだけでは、記載された「その通知に紐づく既売却数」の控除はAを負数にする。Bへの配分、部分売却、訂正による配分復元が未定義。reasonの全IDを台帳根拠にすることも本案は禁止している。さらにledger.py:449–450のeffective_atは残高反映時刻で、保留APPLYでは解決時刻（:708）。取得日としてそのまま採用しない。 |
| D15-02 | 営業日入力・取得日0日目・満了当日を含む規則は採用可能。ただし取得日の確定が前提。複数日にまたがる部分約定を最初の日へ一括帰属するかも明示が必要。 |
| D15-03 | min(満了ロット残合計, held-reserved)を合算後に単元切捨てする数量規則は採用可能。各行のheld/reserved/open_sellの同銘柄整合、ロット残総和<=held、異なる戦略版の合算拒否、売却配分規則を追加するまで全体採用しない。 |
| D15-04 | 既存研究用の簡易呼値によるDecimal切上げと翌営業日08:59 JSTは採用可能。実取引所の呼値表を検証・採用した意味ではない。 |
| D15-05 | 生バイトSHA256をsnapshot_idに使う方向は採用可能。ただし文字列の指定だけでは入力束とロット配分の一致は検証されない。保存・検証接続は後続契約まで未採用。 |

既存境界: gate.evaluateは保有総数とreserved_sharesを検査し、Ledger.create_noticeも銘柄合計の不変条件を検査する。これらは「どの取得ロットが残るか」を保証しない。Proposal.events自体は台帳に保持可能（ledger.pyの_on_notice_created、proposal公開API）だが、packet.py HASH_FIELDSの14項目にevents/reasonは含まれない。既存hash契約を変更せず、将来は束照合を経た構造化配分を根拠にする必要がある。

### §6の4点に対する今回の判断

1. 初期スナップショット保有は対象外を採用。取得日を推測・補完しない。外部SELLのFIFO控除では、この対象外残高と通知由来残高の区別・順序も契約化する。
2. derive_holdingsは実装するならbuild-codex側の読取処理とし、ops APIは変更しない。今回は導出契約不足のため実装しない。合算SELLの全ロットと割当数量、部分約定への消費順、訂正・分割時の扱い、同一観測時点の入力集合を契約修正で固定する。取得時刻atと残高反映時刻effective_atは区別する。
3. 再通知は今回未採用。EXPIREDだけでは実取引の予約は解放されない（共通仕様フェーズ2§3.1）。未決SELLがある間は生成抑止し、取消・約定・訂正を確定した後の再生成ID/再送契約は別途確定する。
4. 保存mode名・13キー流用は今回決定しない（保存接続を明示的に未採用とする判断）。D13既存modeや出力を変更しない。ロット配分を含む入力schemaと検証契約を先に確定する。

次の技術工程はClaudeによる「ロット取得日と合算売却配分」の契約修正1点。製品・既存試験・common・opsは無変更のためpytestは未実施（passed/skipped/failed/秒は未測定）。加えてgit pullは.git/FETCH_HEADのPermission deniedで失敗。この実行環境では.gitが読取専用のためcommit/push完了不能。QUESTIONS.mdに環境復旧事項を記録し、今回の自動実行規則に従って相手宛てB更新・公開は停止する。採否記録の保存を案件全体完了とは扱わない。
