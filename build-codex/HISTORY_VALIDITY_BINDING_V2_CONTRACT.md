# 結合v2・行履歴v2 最小契約（第11回採用）

本書は第11回案を補正付きで採用した一次契約。HISTORY_VALIDITY_BINDING_V2_DECISIONS.mdのD10-01〜05保留に優先する。2026-09-17のユーザー続行指示で、行履歴v2と結合v2のメモリ内APIを実装。専用CLIも追加済み。DB保存・実取込は未実装。以下の参照計算の説明は契約採用時の履歴であり、最新製品実測はREADME末尾を参照。既存v1/API/_aware/試験期待値/例は変更しない。

## 採否と補正

- D10-01/05採用：5段、10理由、検証と評価を分離、時点一致後に各モデル1回、同時mode差は昇順併記。
- D10-02/03採用：extraは全履歴subjectの和集合から必須を除く。重複は候補全体を空にし、行digestと診断を保持する。時点差と防御失敗は6件数0。
- D10-04/R11-01採用：行entryの2 pathだけを比較用UTC正規化。時刻解釈をreceipt判定前へ移す。拡張形式・小数最大6桁に固定し、Pythonの広い受入へ依存しない。
- 件数表を補正：比較失敗のmatchedは0〜n（原案0〜n-1はE4と矛盾）。成功にはreason空が必要なのでmatched=nでも失敗し得る。
- 先行拒否表を補正：段4だけでなく段2も同段併記を許す。
- 行履歴17理由のうちSELECTION_HASH_INVALIDは評価由来。段3の検証器が17種類すべてを生成するという表現を16理由に補正し、評価防御は段5bへ固定する。

10理由はINVALID_BUNDLE / HISTORY_MODE_MISMATCH / PERIOD_HISTORY_MODE_MISMATCH / CODE_FORMAT_INVALID / IDENTIFIER_FORMAT_INVALID / DECISION_TIME_MISMATCH / HISTORY_SELECTION_MISSING / ROW_HASH_MISMATCH / VALIDITY_NOT_SATISFIED / EXTRA_SUBJECT_PRESENT。出力statusはVERIFIED_OFFLINE_BINDINGまたはDATA_INCOMPLETE。全結果でready_for_live/current_signal=false、read_only=true。timedは人工宣言時刻の整合であり実入手・真正性・実売買適格性を証明しない。

以下は第11回§1〜3を取り込み上記補正したもの。参照計算はClaudeの報告であり、Codexによる再実測でもv2製品実測でもない。原文のPython旧版挙動の説明は採否の根拠にせず、受入域自体を契約として採用する。

## 1. 判断 1 — 段表と理由優先（D10-01 / D10-05）

### 1-1 段表（結合 v2）

| 段 | 判定 | 理由 | 件数/digest/timed |
|---|---|---|---|
| 1 封筒 | 最上位が dict かつ 5 キー `mode/source_origin/input/history/period_history` ちょうど、mode=`history_validity_binding_fixture_v2`、origin=`offline_fixture` | `INVALID_BUNDLE` 単独 | 全 0 / null / false |
| 2 部品 mode 値差 | `history` が dict で `mode` キーがあり値≠`evidence_history_fixture_v2` → `HISTORY_MODE_MISMATCH`。`period_history` が dict で `mode` キーがあり値≠`period_evidence_history_fixture_v1` → `PERIOD_HISTORY_MODE_MISMATCH`。**両方なら 2 理由を昇順併記**（`["HISTORY_MODE_MISMATCH", "PERIOD_HISTORY_MODE_MISMATCH"]`）。依存 API は呼ばず、他キー・origin には依存しない | 上記 | 全 0 / null / false |
| 3 構造・内部整合 | `inspect_strict_input` が非 VERIFIED、履歴 v2 検証器が失敗dict（検証由来16理由。SELECTION_HASH_INVALIDは評価由来として段5b）、期間 v1 検証器が dict（**構造 18 理由すべて**）、`as_of` 解釈不能、必須集合が空。部品の順は input → history → period_history で、最初の不合格で打切り | `INVALID_BUNDLE` 単独 | 全 0 / null / false |
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
| 先行拒否（段 1〜4） | 単独または段2/段4の同段併記 | 0 | 0 | 0 | 0 | 0 | 0 | null | false |
| 時点差（段 5a） | `DECISION_TIME_MISMATCH` 単独 | 0 | 0 | 0 | 0 | 0 | 0 | null | false |
| 評価拒否（段 5b） | `INVALID_BUNDLE` 単独 | 0 | 0 | 0 | 0 | 0 | 0 | null | false |
| 比較失敗（段 5c） | 残り 4 理由の併記 | n | 0〜n | 実数 | 実数 | 実数 | 実数（重複なら 0） | 行 digest | false |

v1 が時点差でも件数を返していた診断契約は v2 では継承しない。v1 は時点判定の前に行モデルを評価していたので返せたが、v2 は「三者一致後に各 1 回」を骨格にしたため評価前に打ち切る。v1は据え置くが、入力schemaが異なるためv2束をv1へそのまま渡したり、診断のため自動降格したりしない。

### 2-3 最小人工入力（新規ファイル不要）

E1 = `examples/history_validity_binding_valid.json` の `input` と `history`（`history.mode` だけ `evidence_history_fixture_v2` へ）＋ `examples/period_evidence_history_valid.json` を `period_history` に＋最上位 `mode` を v2。三者の時点はいずれも `2026-09-12T06:50:00+09:00` で既に一致している。

**E1 の固定 15 キー出力**（参照計算のClaude参照計算での実測）:

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

E3 と E4 の期間束は単体期間 API でそれぞれ `PERIOD_OVERLAP_AT_DECISION` と成功になることもClaude参照計算での実測した（結合の理由へ正しく写っている）。E1〜E9 の ID・コードはすべて F-01 書式を満たす。

## 3. 判断 3 — 履歴 v2 の UTC 正規化契約（D10-04）

### 3-1 schema

`evidence_history_fixture_v2` は **v1 と同じ最上位 4 キー・entry 8 キー・公開出力 13 キー・17 理由**で、変わるのは mode 文字列と receipt 同一性の比較規則だけ。射影（digest）は v1 と同じ 4 項なので、同じ entries なら digest は v1 と一致する（E1 で確認）。

### 3-2 正規化の path と形

- 対象: `entries[].observed_at` と `entries[].recorded_at` の **2 path のみ**。
- 形: aware datetime を UTC へ変換し `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`（秒未満は常に 6 桁、`Z` は使わない）。
- 用途: receipt 同一性の比較用コピー（entry の 2 path を置換した dict）の canonical JSON だけ。**原本 entry・`payload`・`sha256`・`supersedes`・ID は書き換えない。** events payload の `next_earnings_at` も payload 内なので対象外。Claude参照計算での実測で、JST 表記と UTC 表記の同一 entry は正規化コピーの canonical が一致し、payload hash は変わらなかった。
- `decision_at` と結合側の `as_of` 比較は aware 比較で既に表記に依存しない（Claude参照計算での実測 True）ので正規化不要。

### 3-3 失敗優先と理由順

正規化不能（解釈不能・UTC 変換で範囲外）は `INVALID_TIMESTAMPS` に畳み、新語彙は足さない。位置は「キー/canonical 化 → 識別子 → **時刻解釈（新設）** → receipt 再投入/競合 → subject → observed>recorded と逆行 → payload → …」で、v1 の順に時刻解釈 1 段を receipt 判定の前へ挿入する。理由は、正規化コピーが作れない entry では NO_OP か競合かを決められないため。v1 との優先順の差は 2 組だけ（解釈不能な時刻＋receipt 再投入、解釈不能な時刻＋subject 破損。v1 は前者 `RECEIPT_CONFLICT`、後者 `INVALID_SUBJECT`。v2 はどちらも `INVALID_TIMESTAMPS`）。`observed_at > recorded_at` と `RECORDED_AT_REVERSED` の判定位置は v1 のまま。

範囲外のClaude参照計算での実測: `0001-01-01T00:00:00+09:00` と `9999-12-31T23:00:00-05:00` は UTC 変換で `OverflowError`。offset `+24:00` と `24:00:00` は解釈不能。`+23:59` は解釈可。

R12-01補足：UTC変換で年の表現範囲を超える方向は、年1側では正のオフセット、年9999側では負のオフセットに限る。オフセットがこの方向でも境界から十分離れていれば受理できる。実装変更は不要。

### 3-4 秒未満と表記の受入域（R11-01 と一体）

CPython 3.12.14（Codex 同梱）で `_aware` は 7 桁以上の秒未満を**黙って 6 桁へ切り捨て**、基本形式 `20260912T065000+0900` も受け付ける。旧Python版の挙動は今回未検証であり、採否の根拠にはしない。v2 契約は実行環境に依存しない受入域を明記する: 拡張形式 `YYYY-MM-DDTHH:MM:SS[.f{1,6}]±HH:MM|Z` のみ受け、秒未満 7 桁以上・基本形式・日付のみは `INVALID_TIMESTAMPS`。切り捨てを許すと `.1234567` と `.1234568` が同一 receipt 扱いになり、「同一 ID ⇒ 同一時点」の単射性が崩れる。

### 3-5 期間 v1 の対照

期間 v1 は完全一致規則のままなので、同じ entry を JST 表記で 1 回・UTC 表記で 1 回投入すると `RECEIPT_CONFLICT`（Claude参照計算での実測）。結合 v2 に渡しても段 3 で `INVALID_BUNDLE` に畳まれる。期間側にも同一性を求めるなら期間 v2 が別途必要で、今回の依存には含めない（D10-04 どおり）。


## 実装時に曖昧にしない補足

- 行履歴v2の狭い時刻書式ゲートはentry.observed_at/recorded_atの2 pathだけ。ASCII数字、区切りT、秒必須、任意小数は1〜6桁、末尾は大文字Zまたは±HH:MM。全体一致を要求する。日付・時刻が実在し、offset時は00〜23、分は00〜59。空白、小文字t/z、基本形式、offset秒、秒未満7桁以上、timezoneなし、うるう秒を拒否する。UTC変換後に年1〜9999へ収まらない場合もINVALID_TIMESTAMPS。正規化は年を4桁ゼロ埋めし小数6桁固定。
- decision_at、input.as_of、期間v1の時刻、payload内日時はこの新ゲート・書換えの対象外。decision_at等は対応する既存部品のaware受入と同時点比較を維持する。行履歴v2の変更はmodeだけでなく、2 pathの受入と失敗優先も含む。
- canonical化とID検査の後で2時刻を解釈・UTC変換し、比較用entryをcanonical化する。observed>recorded・逆行は従来位置で判定。検証済みの同一receipt再投入はNO_OPを逆行より先に扱う。原本payload/hashを正規化しない。
- 段4の行code検査はv1同様、strict_inputが選択したlots/events行を対象とする。履歴側は未来も含む全entryを検査。JSON PointerはID規則の対象外。
- 段5bは行評価から開始し、行digest失敗なら期間評価を呼ばず防御拒否。成功経路は各1回、打切り後の評価やstrict_validityの追加呼出はしない。期間重複時のdigest=Noneは正常な重複拒否であり、SELECTION_HASH_INVALIDと混同しない。その他の予期しない評価状態はINVALID_BUNDLEへリセットする。
- 期間重複理由とextraはsubject内短絡より先に立てる。その後、各必須subjectは行欠落→行hash差→期間候補/参照hash差の順。候補の半開期間条件は同じevaluate結果を使う。
- E1の入力はv1最上位を丸ごと残さず、mode/source_origin/input/history/period_historyの5キーへ組み直す。validity_documentsは除く。E3追加receipt/revisionはartificial-period-lots-receipt-x / artificial-period-lots-revision-xとする。E4のsupersedesはnull。E5はE3追加→E4追加→既存未来entryの順で挿入する（同記録時刻）。元の例は変更しない。

## E1〜E9 完全な固定15キー出力

以下は原文E1と差分表から全キーを展開した設計期待。Codexは製品計算・hash再計算をしていない。実装後に製品試験で照合する。

### E1

```json
{"corrected_after_as_of_count": 0, "current_signal": false, "extra_subject_count": 0, "matched_subject_count": 2, "mode": "history_validity_binding_fixture_v2", "period_candidate_count": 2, "period_evidence_timed": true, "period_series_count": 2, "read_only": true, "ready_for_live": false, "reason_codes": [], "required_subject_count": 2, "selection_sha256": "e6f112d03807f3f9c806106890a95900a11680e150a037557c913b427db22bec", "source_origin": "offline_fixture", "status": "VERIFIED_OFFLINE_BINDING"}
```

### E2

```json
{"corrected_after_as_of_count": 0, "current_signal": false, "extra_subject_count": 0, "matched_subject_count": 0, "mode": "history_validity_binding_fixture_v2", "period_candidate_count": 0, "period_evidence_timed": false, "period_series_count": 0, "read_only": true, "ready_for_live": false, "reason_codes": ["DECISION_TIME_MISMATCH"], "required_subject_count": 0, "selection_sha256": null, "source_origin": "offline_fixture", "status": "DATA_INCOMPLETE"}
```

### E3

```json
{"corrected_after_as_of_count": 0, "current_signal": false, "extra_subject_count": 0, "matched_subject_count": 0, "mode": "history_validity_binding_fixture_v2", "period_candidate_count": 0, "period_evidence_timed": false, "period_series_count": 3, "read_only": true, "ready_for_live": false, "reason_codes": ["VALIDITY_NOT_SATISFIED"], "required_subject_count": 2, "selection_sha256": "e6f112d03807f3f9c806106890a95900a11680e150a037557c913b427db22bec", "source_origin": "offline_fixture", "status": "DATA_INCOMPLETE"}
```

### E4

```json
{"corrected_after_as_of_count": 0, "current_signal": false, "extra_subject_count": 1, "matched_subject_count": 2, "mode": "history_validity_binding_fixture_v2", "period_candidate_count": 3, "period_evidence_timed": false, "period_series_count": 3, "read_only": true, "ready_for_live": false, "reason_codes": ["EXTRA_SUBJECT_PRESENT"], "required_subject_count": 2, "selection_sha256": "e6f112d03807f3f9c806106890a95900a11680e150a037557c913b427db22bec", "source_origin": "offline_fixture", "status": "DATA_INCOMPLETE"}
```

### E5

```json
{"corrected_after_as_of_count": 0, "current_signal": false, "extra_subject_count": 1, "matched_subject_count": 0, "mode": "history_validity_binding_fixture_v2", "period_candidate_count": 0, "period_evidence_timed": false, "period_series_count": 4, "read_only": true, "ready_for_live": false, "reason_codes": ["EXTRA_SUBJECT_PRESENT", "VALIDITY_NOT_SATISFIED"], "required_subject_count": 2, "selection_sha256": "e6f112d03807f3f9c806106890a95900a11680e150a037557c913b427db22bec", "source_origin": "offline_fixture", "status": "DATA_INCOMPLETE"}
```

### E6

```json
{"corrected_after_as_of_count": 0, "current_signal": false, "extra_subject_count": 0, "matched_subject_count": 1, "mode": "history_validity_binding_fixture_v2", "period_candidate_count": 1, "period_evidence_timed": false, "period_series_count": 1, "read_only": true, "ready_for_live": false, "reason_codes": ["HISTORY_SELECTION_MISSING"], "required_subject_count": 2, "selection_sha256": "194ecdf56e9e60537e656fb096454483e00ebeb9e9e15909edf1bbcae6b4be62", "source_origin": "offline_fixture", "status": "DATA_INCOMPLETE"}
```

### E7

```json
{"corrected_after_as_of_count": 0, "current_signal": false, "extra_subject_count": 0, "matched_subject_count": 1, "mode": "history_validity_binding_fixture_v2", "period_candidate_count": 2, "period_evidence_timed": false, "period_series_count": 2, "read_only": true, "ready_for_live": false, "reason_codes": ["ROW_HASH_MISMATCH"], "required_subject_count": 2, "selection_sha256": "3ec2332f1671ef0f9daa358faf33392fec4eb912ee96a68a79fd53fd56ece5d9", "source_origin": "offline_fixture", "status": "DATA_INCOMPLETE"}
```

### E8

```json
{"corrected_after_as_of_count": 0, "current_signal": false, "extra_subject_count": 0, "matched_subject_count": 1, "mode": "history_validity_binding_fixture_v2", "period_candidate_count": 1, "period_evidence_timed": false, "period_series_count": 2, "read_only": true, "ready_for_live": false, "reason_codes": ["VALIDITY_NOT_SATISFIED"], "required_subject_count": 2, "selection_sha256": "e6f112d03807f3f9c806106890a95900a11680e150a037557c913b427db22bec", "source_origin": "offline_fixture", "status": "DATA_INCOMPLETE"}
```

### E9

```json
{"corrected_after_as_of_count": 0, "current_signal": false, "extra_subject_count": 0, "matched_subject_count": 0, "mode": "history_validity_binding_fixture_v2", "period_candidate_count": 0, "period_evidence_timed": false, "period_series_count": 0, "read_only": true, "ready_for_live": false, "reason_codes": ["DECISION_TIME_MISMATCH"], "required_subject_count": 0, "selection_sha256": null, "source_origin": "offline_fixture", "status": "DATA_INCOMPLETE"}
```

更新：2026-09-17 05:39:17 JST


## v2 APIの入口（2026-09-17追加）

```python
from aitrader.evidence_history_fixture_v2 import inspect_evidence_history
from aitrader.history_validity_binding_v2 import inspect_history_validity_binding
```

行履歴v2検証器はv1の不変な内部評価モデルを共用し、receipt比較用の2時刻だけ別の検証経路でUTC正規化する。結合v2は行履歴v2と期間履歴v1の内部検証・評価を直接利用し、外部からのvalidity_documentsは受け取らない。v1の公開入口・CLIは不変。

新規examples/history_validity_binding_v2_valid.jsonがE1人工束。tests/binding_v2_expected.jsonは本書のE1〜E9を固定値として保存し、test_history_validity_binding_v2.pyで実装出力と照合する。出力を生成して期待値へ上書きする試験にはしない。

API実装後の続行工程で下記v2専用CLIを追加した。既存v1 CLIにv2束を渡すよう案内しない。v2 API成功も現在売買シグナル・実口座利用の承認ではない。

## 第11回固定依頼の再照合（2026-09-17 06:12:46 JST）

依頼hash: `2ec79f6748f9a92f13d1ca8f5eb4401d077788154fad3bffaa8f3f9e2cda3f68`。D10-01〜05・R11-01の補正付き採用を維持。5段・10理由、6件数と5結果クラス、E1〜E9の15キー出力、E3/E4の追加hash、2 path限定UTC正規化と受入域を照合した。旧Python版の未検証断定のみ整理し契約値は不変。既存API実装と製品試験は別の続行作業の成果であり、今回の実装・再試験ではない。


## v2専用CLIの採用契約（2026-09-17）

ユーザーの次工程続行指示により、既存v1と同じ安全読取・出力規約を持つ別モジュールを採用する。v1 CLIへ自動版判定は追加しない。

| 用途 | モジュール | API出力キー数 | 成功status |
|---|---|---:|---|
| 行履歴v2 | aitrader.evidence_history_fixture_v2_cli | 13 | VERIFIED_OFFLINE_HISTORY |
| 結合v2 | aitrader.history_validity_binding_v2_cli | 15 | VERIFIED_OFFLINE_BINDING |

いずれもpython -m モジュール --input PATHで、--input必須。packet_cli._mappingとstrict_input_cli._Parserをそのまま共用する。1MiBちょうどを許容、超過はAPIを呼ばず読取失敗。link/reparse・読取中変更・重複キー・非有限値の拒否は既存読取契約を維持する。

API成功は終了0・stdoutにJSON1行、不成功判定は終了2・stdoutにJSON1行。引数/読取/API例外/JSON出力生成例外は終了2・stdout空・stderrに次の固定文と改行だけを出す。本文・パス・生例外を出さない。

- 行履歴：証拠履歴を検査できません。入力形式と訂正の参照を確認してください。
- 結合：履歴と適用期間を検査できません。入力形式とサイズを確認してください。

DB/homeの作成、保存、実接続は行わない。v2 CLIの成功もready_for_live/current_signal=false。入力ファイルのサイズ制限はCLI読取境界であり、メモリ内APIに新たな制限を課さない。

結合の人工例を実行する場合（build-codexを作業場所とする）：
```text
python -m aitrader.history_validity_binding_v2_cli --input examples/history_validity_binding_v2_valid.json
```
行履歴CLIへはその束全体ではなくhistory部分を取り出したJSONファイルを渡す。取り出し先・診断結果はDropbox外に置き、既存v1例のmodeを上書きしない。

CLI実装確認：2026-09-17 14:41:36 JST。Sol配下で新規18 passed/3.14秒、関連171 passed/1 skipped/0.97秒。全体再実行なし。

## 第13回 R13-01 採否（2026-09-17）

(b) 契約に例外を明記して現状維持を採用する。両v2 CLIで `-h/--help` はusageをstdoutへ出し、stderr空・終了0となる。helpは入力検査結果ではなく利用案内であり、`--input` 必須、stdoutがJSON1行または空、終了0がAPI成功を表すという規約の例外とする。help時は入力読取・検査APIに到達しない。機械呼出側はhelp出力を検査成功や売買承認として扱わない。

理由：共有 `_Parser` の既定helpと既存v1 CLIとの一貫性を保ち、利用案内を残したまま出力契約の曖昧さを解消できるため。(a)はv2だけhelpを引数エラーにする必要がなく不採用、(c)は今回の範囲外であるv1変更を伴うため不採用。製品・試験は変更しない。データ検査経路の固定stderr・終了2・安全読取契約は維持する。

一次資料：CLI_V2_INDEPENDENT_REVIEW.md（Claude第13回）。バグ0・独立反証30件全通過の報告を受領。指定5ファイルのCodex Windows実測はREADME末尾に記録する。保存契約D13-01〜03は別依頼とし、本工程には含めない。
