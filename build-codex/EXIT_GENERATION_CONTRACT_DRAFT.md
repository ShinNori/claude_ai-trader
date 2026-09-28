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


---

## 改訂 v2（2026-09-28、Codex 不採用への回答。本節が §1〜§3 と衝突する場合は本節を優先）

Codex の指摘 2 点（合算 SELL のロット別配分が未定義、`effective_at` は取得日に使えない）に答え、D15-01 と D15-03 を差し替え、D15-06〜07 を追加する。§6 の 4 点は Codex の判断（初期保有は対象外、導出は build-codex 側の読取専用、再通知は未採用、保存接続は未採用）をそのまま採る。

### D15-01（改）取得日と取得ロット

- **取得日** `entry_date` = そのロット（BUY 通知）の、取り消されていない（`reversed=false`）約定のうち **`at` が最も早いもの**の JST 日付。`effective_at`（残高反映時刻。保留 APPLY では解決時刻）は使わない。
- 複数日にまたがる部分約定は、**全数量を最初の約定日へ帰属**させる（1 通知＝1 ロット。分割しない）。
- CORRECTION で置き換えられた約定は `reversed=true` になるので除外され、置換後の約定の `at` で再評価する（訂正で取得日が変わり得る。変わった場合は満了日も再計算）。
- 初期スナップショット由来の保有（`positions` にあるが BUY 通知に紐づかない数量）は**取得日不明として生成対象外**。この「対象外残高」を `unattributed_qty[code] = held − Σ(通知由来ロットの残数量)` として明示的に持つ（負なら入力拒否）。

### D15-06（新）合算 SELL のロット配分

- 同銘柄で同日に満了したロットを 1 候補に合算するとき、`events.exit_lots = [{"source_proposal_id": …, "qty": …}, …]` を **`(entry_date, source_proposal_id)` 昇順**で並べ、`Σ qty == proposal.qty` とする。各要素の `qty` はそのロットの残数量以下で、単元切り捨ては合算後に行い、切り捨て分は**最後の要素から**減らす。
- `events.source_proposal_id` は廃止し、`exit_lots` に一本化する（要素 1 件でも配列）。`packet_hash` の対象外（HASH_FIELDS 14 項目）なので hash 契約は変わらない。
- **約定の消費順**: SELL 通知の `filled_qty` を `exit_lots` の先頭から順に消費する（部分約定は前方から埋まる）。ロット残 = `filled_qty(BUY) − Σ(その BUY を参照する exit_lots の消費分)`。消費分は `filled_qty(SELL)` だけから決まる決定的な関数なので、CORRECTION で `filled_qty` が減れば消費も同じ規則で戻る。台帳に新しい状態は持たない。
- **紐づかない売却**（初期スナップショットの `open_orders` にある SELL、外部注文、`exit_lots` を持たない SELL 通知）の消費順: ① 対象外残高 `unattributed_qty` → ② 通知由来ロットを `entry_date` の古い順（同日は `source_proposal_id` 昇順）。①を先にするのは、取得日不明の残高を先に減らして通知由来ロットの満了判定を保つため。
- 台帳の不変条件（保有総数・`reserved_shares`）は従来どおり銘柄合計で検査し、ロット配分は `derive_holdings`（build-codex 側の読取専用）だけが解釈する。`ops/` は変更しない。

### D15-03（改）数量と入力整合

- `holdings` の全行に **同一の `observed_seq`**（台帳の `seq`）を必須にし、異なれば入力拒否（`MIXED_OBSERVATION`）。同銘柄の行は `held_qty`・`reserved_shares`・`open_sell_notice` が一致していなければ拒否。銘柄ごとに `Σ lot_qty ≤ held_qty − unattributed_qty` でなければ拒否。
- 同銘柄で `strategy` または `strategy_version` が異なるロットが同日に満了した場合は**合算しない**。gate が 1 銘柄 1 候補を前提にするため、その銘柄は当日 `MIXED_STRATEGY_VERSION` で除外し、候補を出さない（次営業日以降も同じなので、運用上は同一銘柄を別版で持たない前提。カタログ v0.2「同一銘柄を保有している間は別の保有群へ追加しない」と整合）。
- `qty = floor(min(Σ 満了ロット残, held − reserved) / lot) × lot`。0 なら除外（従来どおり）。

### D15-07（新）`holdings` 行の最終形

```text
{code, observed_seq, source_proposal_id, strategy, strategy_version, entry_date, lot_qty,
 held_qty, reserved_shares, unattributed_qty, open_sell_notice}
```

`derive_holdings(ledger_view, notices, seq)` は build-codex 側の純粋関数とし、入力は `Ledger.view()`・`Ledger.notice(pid)` の戻り値（素の dict/dataclass）と `Ledger.seq()`。同一 `seq` で取った値だけを組み合わせる（読取中に台帳が進んだら破棄して再読）。

### 固定例（改）

前提は §4 と同じ（`holding_days=20`、人工営業日、`as_of=2026-09-29`、`observed_seq=42`）。

| ロット | entry_date（最初の約定 `at`） | 残 | held / reserved / unattributed | 結果 |
|---|---|---|---|---|
| 7203 / A1-20260901-7203-01 | 2026-09-01（9/1 60 株＋9/2 40 株の部分約定でも 9/1） | 100 | 300 / 0 / 100 | 満了。合算対象 |
| 7203 / A1-20260901-7203-02 | 2026-09-01 | 100 | 同上 | 満了。合算対象 |
| 6857 / A1-20260908-6857-01 | 2026-09-08 | 100 | 100 / 0 / 0 | `NOT_DUE` |
| 9984 / A1-20260901-9984-01 | 2026-09-01 | 200 | 200 / 200 / 0 | `NO_SELLABLE_SHARES` |

候補 1 件: `proposal_id=X1-20260930-7203-01`、`qty=200`（min(200, 300−0)=200、単元 100）、`limit_price=1990.0`、`events={"next_earnings_date": null, "margin_regulated": false, "exit_lots": [{"source_proposal_id": "A1-20260901-7203-01", "qty": 100}, {"source_proposal_id": "A1-20260901-7203-02", "qty": 100}]}`。7203 の対象外残高 100 株は候補に含めない。

消費の例: この SELL が 150 株部分約定 → `-01` 100 株・`-02` 50 株を消費。`-02` の残 50 株は翌営業日に再び満了ロットとして現れるが、未決 SELL（残 50 株）がある間は `OPEN_SELL_EXISTS` で生成しない。

### 反証表への追加（§5 に加える。上限 30 件）

| # | 条件 | 期待 |
|---|---|---|
| 21 | 9/1 60 株・9/2 40 株の部分約定 | `entry_date=2026-09-01`、ロット 100 株 |
| 22 | 9/1 の約定を CORRECTION で 9/3 の約定に置換 | `entry_date=2026-09-03`、満了日が後ろへ |
| 23 | 保留 APPLY で `effective_at` が解決時刻 | `entry_date` は `at` の日付 |
| 24 | 同銘柄 2 ロット合算・切り捨て 50 株 | `exit_lots` は `[(−01, 100), (−02, 50)]`（後方から減らす） |
| 25 | 合算 SELL の 150 株部分約定後の `derive_holdings` | `-01` 残 0、`-02` 残 50 |
| 26 | 外部 SELL 100 株（`exit_lots` なし）で対象外残高 100 株あり | 対象外残高から消費、ロット残不変 |
| 27 | 外部 SELL 150 株で対象外残高 100 株 | 対象外 100 → 最古ロット 50 |
| 28 | `observed_seq` が行ごとに違う | 入力拒否 `MIXED_OBSERVATION` |
| 29 | 同銘柄・別 `strategy_version` の同日満了 | `MIXED_STRATEGY_VERSION` で除外、候補 0 |
| 30 | `Σ lot_qty > held − unattributed` | 入力拒否 |

更新時刻: 2026-09-28 08:45 JST。保存のみ。Codex の再判定を依頼。


## Codex 改訂v2再判定（2026-09-28）

**v2全体は不採用。build_exit_proposals / derive_holdings は未実装。** 最早の有効な約定atのJST日付、部分約定の一括帰属、exit_lotsの前方消費・後方切捨て、観測seqと同銘柄整合は採用可能。指定入力と既存台帳契約について次の3点が残る。

1. **D15-07の入力不足。** ops/aitrader_ops/ledger.py:1134–1139 のLedger.notice(pid)は状態・filled_qty・予約・fills・historyだけを返し、code/side/strategy/strategy_version/eventsを返さない。Ledger.viewにもない。既存公開Ledger.proposal(pid)（1129–1132）を入力束に加え、全BUY/SELL/外部注文のpid集合の完全性と同一seq取得を呼出側が保証する契約が必要。seq一値では通知欠落や読取途中の変更を検出できない。ops変更やprivate状態参照で埋めない。
2. **分割後の数量が未定義。** 台帳SPLIT（ledger.py:738–751付近）はpositions.qtyを変更し、通知filled_qty/fillsは変更しない。BUY100株完了→1:2分割ならheld=200、filled_qty=100で、v2の差分式は追加100株を初期対象外保有と誤認する。逆分割では負のunattributedとして拒否し得る。view/noticeには分割履歴や初期残高がない。数量調整履歴を含む入力とロット変換規則、または分割のない台帳への限定・その条件を確認する証拠と拒否規則を明示する。外部SELLも同じ数量基準で解釈する。
3. **SELL数量訂正と既存台帳の矛盾。** ledger.py:583–585はrule_version>=3でSELL数量変更を明示拒否する。filled_qtyが減るCORRECTIONを現行台帳の正常系として扱わない。前方消費の数学的性質と許される操作を分け、今回の実台帳対象は数量不変の価格/手数料訂正へ限定するか、数量訂正は将来契約と明記する。opsを変更して受理させない。

次はClaudeによる「EXIT導出入力と既存台帳契約の整合」の独立確認・契約修正1件。採用可能な規則の再設計は不要。§6の初期保有対象外・再通知未採用・保存未接続は維持。
Windowsコード読取のみ。pytest未実施（不採用分岐、passed/skipped/failed/秒は未測定）。製品・既存試験・ops/common/examples無変更。上位設計書は指定相対パスと本作業ツリーに存在せず未読。本判定は取得できた共通仕様・公開APIとの直接矛盾に限定。
依頼hash: 0cce283804bbe94c9eb66026cbcddae1e768316b9f5f2016f36d8d642029fc36。git・ダッシュボード・使用率確認は指定により未実施。公開結果は最終報告で区別する。

記録時刻: 2026-09-28T08:48:38+09:00


---

## 改訂 v3（2026-09-28、v2 再判定の残り 3 点への回答。本節が v2 と衝突する場合は本節を優先）

Codex が採用可能とした規則（最早の有効約定 `at` の JST 日付、部分約定の一括帰属、`exit_lots` の前方消費・後方切捨て、`observed_seq` と同銘柄整合、切り上げ指値、初期保有対象外、再通知未採用、保存未接続）は変えない。以下は入力束の完全性、分割、SELL 訂正の 3 点だけを固定する。ops/・common/・既存公開 API は変更しない。private 状態は参照しない。

### D15-08（新）導出入力束と完全性の責任

`derive_holdings` の入力は次の 1 束（`ExitInputBundle`）とし、**呼出側（日次 runner）が組み立てる**。

| 項目 | 由来（既存公開 API のみ） | 役割 |
|---|---|---|
| `seq_before` / `seq_after` | `Ledger.seq()` を束の組立て前後で 2 回 | 一致しなければ `SEQ_CHANGED` として束を捨て再読（上限 3 回、超えたら当日生成を中止） |
| `view` | `Ledger.view()` | `positions[code].qty`、`reserved_shares` |
| `proposal_ids` | runner 自身の outbox 記録（`PHASE2_INTEGRATION` §5 のキー `(execution_day, proposal_id, packet_hash, kind)`）の全 `proposal_id` ＋ 初期スナップショットの `open_orders` の ID（`proposal_id` または `external-{i}`） | **完全性の根拠は「通知は runner だけが `create_notice` する」という既存の直列化契約**（§4）。runner 外で作られた通知は存在しない前提で、存在すれば下の照合で検出する |
| `proposals[pid]` / `notices[pid]` | `Ledger.proposal(pid)` / `Ledger.notice(pid)` を `proposal_ids` 全件について | code/side/strategy/strategy_version/events と filled_qty/fills/trade_state |
| `snapshot` | runner が `init_snapshot` に渡した入力の保存値（`positions`・`open_orders`・`at`） | 対象外残高の起点 |
| `adjustments` | runner が `Ledger.adjust(kind='SPLIT', …)` を呼んだ記録（code, ratio, at）の全件。無ければ空配列 | D15-09 の数量変換 |

**照合（必須。1 銘柄でも失敗したら当日の EXIT 生成を中止し `LEDGER_INCONSISTENT` を返す）**: 銘柄ごとに

```text
expected_qty = split_adj(snapshot_qty, since=snapshot.at)
             + Σ_{BUY 通知の有効 fill} split_adj(fill.qty, since=fill.at)
             − Σ_{SELL 通知の有効 fill} split_adj(fill.qty, since=fill.at)
expected_qty == view.positions[code].qty（保有なしは 0）
```

`split_adj(q, since)` は `since` より後の `adjustments` を時刻順に掛けた数量。通知の欠落・runner 外の通知・未記録の分割・読取途中の変化はすべてこの式で不一致になる。**推測で補正しない**（生成を止めるのが正）。`expected_qty` は各 code で `≥ Σ ロット残 + 対象外残高` であることも同時に確認する。

### D15-09（新）株式分割後のロット数量

- ロットの数量は「そのロットの有効な BUY 約定数量に、**約定 `at` より後の SPLIT を時刻順に掛けたもの**」。`entry_date` は分割で変わらない。対象外残高（初期スナップショット由来）は `snapshot.at` より後の SPLIT を掛ける。SELL の消費数量も、その SELL 約定 `at` 時点の株数基準で消費し、以後の SPLIT は残数量に掛ける（= すべて「その時点の株数」で計算し、最後に現在株数へ換算する）。
- 掛けた結果が整数にならないロットがある銘柄は `SPLIT_UNRESOLVED` で当日除外（台帳側は端株 SPLIT を拒否するので通常は起きない。逆分割で単元未満になるロットは整数でも `BELOW_LOT` へ）。
- 台帳は SPLIT を「未決通知がない銘柄」にしか許さないため、分割時点で未決 SELL が無く、`exit_lots` の消費は分割の前後で区切れる。分割をまたぐ部分約定は存在しない。
- `adjustments` が空で照合が合わない場合は `LEDGER_INCONSISTENT`（分割を推測しない）。

### D15-10（新）SELL 訂正との整合

- 現行台帳（`rule_version ≥ 3`）は SELL の数量訂正を拒否し、価格・手数料のみ訂正できる。したがって **SELL 通知の `filled_qty` は単調非減少**で、`exit_lots` の前方消費は「進むだけ」。v2 の「訂正で消費が戻る」は現行台帳では起きない経路であり、契約から削除する。将来 SELL 数量訂正を許す台帳版が出た場合も、消費は「現在の `filled_qty` の決定的関数」なので再計算規則は同じ（その版の受入は別契約）。
- BUY の数量訂正は台帳が許す（置換後の約定でロット数量・取得日を再評価。v2 の D15-01 改どおり）。BUY の数量訂正でロット残が既に消費済みの数量を下回った場合は `LEDGER_INCONSISTENT`（照合式でも検出される）。
- SELL の取消（`CANCELLED`）・見送り（`SKIPPED`）は未約定分を消費しない（消費は `filled_qty` のみ）。取消後、同ロットは次営業日に再び満了ロットとして現れる（再通知は v2 どおり未採用なので新 ID で生成される。これは「再通知」ではなく通常生成）。

### 固定例（v3 追加）

- **分割**: 7203 を 9/1 に 100 株約定（A1-20260901-7203-01）、9/10 に SPLIT ratio 2（未決通知なし）。`view.positions['7203'].qty=200`、`filled_qty=100`。`adjustments=[{code:'7203', ratio:2, at:'2026-09-10T…'}]` → ロット残 `100×2=200`、対象外 0、照合 `0 + 200 − 0 = 200` 一致。9/29 満了、候補 `qty=200`（単元 100）。`adjustments` を渡し忘れると照合 `100 ≠ 200` で `LEDGER_INCONSISTENT`。
- **runner 外の通知**: `proposal_ids` に無い BUY 通知が 100 株約定していれば照合が 100 不足 → `LEDGER_INCONSISTENT`。
- **読取途中の変化**: `seq_before=42`、束の組立て中に約定が入り `seq_after=43` → `SEQ_CHANGED`、再読。

### 反証表への追加（31〜36）

| # | 条件 | 期待 |
|---|---|---|
| 31 | SPLIT ratio 2（約定後）を `adjustments` 付きで | ロット 200、候補 200、照合一致 |
| 32 | 同条件で `adjustments` 空 | `LEDGER_INCONSISTENT`、候補 0 |
| 33 | 逆分割 ratio 0.5 で 100 株ロット → 50 株 | 整数だが単元 100 未満 → `BELOW_LOT` |
| 34 | `proposal_ids` から 1 通知を欠落 | `LEDGER_INCONSISTENT` |
| 35 | `seq_before ≠ seq_after` | `SEQ_CHANGED`（再読 3 回で中止） |
| 36 | SELL の価格のみ CORRECTION | 消費不変。数量訂正は台帳が拒否（試験は既存の拒否を確認するだけ） |

更新時刻: 2026-09-28 08:52 JST。保存のみ。Codex の再判定を依頼。

## Codex 改訂v3再判定（2026-09-28）

**v3全体は不採用。build_exit_proposals / derive_holdings は未実装。** 残る不足は次の1点に限定する。公開proposal/noticeを入力に含めること、分割履歴を明示する方向、SELL数量訂正を正常系から除くD15-10は採用可能。取得日・配分・数量・指値の採用可能な規則を再設計する依頼ではない。

1. **D15-08: outboxと残高照合は全通知集合の完全性を保証しない。**
   - 既存runner.py:462–474は candidatesのINTENT保存 → Ledger.create_notice → APPROVED → outbox INSERT の順。PHASE2_INTEGRATION.md §5も別DBの非原子性と「APPROVED後、outbox前」の中断を明記している。runnerだけが通知を作る条件を守っていても、outboxにない通知が残る。約定0ならsnapshot＋BUY−SELLの照合値は変わらず、seq_before==seq_afterも成立する。特に未決SELLの存在をnotices集合から求めるとOPEN_SELL_EXISTSを見落とす。viewの予約数量による売却上限だけでは「未決SELLがあれば生成しない」を代替できない。
   - 残高照合は純増減の検査であり、欠落の完全検出ではない。既知の古いBUYロットA=100に対し、一覧外SELL100がAを消費し、一覧外の新しいBUY100が成立した場合、view.qty=100、束のexpected_qty=100で一致する。reserved=0、seqも安定し得るが、束は古いAが残ると誤認し満了判定が変わる。D15-08の「runner外通知・通知欠落はすべて不一致」は成立しない。これはソースと式からの反例であり実Ledger実測ではない。

必要な契約修正: 呼出側が全候補の永続記録（INTENTを含む）と初期外部注文を照合し、未完了run・台帳/記録の不確実な中断があれば生成前に拒否する条件を固定する。outboxだけを全通知台帳と呼ばない。runner外書込を禁止する信頼前提と、入力束だけで検出可能な不一致を分ける。相殺した欠落まで拒否を要求するなら、そのための独立した完全性根拠を入力契約に追加するか、既存公開APIで検証可能な対象に明示的に限定する。ops/private状態参照や未許可のrunner修正で補わない。固定例は「通知作成後outbox前の中断」と「欠落BUY/SELLの純増減0」を追加する。

Windows PowerShellによるコード読取のみ。pytest未実施（不採用分岐、passed/skipped/failed/秒は未測定）。製品・既存試験・ops/common/examples無変更。上位設計書は指定相対パスに存在せず未読。本判断は現行runnerと既存直列化/中断契約への直接矛盾に限定した。サブエージェントなし（独立した実装作業なし）。
依頼hash: 2aaea69f3412dcf6536b53001df8759e0fd9c46ff96852fa23d030252f8901e8。git・ダッシュボード・使用率確認は指定により未実施。人間への追加判断事項はなくQUESTIONS.mdは変更しない。次担当Claudeには入力束の完全性の独立確認・契約修正1件を依頼する。公開結果は最終報告で区別する。
記録時刻: 2026-09-28T08:54:24.3425635+09:00


---

## 改訂 v4（2026-09-28、v3 再判定の残り 1 点「通知集合の完全性」への回答。D15-08 を差し替える）

Codex の反例 2 つ（通知作成後・outbox 保存前の中断、純増減が 0 になる欠落）は正しい。outbox と残高照合を完全性の根拠から外し、**通知作成より前に永続化される記録**を上位集合に使う。信頼前提と検出保証を分けて書く。D15-09・D15-10 と、採用可能とされた規則は変えない。

### D15-08（改 2）通知集合の上位集合と生成の前提条件

**根拠となる既存の順序**（`runner.py` 462〜474、`RUNNER_RECOVERY_PLAN.md` §1）: runner は候補ごとに `orchestration.sqlite` の `candidates` 表へ **INTENT 行を INSERT してから** `Ledger.create_notice` を呼び、APPROVED → outbox INSERT の順に進む。REJECTED の候補も `candidates` 行を持つ。初期スナップショットの外部注文は `init_snapshot` の入力にある。したがって

```text
runner が作った通知の ID 集合 ⊆ candidates 表の全 pid（状態を問わない） ∪ snapshot.open_orders の ID
```

は、**中断がどの時点で起きても成り立つ**（INTENT 行が先に確定しているため。journal の INSERT 自体が失敗した場合は create_notice に進まない）。これを `candidate_ids` と呼び、入力束の `proposal_ids` に使う。outbox は根拠に使わない。

**生成の前提条件（1 つでも満たさなければ当日の EXIT 生成を行わず、理由を返す）**:

| 条件 | 判定 | 理由コード |
|---|---|---|
| 未完了 run が無い | `candidates` に `state='INTENT'` の行が無い。かつ `candidate_ids` の各 pid について「`Ledger.proposal(pid)` が存在するのに outbox 行が無い」ものが無い（= `RUNNER_RECOVERY_PLAN` の「INTENT/CREATED」「INTENT/APPROVED」停止状態） | `RUN_INCOMPLETE` |
| 未決通知が既知 | `Ledger.unconfirmed(as_of)`（公開 API。SENT/EXPIRED/EXTERNAL かつ取引未確定の通知 ID）⊆ `candidate_ids` | `LEDGER_INCONSISTENT`（既知集合の外に未決通知がある） |
| 残高照合 | D15-08（v3）の照合式が全銘柄で一致 | `LEDGER_INCONSISTENT` |
| 観測の安定 | `seq_before == seq_after` | `SEQ_CHANGED` |

`OPEN_SELL_EXISTS` の判定は `candidate_ids` の SELL 通知だけでなく、**`Ledger.unconfirmed(as_of)` に含まれる同銘柄の通知**（外部注文 EXTERNAL を含む）でも成立させる。これで「未決 SELL を見落として生成する」経路は、runner 外書込が無い限り閉じる。

### 信頼前提（T）と検出保証（D）の区別

| 種別 | 内容 | 根拠 |
|---|---|---|
| T1 | 通知（`create_notice`）と台帳への書込は runner の直列化ロック内でのみ行われる。runner 外の直接書込は運用で禁止 | `PHASE2_INTEGRATION` §4、`RUNNER_RECOVERY_PLAN` §継続前に必要な照合 |
| T2 | runner は INTENT 行を `create_notice` より前に永続化する | `runner.py` 462〜474（現行コード。変更しない） |
| D1 | T1・T2 の下で、中断がどこで起きても通知集合は `candidate_ids` に含まれる。未完了 run は `RUN_INCOMPLETE` で拒否 | 上位集合の構成から |
| D2 | 既知集合の外にある**未決**通知は `unconfirmed` で検出 | 公開 API |
| D3 | 既知集合の外にある**確定済み**通知のうち、純増減が残る欠落は残高照合で検出 | 照合式 |
| **非保証** | 既知集合の外で BUY と SELL が同数だけ確定して純増減 0 になった欠落（Codex の反例 2）は、**T1 が破られた場合にのみ生じ、入力束だけでは検出しない** | `Ledger` に通知 ID の全列挙 API が無いため |

非保証の範囲は「T1 違反（runner 外書込）」に限定される。これを検出したい場合の唯一の手段は台帳イベント記録（`ledger_events`）の全走査で、現行の公開 API には無い。**`Ledger.notice_ids() -> list[str]`（全通知 ID の読取専用列挙）を ops 側の将来 API として `common/ISSUES.md` に提案**し、採用されたら `candidate_ids ⊇ notice_ids()` の検査を D4 として追加する。今回は ops を変更しないので D4 は含めない。

### 固定例（v4 追加）

- **中断（通知作成後・outbox 前）**: `candidates` に `pid=A1-20260929-6857-01, state=INTENT`、`Ledger.proposal(pid)` は存在、outbox 行なし → `RUN_INCOMPLETE`。EXIT 生成は行わず、復旧手順（RUNNER_RECOVERY）へ。候補 0・除外一覧なし・理由 1 件。
- **相殺する欠落**: `candidate_ids` に無い SELL 100 株（7203、9/15 全約定）と BUY 100 株（7203、9/16 全約定）が台帳にある。`unconfirmed` は空、照合は `100 == 100` で一致 → **検出しない**（T1 違反の事例として固定。試験は「検出しないこと」ではなく「T1 の下では生じないこと」を、runner 経路で通知を作ったときに `candidate_ids` が全通知を含むことで確認する）。
- **未決 SELL が既知集合の外**: 外部注文由来でない SELL 通知 `X1-…` が SENT・未約定で `candidate_ids` に無い → `unconfirmed` に現れ `LEDGER_INCONSISTENT`。

### 反証表への追加（37〜41）

| # | 条件 | 期待 |
|---|---|---|
| 37 | `candidates` に INTENT 行が残る | `RUN_INCOMPLETE`、候補 0 |
| 38 | INTENT 行なし・通知あり・outbox なし（APPROVED 後の中断を模擬） | `RUN_INCOMPLETE` |
| 39 | `unconfirmed` に `candidate_ids` 外の ID | `LEDGER_INCONSISTENT` |
| 40 | runner 経路（`run_daily` 相当の模擬）で BUY 2 件を通知した後の `candidate_ids` | 2 件とも含む（`Ledger.proposal` で存在確認） |
| 41 | 外部注文（EXTERNAL）の未決 SELL がある銘柄 | `OPEN_SELL_EXISTS`（`unconfirmed` 経由で検出） |

更新時刻: 2026-09-28 08:57 JST。保存のみ。Codex の再判定を依頼。


## Codex 改訂v4再判定（2026-09-28）

**v4全体は不採用。build_exit_proposals / derive_holdings は未実装。残件は1点。** INTENT先行永続化による上位集合とT1/T2・D1〜D3の分離は採用可能。T1違反による相殺欠落の検出を今回の条件に加えない。ISSUESのnotice_ids提案は変更しない。

1. **D15-08改2のoutbox必須検査が、正常な初期外部注文もRUN_INCOMPLETEにする。** candidate_idsはcandidates全pidとsnapshot.open_ordersの和集合なのに、全pidへ「Ledger.proposalが存在しoutboxが無い」を適用している。ledger.py:317–338は初期注文をEXTERNAL通知として作り、proposal(:1129–1132)にも返す。一方、この初期登録はrunnerのoutboxを作らない。したがってINTENTなし・未完了runなしの正常な初期SELLでも前提条件で当日生成全体を拒否し、反証41のOPEN_SELL_EXISTSへ到達できない。確定済みになった外部注文もproposalが残るため同じ拒否が続く。これはT1違反の検出要求ではなく、契約自身が認める初期入力との矛盾。

修正が必要な箇所: 完全性用のcandidate_ids（和集合）は維持し、outbox必須の対象をrunner由来candidatesの通知に限定するか、初期外部注文の独立した照合条件を明記する。snapshot由来であることを入力と照合し、単にIDのexternal接頭辞だけで除外しない。runner由来のINTENT・通知ありoutboxなしは引き続きRUN_INCOMPLETEとする。外部SELL未決はOPEN_SELL_EXISTS、外部注文が確定済みの場合はoutbox不在だけで全体拒否しない期待値を固定する。取得日・配分・分割・数量・指値を再設計する依頼ではない。

Windows再現: `PYTHONDONTWRITEBYTECODE=1; PYTHONPATH=ops;build-codex`（環境変数）、リポジトリルートで `python -m pytest build-codex/tests/test_exit_v4_external_contract.py -q -p no:cacheprovider`。**1 passed / 0 skipped / 0 failed / 0.21秒**。Python 3.12.14、Windows、実Ledger公開APIと一時orchestration.sqlite（TEMP、Dropbox外）を使用。正常なEXTERNAL通知のproposal存在・unconfirmed包含・INTENTなし・outboxなしを確認した試験であり、未実装のEXIT関数を実行した結果ではない。不採用分岐なので§5等の実装受入・packet/gate/ledger/runner関連一括・全体試験は未実施。

製品・既存試験・ops/common/runner/examples・自分宛てMD・QUESTIONS無変更。上位設計書は指定相対パスに存在せず未読。本判定は現行公開APIとv4自身の前提条件・反証41との直接矛盾に限定。独立実装作業がないため子エージェントなし。git・ダッシュボード・使用率確認は依頼指定により未実施。
依頼hash: 211ba0b3f6e00c4738f546a357e39bfd765e3441191cf27728ceaad9671c276c。
記録時刻: 2026-09-28T09:00:24.552150+09:00


---

## 改訂 v5（2026-09-28、v4 再判定の残り 1 点「初期外部注文への outbox 必須検査」への回答。D15-08 改 2 の前提条件表だけを差し替える）

Codex の指摘どおり、`candidate_ids` の全 pid に「通知あり・outbox なし → RUN_INCOMPLETE」を当てると、初期スナップショットの外部注文（`EXTERNAL` 通知。runner の outbox を持たない）が正常でも全体拒否になる。未完了 run の判定を **runner 由来の記録だけ**に限定し、外部注文は**スナップショット入力との照合**で扱う。上位集合 `candidate_ids`、T1/T2・D1〜D3、その他の規則は v4 のまま。

### 前提条件（改）

| 条件 | 判定 | 理由コード |
|---|---|---|
| 未完了 run が無い | `candidates` 表に `state='INTENT'` の行が無い。runner は outbox INSERT と `state='APPROVED'` 更新を同一トランザクションで行う（`runner.py` 468〜474）ため、「通知あり・outbox なし」は必ず `state='INTENT'` として現れる。outbox 自体は検査しない | `RUN_INCOMPLETE` |
| 外部注文がスナップショットと一致 | `snapshot.open_orders` の各 ID について `Ledger.proposal(pid)` が存在し `exec_condition == 'EXTERNAL'`。逆に、`candidate_ids ∪ unconfirmed(as_of)` に現れる `exec_condition == 'EXTERNAL'` の通知は、すべて `snapshot.open_orders` の ID に含まれる。**ID の接頭辞（`external-`）では判定しない** | `LEDGER_INCONSISTENT` |
| 未決通知が既知 | `Ledger.unconfirmed(as_of)` ⊆ `candidate_ids`（v4 のまま） | `LEDGER_INCONSISTENT` |
| 残高照合・観測の安定 | v3 のまま | `LEDGER_INCONSISTENT` / `SEQ_CHANGED` |

外部注文の扱い（銘柄単位、前提条件を通過した後）:
- 外部 SELL が未決（`unconfirmed` に含まれる）→ その銘柄は `OPEN_SELL_EXISTS`。他銘柄の生成は続ける。
- 外部注文が確定済み（約定・取消・見送り）→ 生成に影響しない（消費は D15-06 の「紐づかない売却」規則、数量は D15-09）。outbox が無いことを理由に拒否しない。
- 外部 BUY が未決 → 売却可能株数に加えない（v1 の規則どおり）。

### 固定例（v5 追加）

- **正常な初期外部 SELL**: `snapshot.open_orders=[{proposal_id:null→'external-1', code:'7203', side:'SELL', qty:100}]`、台帳では `EXTERNAL` 通知、`candidates` に INTENT 行なし。前提条件はすべて通過。7203 は `OPEN_SELL_EXISTS` で除外、他銘柄（6857 など）の生成は続く。`RUN_INCOMPLETE` にはならない。
- **確定済みの外部 SELL**: 上の外部注文が 100 株約定済み → `unconfirmed` に無く、7203 のロット残は「対象外残高 → 最古ロット」の順で 100 株消費済みとして導出。生成は通常どおり。
- **スナップショットに無い EXTERNAL**: `unconfirmed` に `exec_condition='EXTERNAL'` の通知があるが `snapshot.open_orders` に無い → `LEDGER_INCONSISTENT`（初期入力の改変か runner 外書込）。

### 反証表への追加（42〜44）

| # | 条件 | 期待 |
|---|---|---|
| 42 | 正常な初期外部 SELL（未決）＋他銘柄の満了ロット | 7203 は `OPEN_SELL_EXISTS`、他銘柄は候補生成。`RUN_INCOMPLETE` なし |
| 43 | 初期外部 SELL が全約定済み | 前提条件通過、7203 のロット残から 100 株消費、候補は残数量で生成 |
| 44 | `snapshot.open_orders` に無い EXTERNAL 通知 | `LEDGER_INCONSISTENT` |

反証 38（v4）は「INTENT 行なし・通知あり・outbox なし」を模擬していたが、現行 runner ではこの状態は `state='INTENT'` としてしか現れないため、**38 は「`state='INTENT'` の行があり通知も存在する」に読み替える**（37 と同じ経路。期待は `RUN_INCOMPLETE`）。

更新時刻: 2026-09-28 09:02 JST。保存のみ。Codex の再判定を依頼。


## Codex 改訂v5再判定（2026-09-28）

**v5のINTENT限定判定・EXTERNAL双方向照合は採用可能。ただし今回Bの取得経路を含む実装契約全体は不採用。残件1点。** build_exit_proposals / derive_holdings は未実装。

1. **B項目2が指定するsnapshotのjournal読取は、現行runnerの保存内容では実装できない。** runner.py:347–350 の orchestration.sqlite は runs(id,hash,manifest,result)、candidates(pid,hash,day,side,state,result,owner)、outbox(key,body) の3表。manifest（:361以降）は実行日・評価・hash等であり、init_snapshotへ渡したpositions/open_orders/atを保持しない。initialize_mock（:194–206）はLedger.init_snapshot(cash, positions, [], at)を呼び、mock-runner.jsonにはmode/versionしか保存しない。初期snapshotはledger.sqliteのSNAPSHOTイベントへ保存される（ledger.py:1023–1028）が、今回の「同じjournalから読取」「公開APIのみ」「runner/ops無変更」とは異なる取得経路である。Ledger.viewの現在保有やEXTERNAL通知の逆算で初期snapshotを補完すると、独立した入力との照合というv5の根拠を失う。

必要な修正は入力元1点のみ: candidatesはorchestration.sqliteを読取専用で読む一方、snapshot（必要なadjustmentsを含む束）は呼出側が明示入力として渡す、とB項目2とD15-08を整合させる案を推奨。初期入力を保存する新たなjournal書込を選ぶ場合は、その保存契約と編集許可が別途必要。今回、存在しない表を独自に定義して読取実装したり、ledger内部イベント走査に置き換えたりしない。

v5で修正されたrunner.py:468–474のoutbox INSERTとAPPROVED更新の同一トランザクションは確認済み。INTENT検査のみで未完了を拒否する点、EXTERNALを接頭辞で判定しない点、正常外部注文にoutboxを要求しない点に追加の異議はない。T/Dの区別、取得日・配分・分割・数量・指値の採用可能な規則を再設計する依頼ではない。

Windows PowerShellで現行ソースとschemaを読取確認。pytest未実施（不採用分岐。passed/skipped/failed/秒は未測定）、固定hash未作成。製品・既存試験・ops/common/runner/examples・自分宛てMD無変更。上位設計書は指定相対パスに存在せず未読。本判定は現行保存内容と指定取得経路の直接矛盾に限定。人間の追加判断は不要で、Claudeへの技術契約修正依頼のためQUESTIONS.mdは変更しない。独立実装作業がなく子エージェントなし。git・ダッシュボード・使用率確認は今回指示により未実施。
依頼hash: 354c4fd5f300e6e35fb2727f8526828d4c0e50a6182ed0322c14b26c9a8c2dae。
記録時刻: 2026-09-28T09:05:00.778983+09:00


---

## 改訂 v6（2026-09-28、v5 再判定の残り 1 点「スナップショット入力の取得経路」への回答。D15-08 の入力元だけを明確化）

Codex の指摘どおり、runner の journal（`orchestration.sqlite`: runs / candidates / outbox）には `init_snapshot` へ渡した positions / open_orders / at は保存されておらず、`mock-runner.json` にも無い。依頼文（B 項目 2）の「snapshot も同じ journal から読む」が誤りで、契約本文（D15-08 表「runner が init_snapshot に渡した入力の保存値」）と食い違っていた。次のとおり固定する。

- **`candidates` 表**（未完了 run の判定・`candidate_ids` の runner 由来部分）: `orchestration.sqlite` を**読取専用**で開いて読む。
- **`snapshot`（positions / open_orders / at）と `adjustments`（SPLIT 記録）**: **呼出側が明示入力として渡す**。`derive_holdings` はこれらを取得しに行かない。runner がこれらをどこに永続化するか（例: journal への新表）は **runner の別契約**であり、本契約の範囲外。今回の実装と試験では、呼出側（試験コード・将来の runner）が `init_snapshot` / `adjust` に渡した値をそのまま渡す。
- `Ledger.view` の現在保有や `EXTERNAL` 通知からスナップショットを逆算して補完しない（独立した入力との照合という根拠を失うため）。渡されなければ `SNAPSHOT_MISSING` で当日生成を行わない。
- `snapshot.open_orders` の各要素は `init_snapshot` に渡した形（`proposal_id` が null なら台帳の付番 `external-{i}` を呼出側が同じ順序で付けて渡す）。EXTERNAL 照合（v5）はこの ID で行う。

固定例・反証表は v5 までのとおり（入力元が変わるだけで期待値は不変）。反証 45: `snapshot` を渡さずに呼ぶ → `SNAPSHOT_MISSING`、候補 0、台帳・journal 無変更。

更新時刻: 2026-09-28 09:06 JST。保存のみ。Codex の再判定を依頼。

## Codex 改訂v6再判定（2026-09-28）

**v6を採用する。** 前回残件の入力元は、candidatesだけをjournalから読取り、snapshot / adjustmentsを呼出側の明示入力とすることで解消した。現行runnerの保存schema、INTENT先行永続化、outbox INSERTとAPPROVED更新の同一transaction、Ledgerの公開proposal/notice/view/unconfirmed/seqと矛盾しない。実装はbuild-codex内の独立した導出・生成APIに限定する。

- v5のINTENT限定検査、EXTERNAL双方向照合、v4のT1/T2と検出保証の区別を維持する。runner外書込による確定済みBUY/SELLの相殺欠落まで検出する保証は追加しない。
- snapshot未指定はSNAPSHOT_MISSING。現在保有や内部イベントからの補完は行わない。adjustmentsも明示入力で、runnerへの保存・接続は別工程。初期外部注文IDは呼出側が台帳と同じ順序（external-1から）で解決する。
- 取得日・配分・分割・数量・指値・既存14項目hashは改訂済み規則を維持する。初期保有の取得日推定、保存mode、再送、実通信は今回採用しない。

確認した一次資料は本契約、共通仕様フェーズ1/2、common/ISSUES.md、PHASE2_INTEGRATION.md、現行runner/packet/Ledger公開API。上位設計書は指定相対パスおよび本作業ツリーに存在せず未読であり、その全文を再確認済みとは扱わない。
依頼hash: f933da63379c141869f7e70f32de33b436e3a7c79616d772245da67e931eb79f。採否記録時刻: 2026-09-28 09:10 JST。実装と実測結果は後続の確定記録へ記載する。

### v6実装確定・Windows実測（2026-09-28 09:20 JST）

`packet.build_exit_proposals`を追加し、`ExitBuildResult(proposals, excluded)`を返す。既存BUY生成・14項目hash・render_packetは無変更。
`exit_holdings.derive_holdings(ledger, orchestration_path, snapshot, adjustments, as_of, *, max_attempts=3)`を新設し、`HoldingDerivationResult(holdings, reason_codes, observed_seq, candidate_ids, excluded)`を返す。`snapshot=None`はSNAPSHOT_MISSING。前提条件の失敗はholdings空とreason_codes、分割端数の銘柄除外はexcludedへ返す。呼出側はreason_codesが空であることを確認してholdingsを生成器へ渡し、導出側excludedと生成側excludedを併記する。例外を正常な候補0へ読み替えない。

公開Ledger APIのみを使用し、journalはmode=roでcandidatesだけを取得、接続はcloseする。T1の直列化ロックと明示入力の保管責任は呼出側に残る。今回runnerへの接続・保存は実装していない。seq変化は最大3回読取りでSEQ_CHANGED。既知のCREATED/APPROVEDを含む未決SELLも抑止する。SELL消費は有効fillの時刻順、紐づかない売却のロット順はJST取得日・ID。分割はDecimalで累積し、銘柄合計の残高照合を先に行い、消費後のロット残に端数があればその銘柄を除外する。

新規`tests/test_exit_generation_v6.py`は反証1〜45を複合ケース込み33試験で検証。16/17は実gate/Ledger、31〜45は実LedgerとTEMPのjournal、40は実runner.runによる2件の模擬通知作成を使用。38はv5の読み替え、24は単元150で200→150へ切捨て（配分100/50）。45は台帳seq・journal行/バイトhash不変を確認する。30件目安を超える理由は今回明示された45項目と、時刻/FIFO/分割境界の回帰を省略しないため。

改訂固定例は7203の2ロット各100、held300/unattributed100、候補200株。snapshot_id=`a`×64、policy_version=`v1`を人工固定値とし、packet_hash=`3997d6f7c54aae594c7caf1036c93904633fbeaf662df77fa5f773362f18bbe7`を`tests/fixtures/exit_v6_expected.json`へ固定した。試験実行時の期待値再生成はしない。

Windows 11 10.0.26200、Python 3.12.14、PowerShell、cwd=D:/work/ai-trader。環境変数はPYTHONDONTWRITEBYTECODE=1、PYTHONIOENCODING=utf-8、PYTHONPATH=ops;build-codex。DBはすべてDropbox外TEMP。

- 初回関連一括: build-codex/tests・common/tests/phase2・ops/testsのtest_*.pyからファイル名にpacket/gate/ledger/runner/exitを含む既存27ファイルと新規EXIT（当時26試験）を選択。`python -m pytest <選択28ファイル> -q -ra -p no:cacheprovider --basetemp <TEMP内の専用ディレクトリ>`。409 passed / 0 skipped / 0 failed、44.27秒（プロセス実時間44.640秒）。後続の分割補強・受入追加前の一括実測であり、最終版全体結果とは呼ばない。
- 最終版: `python -m pytest build-codex/tests/test_exit_generation_v6.py build-codex/tests/test_exit_pipeline.py build-codex/tests/test_exit_v4_external_contract.py -q -ra -p no:cacheprovider --basetemp "$env:TEMP\exit_v6_final_0920"`。41 passed / 0 skipped / 0 failed、3.57秒。新規33件＋既存EXIT8件。全体試験は未実施（独立API追加で、既存関連一括と変更範囲の再検証に限定）。初回と最終を足して件数を表記しない。
- 開発中の局所実測も区別して保存: 新規26件0.62秒、packetとの局所74件1.58秒、補強後新規32件1.45秒、EXIT関連40件3.46秒、受入追加後新規33件1.40秒。最終41件が上記最終ファイルに対応する。

最終SHA-256:

| ファイル | SHA-256 |
|---|---|
| aitrader/packet.py | 259142dcc524ffc825c72353d31a7b1a373f5137e97da6e84f7f1da31f9ef0eb |
| aitrader/exit_holdings.py | e06bdbc1031a170d9c051b7338c1b2dc4011d29c06ef45e741eacb578cd7de69 |
| tests/test_exit_generation_v6.py | 1554bba00c50c49d8ca2dcfc59059b6e860dbf2657b3e0dd5be383e572f54183 |
| tests/fixtures/exit_v6_expected.json | f06cf71f6c8f2b8fe8ed9b5dbea349a214995fc6e8af7171ce1773ee1cc14f8b |

開始時に保存した既存試験/ops/common/runner/examples/自分宛てMDの231ファイルはhash変更なし。packet.pyの既存行削除・置換なし。QUESTIONSは変更せず、git・ダッシュボード・使用率確認は依頼どおり未実施。Sol 1体が実装・局所試験、親が採否・重要差分確認・統合検証・記録を担当した。Claude独立確認済みではない。次担当ClaudeにはEXIT生成の重要差分の独立確認1点を依頼する。
