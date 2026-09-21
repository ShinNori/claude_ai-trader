# Claude 独立レビュー 第11回: D10-01〜05 の未決契約整理（結合 v2・履歴 v2 の最小契約、人工例、固定出力）

2026-09-16。実施者 Claude（Claude Code、Windows 実機、モデルは Claude Fable 5.1。サブエージェント 0 体）。依頼は[キャッチボール文書](Claude_Opusキャッチボール.md)の B（更新 2026-09-16 23:34:35 JST）。一次資料は [HISTORY_VALIDITY_BINDING_V2_DECISIONS.md](HISTORY_VALIDITY_BINDING_V2_DECISIONS.md)。第10回までは[第10回](Claude_Opusレビュー_証拠履歴_第10回.md)以前を参照。

読んだのは B の指定どおり V2_DECISIONS.md、`evidence_history_fixture.py` と `period_evidence_history_fixture.py` の検証器/評価器、結合 v1 `history_validity_binding.py`、`strict_input._aware/_canonical`、examples の結合 v1 例と期間履歴例。README・過去レビュー全文・全体試験は読んでいない・回していない。

実装コード・既存試験の期待値・共通仕様・合成データ・examples は変更していない。新規試験ファイルも今回は作っていない（結合 v2 は未実装で実行対象が無い）。実 API・売買審査・LINE・証券接続・発注・見張り再開・公開は行っていない。検算スクリプトと出力は Dropbox 外（scratchpad）に置いた。

## 結論

**D10-01〜05 の 5 論点はすべて一意に確定できた。** 段表（5 段・10 理由）、件数表（6 件数・digest・timed を 5 結果クラスで固定）、人工例 9 束とその固定 15 キー出力、履歴 v2 の正規化契約を下に示す。固定出力の digest と件数は、既存 4 モジュールの検証器・評価器を読み取り専用で呼ぶ参照計算で実測した値であり、手計算ではない。ただし**結合 v2 本体は未実装なので、これは設計期待であって製品実測ではない。**

- 判断 1（D10-01/05）: 採用。構造 18 理由は段 3 `INVALID_BUNDLE`、重複は段 5 `VALIDITY_NOT_SATISFIED`（候補件数 0）、digest 生成失敗は `INVALID_BUNDLE` へ全件リセット。両履歴の同時 mode 差は段 2 で 2 理由を昇順併記。複合反例 3 種の固定出力は E5・E6・E9。
- 判断 2（D10-02/03）: 採用。extra は「行履歴全 subject ∪ 期間履歴全 subject − 必須」。6 件数/digest/timed は 5 クラスで一意（§2 の表）。最小人工入力は既存 2 例の合成で済み、新規ファイルは不要。
- 判断 3（D10-04）: 具体化できた。正規化は行履歴 v2 の `observed_at`/`recorded_at` の**比較用コピーだけ**。範囲外・解釈不能は `INVALID_TIMESTAMPS` に畳み、NO_OP 判定より前に置く。秒未満は 6 桁までを受け、7 桁以上は拒否。期間 v1 は表記差で `RECEIPT_CONFLICT`（実測）のまま。

指摘は 1 件（R11-01、低）。契約の穴で、実装バグではない。

## 1. 判断 1 — 段表と理由優先（D10-01 / D10-05）

### 1-1 段表（結合 v2）

| 段 | 判定 | 理由 | 件数/digest/timed |
|---|---|---|---|
| 1 封筒 | 最上位が dict かつ 5 キー `mode/source_origin/input/history/period_history` ちょうど、mode=`history_validity_binding_fixture_v2`、origin=`offline_fixture` | `INVALID_BUNDLE` 単独 | 全 0 / null / false |
| 2 部品 mode 値差 | `history` が dict で `mode` キーがあり値≠`evidence_history_fixture_v2` → `HISTORY_MODE_MISMATCH`。`period_history` が dict で `mode` キーがあり値≠`period_evidence_history_fixture_v1` → `PERIOD_HISTORY_MODE_MISMATCH`。**両方なら 2 理由を昇順併記**（`["HISTORY_MODE_MISMATCH", "PERIOD_HISTORY_MODE_MISMATCH"]`）。依存 API は呼ばず、他キー・origin には依存しない | 上記 | 全 0 / null / false |
| 3 構造・内部整合 | `inspect_strict_input` が非 VERIFIED、履歴 v2 検証器が dict（17 理由すべて）、期間 v1 検証器が dict（**構造 18 理由すべて**）、`as_of` 解釈不能、必須集合が空。部品の順は input → history → period_history で、最初の不合格で打切り | `INVALID_BUNDLE` 単独 | 全 0 / null / false |
| 4 F-01 書式 | コード: `expected_codes`、`sources` のキー、行レコードの `code`、行履歴 `subject.code`/`payload.code`、期間履歴 `subject.code`/`payload.code`。ID: `source_documents[].id`、`document_id`、行履歴 `receipt_id`/`revision_id`/`supersedes.revision_id`、期間履歴 `receipt_id`/`revision_id`/`series_id`/`supersedes.revision_id` | `CODE_FORMAT_INVALID`・`IDENTIFIER_FORMAT_INVALID`（同段併記） | 全 0 / null / false |
| 5a 三者時点 | `input.as_of == history.decision_at == period_history.decision_at`（aware 比較。表記差は不一致にしない） | 不一致なら `DECISION_TIME_MISMATCH` 単独で打切り | **全 0 / null / false**（v1 と違い評価前なので件数を返さない） |
| 5b 各 1 回評価 | 行モデル `evaluate(as_of)` と期間モデル `evaluate(as_of)` を各 1 回。行 digest が None、または期間評価が `SELECTION_HASH_INVALID` | `INVALID_BUNDLE` 単独（防御リセット） | 全 0 / null / false |
| 5c 比較 | 期間評価が `PERIOD_OVERLAP_AT_DECISION` → `VALIDITY_NOT_SATISFIED` を立て候補集合を空とみなす。extra>0 → `EXTRA_SUBJECT_PRESENT`。必須 subject ごとに 行選択なし→`HISTORY_SELECTION_MISSING`、行 hash 差→`ROW_HASH_MISMATCH`、期間候補なし・`record_sha256` 差→`VALIDITY_NOT_SATISFIED`、すべて合格→matched+1。**subject 内は最初の不合格だけ**（v1 の `continue` を踏襲）、subject 間は理由を集合で併記し昇順 | 併記 | §2 の表 |

構造由来 18 理由の内訳（期間 v1 `_validate_period_history` の返す理由）: INVALID_BUNDLE / INVALID_MODE / INVALID_ORIGIN / INVALID_DECISION_AT / INVALID_ENTRIES / INVALID_ENTRY / INVALID_IDENTIFIER / RECEIPT_CONFLICT / INVALID_SUBJECT / INVALID_TIMESTAMPS / RECORDED_AT_REVERSED / INVALID_PAYLOAD / PERIOD_INTERVAL_INVALID / INVALID_HASH / PAYLOAD_HASH_MISMATCH / INVALID_SUPERSEDES / REVISION_CONFLICT / SERIES_REFERENCE_INVALID。評価器由来の 2 理由（PERIOD_OVERLAP_AT_DECISION / SELECTION_HASH_INVALID）だけが段 5 に残る。これで「検証・評価分離」「段 5 各 1 回」「重複は段 5」の 3 条件が同時に成り立つ。

### 1-2 なぜ重複は `VALIDITY_NOT_SATISFIED` で、`INVALID_BUNDLE` ではないか

重複は構造破損ではなく「as_of 時点で有効期間が確定できない」という意味的不適合であり、単体期間 API でも評価段で初めて出る。段 3 に置くと期間モデルを段 3 と段 5 で 2 回評価することになり、D10-01 の骨格と衝突する。また `INVALID_BUNDLE` は全診断を捨てる理由なので、行側の診断（digest・corrected）まで消えてしまう。E3 の固定出力のとおり、重複でも行 digest と `required`・`corrected` は返し、`period_candidate_count=0`・`matched=0` で「期間側が確定しない」ことだけを表す。

### 1-3 複合反例（依頼の 3 種）

| 例 | 束 | 固定理由 | 決まる理由 |
|---|---|---|---|
| E9 時点差＋重複 | E3 に加え `period_history.decision_at` を 06:51 へ | `DECISION_TIME_MISMATCH` 単独、全 0 | 段 5a が 5b より先。評価しないので重複を見ない |
| E5 余分 subject＋重複 | E3＋E4 | `["EXTRA_SUBJECT_PRESENT", "VALIDITY_NOT_SATISFIED"]`、`extra=1`、`series=4`、`cand=0`、`matched=0` | 同段併記。extra は検証済み全 subject から出るので重複の影響を受けない |
| E6 行欠落＋期間候補なし | 行履歴に lots だけ、期間履歴も lots 系列だけ | `HISTORY_SELECTION_MISSING` 単独、`matched=1`、`cand=1` | subject 内は最初の不合格だけ。events の期間欠落は報告されない |

E7（行 hash 差、期間候補は旧 hash で存在）は `ROW_HASH_MISMATCH` 単独。行 hash 差＋期間候補なしも同じく `ROW_HASH_MISMATCH` 単独になる（subject 内短絡）。これは v1 と同じ規則で、変えていない。

## 2. 判断 2 — 余分 subject と件数（D10-02 / D10-03）

### 2-1 定義

- `required_subject_count` = |{lots, events} × 検証済み expected_codes|（v1 と同じ、期間側から再計算しない）
- `extra_subject_count` = |(行履歴の全 revision の subject ∪ 期間履歴の全 revision の subject) − 必須|。未来のみ・期限外のみの subject も含む（両モデルとも検証済み全 entry から取る）。重複排除済み
- `matched_subject_count` = 必須のうち 行選択あり・行 hash 一致・期間候補ちょうど 1 件・`record_sha256` 一致・半開区間充足 をすべて満たす数
- `corrected_after_as_of_count` = 行モデルの `corrected_subjects ∩ 必須`（v1 と同じ。期間側の訂正は数えない）
- `period_series_count` = 期間モデルの `series_count`（未来のみの系列も含む全系列数）
- `period_candidate_count` = 期間評価の `candidates` の件数（必須外を含む全 subject の有効候補数）。重複検出時は 0
- `selection_sha256` = 行選択 digest のみ。期間 digest はこの欄に入れない
- `period_evidence_timed` = `reason_codes==[]` かつ `matched==required>0`。この mode でだけ true になり得る

### 2-2 結果クラス別の固定値

| クラス | 理由 | required | matched | extra | corrected | series | cand | digest | timed |
|---|---|---|---|---|---|---|---|---|---|
| 成功 | `[]` | n>0 | n | 0 | 0 以上 | 実数 | 実数 | 行 digest | **true** |
| 先行拒否（段 1〜4） | 単独 or 段 4 併記 | 0 | 0 | 0 | 0 | 0 | 0 | null | false |
| 時点差（段 5a） | `DECISION_TIME_MISMATCH` 単独 | 0 | 0 | 0 | 0 | 0 | 0 | null | false |
| 評価拒否（段 5b） | `INVALID_BUNDLE` 単独 | 0 | 0 | 0 | 0 | 0 | 0 | null | false |
| 比較失敗（段 5c） | 残り 4 理由の併記 | n | 0〜n-1 | 実数 | 実数 | 実数 | 実数（重複なら 0） | 行 digest | false |

v1 が時点差でも件数を返していた診断契約は v2 では継承しない。v1 は時点判定の前に行モデルを評価していたので返せたが、v2 は「三者一致後に各 1 回」を骨格にしたため評価前に打ち切る。時点差の診断が必要なら結合 v1 を使う（v1 は据え置き）。

### 2-3 最小人工入力（新規ファイル不要）

E1 = `examples/history_validity_binding_valid.json` の `input` と `history`（`history.mode` だけ `evidence_history_fixture_v2` へ）＋ `examples/period_evidence_history_valid.json` を `period_history` に＋最上位 `mode` を v2。三者の時点はいずれも `2026-09-12T06:50:00+09:00` で既に一致している。

**E1 の固定 15 キー出力**（参照計算の実測）:

```json
{"mode": "history_validity_binding_fixture_v2", "source_origin": "offline_fixture", "status": "VERIFIED_OFFLINE_BINDING", "selection_sha256": "e6f112d03807f3f9c806106890a95900a11680e150a037557c913b427db22bec", "reason_codes": [], "required_subject_count": 2, "matched_subject_count": 2, "extra_subject_count": 0, "corrected_after_as_of_count": 0, "period_series_count": 2, "period_candidate_count": 2, "period_evidence_timed": true, "ready_for_live": false, "current_signal": false, "read_only": true}
```

`selection_sha256` は結合 v1 例の digest `e6f112d0…22bec` と同一。射影に時刻が入らないので、履歴 v2 の正規化は digest を変えない。期間 API 単体の digest `ba6486dd…186ca` はこの欄に現れない。

派生 8 束（E1 からの差分だけ記す。追加 entry の `sha256` は payload の canonical hash で、参照計算で得た値）:

| 例 | E1 からの差分 | 固定出力（15 キーのうち変わる欄） |
|---|---|---|
| E2 | `period_history.decision_at` = `2026-09-12T06:51:00+09:00` | `DECISION_TIME_MISMATCH`、全 0、digest null |
| E3 | 期間 entries の 3 番目（未来 entry の前）に系列 `artificial-period-lots-series-x`（receipt/revision も `…-x`）、subject lots/0001、observed/recorded `2026-09-11T18:00:00+09:00`、`valid_from` `2026-09-11T00:00:00+09:00`、`valid_until` `2026-09-15T00:00:00+09:00`、`record_sha256` は既存 lots 行 hash、`sha256`=`2bc3f358a8cc4f7892f2ffbafd7c94ac69ac1b909634067d3f850d513692056d`、`supersedes` null | `["VALIDITY_NOT_SATISFIED"]`、required 2、matched 0、extra 0、series 3、cand 0、digest `e6f112…`、timed false |
| E4 | 同位置に系列 `artificial-period-extra-series`（receipt/revision `artificial-period-extra-receipt/-revision`）、subject lots/**0002**、時刻は E3 と同じ、区間は E1 の lots と同じ、`record_sha256` は lots 行 hash、`sha256`=`8782ffbc91427a0d131ecccb24b68d00b2f69270ef80665c81da526c2b467f4a` | `["EXTRA_SUBJECT_PRESENT"]`、required 2、**matched 2**、extra 1、series 3、cand 3、digest `e6f112…`、timed false |
| E5 | E3＋E4 | `["EXTRA_SUBJECT_PRESENT", "VALIDITY_NOT_SATISFIED"]`、matched 0、extra 1、series 4、cand 0 |
| E6 | 行履歴を lots の 1 entry だけに、期間 entries を lots の 2 entry（1 番目と 3 番目）だけに | `["HISTORY_SELECTION_MISSING"]`、required 2、matched 1、series 1、cand 1、digest `194ecdf56e9e60537e656fb096454483e00ebeb9e9e15909edf1bbcae6b4be62` |
| E7 | 行履歴 lots の payload を `{"code":"0001","lot_size":200}`、`sha256`=`53c5b4841c756e51f9b374840df775e2ac999cb745bb2688beaf8efa7c138985` | `["ROW_HASH_MISMATCH"]`、matched 1、series 2、cand 2、digest `3ec2332f1671ef0f9daa358faf33392fec4eb912ee96a68a79fd53fd56ece5d9` |
| E8 | 期間 events の `valid_until` を `2026-09-12T06:50:00+09:00`（= as_of）、`sha256`=`c40f164e158b14c6c56888028cbc38642a8f14867a6d65c529a8e2f57009f3df` | `["VALIDITY_NOT_SATISFIED"]`、matched 1、series 2、cand 1（半開区間の右端は候補にならない） |
| E9 | E3＋E2 | `DECISION_TIME_MISMATCH` 単独、全 0 |

E3 と E4 の期間束は単体期間 API でそれぞれ `PERIOD_OVERLAP_AT_DECISION` と成功になることも実測した（結合の理由へ正しく写っている）。E1〜E9 の ID・コードはすべて F-01 書式を満たす。

## 3. 判断 3 — 履歴 v2 の UTC 正規化契約（D10-04）

### 3-1 schema

`evidence_history_fixture_v2` は **v1 と同じ最上位 4 キー・entry 8 キー・公開出力 13 キー・17 理由**で、変わるのは mode 文字列と receipt 同一性の比較規則だけ。射影（digest）は v1 と同じ 4 項なので、同じ entries なら digest は v1 と一致する（E1 で確認）。

### 3-2 正規化の path と形

- 対象: `entries[].observed_at` と `entries[].recorded_at` の **2 path のみ**。
- 形: aware datetime を UTC へ変換し `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`（秒未満は常に 6 桁、`Z` は使わない）。
- 用途: receipt 同一性の比較用コピー（entry の 2 path を置換した dict）の canonical JSON だけ。**原本 entry・`payload`・`sha256`・`supersedes`・ID は書き換えない。** events payload の `next_earnings_at` も payload 内なので対象外。実測で、JST 表記と UTC 表記の同一 entry は正規化コピーの canonical が一致し、payload hash は変わらなかった。
- `decision_at` と結合側の `as_of` 比較は aware 比較で既に表記に依存しない（実測 True）ので正規化不要。

### 3-3 失敗優先と理由順

正規化不能（解釈不能・UTC 変換で範囲外）は `INVALID_TIMESTAMPS` に畳み、新語彙は足さない。位置は「キー/canonical 化 → 識別子 → **時刻解釈（新設）** → receipt 再投入/競合 → subject → observed>recorded と逆行 → payload → …」で、v1 の順に時刻解釈 1 段を receipt 判定の前へ挿入する。理由は、正規化コピーが作れない entry では NO_OP か競合かを決められないため。v1 との優先順の差は 2 組だけ（解釈不能な時刻＋receipt 再投入、解釈不能な時刻＋subject 破損。v1 は前者 `RECEIPT_CONFLICT`、後者 `INVALID_SUBJECT`。v2 はどちらも `INVALID_TIMESTAMPS`）。`observed_at > recorded_at` と `RECORDED_AT_REVERSED` の判定位置は v1 のまま。

範囲外の実測: `0001-01-01T00:00:00+09:00` と `9999-12-31T23:00:00-05:00` は UTC 変換で `OverflowError`。offset `+24:00` と `24:00:00` は解釈不能。`+23:59` は解釈可。

### 3-4 秒未満と表記の受入域（R11-01 と一体）

CPython 3.12.14（Codex 同梱）で `_aware` は 7 桁以上の秒未満を**黙って 6 桁へ切り捨て**、基本形式 `20260912T065000+0900` も受け付ける。この 2 つは Python 3.10 以前では拒否される（`fromisoformat` の受入域が 3.11 で広がった）。v2 契約は実行環境に依存しない受入域を明記する: 拡張形式 `YYYY-MM-DDTHH:MM:SS[.f{1,6}]±HH:MM|Z` のみ受け、秒未満 7 桁以上・基本形式・日付のみは `INVALID_TIMESTAMPS`。切り捨てを許すと `.1234567` と `.1234568` が同一 receipt 扱いになり、「同一 ID ⇒ 同一時点」の単射性が崩れる。

### 3-5 期間 v1 の対照

期間 v1 は完全一致規則のままなので、同じ entry を JST 表記で 1 回・UTC 表記で 1 回投入すると `RECEIPT_CONFLICT`（実測）。結合 v2 に渡しても段 3 で `INVALID_BUNDLE` に畳まれる。期間側にも同一性を求めるなら期間 v2 が別途必要で、今回の依存には含めない（D10-04 どおり）。

## 4. 指摘

### R11-01（低・契約の穴）`_aware` の受入域が Python 版に依存し、履歴 v2 の同一性契約が環境で変わり得る

再現条件: entry の `recorded_at` に `2026-09-12T06:50:00.1234567+09:00`（秒未満 7 桁）または `20260912T065000+0900`（基本形式）。
期待: 契約で受入域が固定され、どの実行環境でも同じ理由になる。
実測: CPython 3.12.14 では両方とも受理され、前者は 6 桁へ切り捨てられる。3.10 以前では `INVALID_TIMESTAMPS` になる（文書上の仕様差で、当方は 3.10 で実測していない）。
重要度: 低（v1 は完全一致規則なので実害なし。v2 で正規化を入れると 3-4 の単射性に触れる）。
修正案: 履歴 v2 の契約に 3-4 の受入域を明記し、実装では `_aware` の前に正規表現ゲートを置く。**v1 の `_aware` と既存 4 API は変更しない。**

## 5. 実測の区分

- 当方: 参照計算スクリプト 1 本（Dropbox 外）。既存モジュールを import して E1〜E9 の固定出力と正規化境界 13 種を計算。pytest は回していない（再実測 0 点。TEAM_WORKFLOW の「指摘に直結する 1 点のみ」の範囲内）。fuzz 0 束。
- Codex 実測: B に記載の 2026-09-14 R10 関連 207 passed / 2 skipped を引用しただけで、再実行していない。
- 環境: Windows 11、`C:/Users/s/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`（CPython 3.12.14）、`PYTHONDONTWRITEBYTECODE=1`。`python` コマンドは Windows Store のスタブで実行不能だった。

## 6. 残る限界・次の担当

上記はすべて設計期待で、結合 v2・履歴 v2 は未実装。E1〜E9 の固定値は Codex の実装後に製品試験で確定する。E3/E4 の追加 entry は examples へ書いていない（製品領域は Codex 所有）。次は Codex が本文書の段表・件数表・E1〜E9 を契約文書へ取り込み、採否を記載する。実装着手は D10-01〜05 の採否が契約文書に載ってからで、この文書だけで開始しない。`period_evidence_timed=true` は検査に通った意味にとどまり、実入手事実・真正性・実売買適格性を意味しない。

終了時刻: 2026-09-16 23:50 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、第11回レビュー（D10-01〜05 の確定案: 5段表・10理由の優先順・6件数と digest/timed の5クラス表・人工例 E1〜E9 と固定15キー出力・履歴 v2 の UTC 正規化契約・R11-01）の採否判断と契約文書への反映を行ってください。
```
