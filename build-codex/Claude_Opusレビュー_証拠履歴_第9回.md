# Claude Opus 独立レビュー 第9回: R8-01〜05 の対応と、採用された期間履歴の最小契約

2026-09-13。実施者 Claude Opus（Cowork、Linux コンテナ）。依頼は[キャッチボール文書](Claude_Opusキャッチボール.md)の B（更新 2026-09-13 08:37 JST）。第1〜8回は[原レビュー](Claude_Opusレビュー_証拠履歴.md)、[第2回](Claude_Opusレビュー_証拠履歴_第2回.md)〜[第8回](Claude_Opusレビュー_証拠履歴_第8回.md)。

今回も観点ごとに 7 体のサブエージェントを並列で走らせ、親がすべて再現・検算して統合した。裏の取れなかった主張は本文へ入れていない。

実装コード・既存試験の期待値・共通仕様・合成データ・専用例は変更していない。追加したのは専用の新規試験 1 ファイルのみ。実 API・売買審査・LINE・証券接続・発注・見張り再開・Claude 公開はいずれも行っていない。一時ファイルはすべて Dropbox 外のレビュー用コンテナに置いた。

## 結論

**R8-01〜05 は 5 件とも推奨どおり採用され、矛盾は見つからなかった。**期間履歴の最小契約は、20 理由・判定順序・件数・digest・写し規則のいずれも一意に読める水準まで具体化されている。依頼が名指しした論点（20 理由への補足、理由優先、系列の subject 固定と束全体の revision 識別、`selected_series_count` と有効候補 digest の差、0/1/2 候補、行 hash で事前選別しない写し、未来・期限外 subject が v1 から見えない限界）は、いずれも文書から一意に決まることを確認した。**実装バグは 0 件で、製品コードは第8回時点とバイト単位で同一である。**

**Codex が確認を求めた点は正しい。**「反証表の『同 series の subject 変更 → SERIES_REFERENCE_INVALID』は新 receipt・新 revision を前提とし、同じ revision_id を再使用すれば先に REVISION_CONFLICT になる」という読みは、判定順序の記述とも、手本である履歴 v1 の実装挙動とも一致する（実測で確認）。

**残る指摘は 5 件。**実装着手前に決めておきたいのは R9-01 だけで、これは「期間履歴が受理する `revision_id` が、結合 v1 の ID 書式を満たすとは限らない」という**二文書にまたがる非対称**である。R8-03（無変換で写す）と「ASCII 制限を先取りしない」を素直に組み合わせると、期間履歴単体では成功するのに結合で必ず落ちる束が作れる（5 種の ID で実測）。残る 4 件は文言の穴で、実装を止めるものではない。

宿題（人工入力例・固定出力・CLI の最小具体案）は本文の後半に置いた。hash はすべて実際に計算した値で、**提案した期間 payload は既存の専用例 `history_validity_binding_valid.json` の期間証拠とバイト単位で一致する**ため、写しがそのまま結合 v1 を通ることまで実測で確かめてある。

## 検証環境と実測（Codex 実測と区別）

Linux コンテナ、CPython 3.11.15、pytest 9.1.1。Windows 実機・Codex ランタイムでは実行していない。対象を読み取り専用で複製し、Dropbox へ書き戻していない（本レビュー文書と追加試験の 2 ファイルを除く）。

**「製品コード・既存試験期待値・専用例・共通仕様・合成データは変更なし」を全数照合で裏取りした。**第8回時点の作業コピーと今回の `aitrader/` `tests/` `examples/` を `diff -r` と sha256 で比較し、差異は 0。期間履歴の mode 文字列（`period_evidence_history_fixture_v1`）は実装・試験・例のどこにも存在せず、「設計採用のみで製品実装なし」という申告と実体が一致する。

私が実行した関連範囲は **22 ファイル 474 passed、1.53 秒、失敗 0 件**。Codex 報告「473 passed / 1 skipped / 1.71 秒」と合計 474 で一致する（差の 1 件は実 symlink 作成試験。当方は root 権限のため skip されず通過）。今回追加した 14 件を含めると **488 passed、1.35 秒**である。

**Codex 全体実測（前回 2728 passed / 10 skipped / 562 warnings / 305.83 秒）は Codex の実測で、私は再実行していない。**README 末尾がこれを「後続追加を含まない前回全体実測」と断り、私の 474 件・過去の 6,000 束・2,428 束・4,000 束を「Codex 再実測ではない」と区別している点も確認した。帰属の誤りはない。

## 重点項目への回答

| 重点 | 判定 |
|---|---|
| 20 理由への補足（INVALID_HASH の追加） | **妥当。落ち先の無い条件はない。ただし 3 箇所で条件→理由の対応が未記載（R9-02）** |
| 理由優先（最小の判定順序） | **一意に読める。NO_OP と逆行検査、再取得と系列固定の先後はいずれも曖昧でない** |
| 系列の subject 固定 / 束全体の revision 識別 | **指摘なし。Codex の REVISION_CONFLICT 先行の読みは正しい（実測）** |
| `selected_series_count` と有効候補 digest の差 | **指摘なし。区間外を含む選択数と、有効候補だけの digest が読み分けられる** |
| 0 / 1 / 2 候補 | **指摘なし。0 は単体成功・写さない、1 は写す、2 は全体拒否で 6 件数 0・digest null** |
| 行 hash で事前選別しない写し | **指摘なし。5 通りの写しを結合 v1 へ実際に通して確認** |
| 未来・期限外 subject が v1 に見えない限界 | **指摘なし。写し規則を守る限り誤って成功しない。ただし出所は検証されない（R9-05）** |
| 人工例・固定出力・CLI の最小案 | **本文後半に提示（hash は実測値）** |

## R8-01〜05 の反映確認

5 件とも推奨どおりの採用で、条件付き採用や別案はなかった。

| 指摘 | 反映 |
|---|---|
| R8-01 系列キー | `series_id` を投入側が宣言し、同系列内は線形訂正、追加区間は別系列。区間の重なりから訂正／追加を推測しない、と明記された |
| R8-02 有効候補 | 各系列の過去選択後、subject ごとに区間内候補 1 件なら写す、0 件は写さず欠落、2 件以上は**同 hash でも**全体拒否 |
| R8-03 文書 ID | 選択された期間 `revision_id` を `validity_documents.id` へ無変換で写す（**R9-01 の非対称が残る**） |
| R8-04 timed 昇格 | 結合 v2 のみ。結合 v1 は false 固定、期間履歴 API は当該フラグを**出さない**。両文書に記載を確認 |
| R8-05 例外境界 | 「json.loads 由来の素の dict/list/str/int/float/bool/None だけを信頼し、メソッドを差し替えた Python オブジェクトは契約外」が**結合 v1 側（第8回節）と期間履歴側の両方**に入った |

R8-04 と R8-05 は、片方の文書だけに書かれて終わる形になりやすい指摘だったが、どちらも両文書に入っている。結合 v1 の 13 キー・閉じた 9 理由・`period_evidence_timed=false` 固定が「変更しない」と書かれ続けていることも、実装（`_result` が返す 13 キーと 9 理由）と突き合わせて確認した。

## 採用契約の点検

### 系列と識別子（指摘なし）

Codex が確認を求めた「同 revision 再使用なら REVISION_CONFLICT が先行する」を、履歴 v1 の実装で実測した。同じ `revision_id` を別 subject の初版として再投入すると `REVISION_CONFLICT`、同一 subject で payload だけ変えても `REVISION_CONFLICT` になる。`_validate_history` は `revision_id in revisions` の同一性判定を、`heads` を使う参照整合判定より先に実行しており、期間履歴の判定順序（…supersedes 形 → revision 再取得／競合 → series の subject 固定 → 訂正参照…）も同じ骨格である。したがって反証表の当該行は「新 receipt・新 revision」を前提とした期待として正しく、矛盾はない。

`receipt_id` / `revision_id` を束全体で一意とし、行履歴側と独立の名前空間とする規定にも曖昧さはない。同一 subject に複数の `series_id` を置けること（追加区間）も明記されている。

### 件数と digest（指摘なし）

出力 14 キーのうち件数は `receipt_count` / `revision_count` / `noop_receipt_count` / `series_count` / `selected_series_count` / `future_revision_count` の 6 つで、「不成功時は 6 件数すべて 0」の 6 と一致する。数え直して確認した。

`selected_series_count` は区間外の選択も含む系列数、digest は有効候補だけの射影、「配列長と一致しなくてよい」という関係も一貫している。有効候補 0（全系列が区間外）でも単体は成功し digest は空配列 hash、1 なら 1 要素、同一 subject で 2 なら `PERIOD_OVERLAP_AT_DECISION` で全体拒否・6 件数 0・digest null。3 通りとも本文と反証表から一意に読める。

`future_revision_count`（初出記録が判定後の一意 revision 数、初版を含む、再取得は加算しない）は、履歴 v1 の実装挙動と一致していることを `evaluate` のコードで確認した。射影の順序も、同一 (group, code) に有効候補が 2 件以上あれば digest 計算前に拒否されるため一意に決まる。

### 写しと結合 v1 の噛み合わせ（実測）

写し規則どおりに作った `validity_documents` を、実装済みの結合 v1 へ実際に通した。

| 写し方 | 結合 v1 の結果 | required/matched/extra/corrected |
|---|---|---|
| 全 subject の有効候補（`id` は revision_id） | `VERIFIED_OFFLINE_BINDING` | 2/2/0/0 |
| 1 subject が 0 候補で要素なし | `VALIDITY_NOT_SATISFIED` | 2/1/0/0 |
| 全 subject 0 候補（空 list） | `VALIDITY_NOT_SATISFIED` | 2/0/0/0 |
| 余分 subject の有効候補も渡す | `VALIDITY_NOT_SATISFIED` | 2/2/0/0 |
| 行 hash が合わない候補も隠さず渡す | `VALIDITY_NOT_SATISFIED` | 2/1/0/0 |
| 区間外の版を写した場合 | `VALIDITY_NOT_SATISFIED` | 2/1/0/0 |

いずれも契約どおりで、写し規則を守る限り誤って成功する束は作れなかった。余分 subject は `EXTRA_SUBJECT_PRESENT`（履歴 subject 集合の診断）ではなく `VALIDITY_NOT_SATISFIED`（対象集合不一致）として現れる点も、文書の記述と一致する。

## 指摘事項

### R9-01（中・設計課題）期間履歴が受理する revision_id が、結合 v1 の ID 書式を満たすとは限らない

再現条件: 期間履歴で `revision_id` を `リビジョン-001` や `revision 001`（内部空白）、ゼロ幅文字入り、`_leading`（記号始まり）、129 文字にする。いずれも期間履歴の契約（非空 trim 済み文字列、ASCII 追加制限は先取りしない）では有効である。この候補を R8-03 どおり `validity_documents.id` へ無変換で写す。

期待: 期間履歴で有効な候補が、結合 v1 でも受理されること。少なくともその可否が文書から読めること。

実測: 5 種すべてで結合 v1 は段4 の `IDENTIFIER_FORMAT_INVALID`、4 件数 0、digest null になった（ASCII で 128 文字なら通る）。結合 v1 の F-01 は「原本 document ID、**期間 document ID**、receipt/revision ID と参照 ID へ同じ ID 規則を使う」と定めており、`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` を満たさない `id` は必ず落ちる。一方、期間履歴の文書は「ASCII 追加制限は既存結合境界だけの契約を維持し、この独立 API には先取りしない」と書くだけで、写した先で落ちることに触れていない。**期間履歴単体は成功するのに、結合では必ず失敗する束**が作れる。

重要度: 中（実害はまだ出ない。期間履歴は未実装のため。ただし実装後に「写すだけでよい」と読んだまま採番すると、書式起因の恒常的な結合失敗を招く）。

推奨する単一の結果: 期間履歴の「結合 v1 への写し方」節へ 1 文足す。「結合へ写す運用を想定する場合、`revision_id` は結合境界の ASCII ID 書式（`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`）で採番する。期間履歴 API 自体はこれを強制しないが、満たさない候補を写すと結合 v1 の段4 で `IDENTIFIER_FORMAT_INVALID` になる」。API 側に書式を課す案もあるが、「先取りしない」という決めと衝突するので前者を推す。

### R9-02（低・文書）条件と理由の対応に 3 箇所の穴がある

いずれも 20 種の集合そのものではなく、「どの条件がどの理由に落ちるか」の記載漏れである。

第一に、判定順序の「payload 形と subject 一致」の段に対応する理由名が書かれていない。本文が `INVALID_PAYLOAD` として明示しているのは `record_sha256` の書式破損だけで、payload が 5 キーちょうどでない場合や `group`/`code` が subject と一致しない場合の落ち先は名称からの類推になる。`INVALID_PAYLOAD` と読むのが自然なので、その旨を明記したい。なお履歴 v1 の `INVALID_PAYLOAD` は行 payload 検証器（`_lot`/`_event`）の失敗を指すが、期間履歴はその検証器を使わないと明記しているため、**同じ名前で中身が別物**になる。これも 1 文あるとよい。

第二に、`SELECTION_HASH_INVALID` の発火条件が本文に無く、20 種のリストにしか現れない。履歴 v1 では「選択 hash 生成失敗は従来どおり固定拒否」と明記されている（第6回対応）。期間履歴にも同じ一文が要る。なお射影の各要素は検証済みの文字列だけなので、履歴 v1 と同じく実質到達不能な防御ガードになるはずである。

第三に、「`receipt_id`/`revision_id`/`series_id`/`code` は非空 trim 済み文字列」と一文で並ぶ一方、判定順序は識別子の段と subject の段を分けている。空の `code` が `INVALID_IDENTIFIER` と `INVALID_SUBJECT` のどちらになるか読めない。履歴 v1 の実装は `code` の trim を subject 側で見ているので、それに合わせて「識別子の段が見るのは `receipt_id`/`revision_id`/`series_id`、`code` は subject の段」と書き分けるのが安全である。

重要度: 低（3 件とも 1 文ずつで閉じる）。

### R9-03（低・文書）既知 series_id に対する 2 つ目の「初版」の落ち先

再現条件: すでに登録済みの `series_id` に対し、新しい `revision_id` で `supersedes=null` の entry を投入する（subject は同じでも違ってもよい）。

期待: 理由が一意に読めること。

実測（文書読解）: 「各 series の初版は `supersedes=null`」「同系列の…分岐…を拒否」「未知・前方・自己・分岐・参照 hash 差は `INVALID_SUPERSEDES`」から `INVALID_SUPERSEDES` と読むのが自然だが、直接の記述はない。履歴 v1 の実装は同じ状況（`heads` に既存の head があるのに `supersedes` が null）を `INVALID_SUPERSEDES` にしており、この読みと一致する。

推奨: 「既知 series に対する 2 つ目の `supersedes=null` は分岐として `INVALID_SUPERSEDES`（`SERIES_REFERENCE_INVALID` ではない）」と 1 文。

### R9-04（低・文書）digest の射影に使う sha256 の出所

`selection_sha256` の射影 `{group, code, series_id, revision_id, sha256}` の `sha256` が、`entry.sha256`（期間 payload の自己 hash）なのか `payload.record_sha256`（行の hash）なのかが、定義箇所だけでは読み取れない。写し方の節には「sha256: 期間 payload の hash」とあるので前者と分かるが、離れている。成功経路では `PAYLOAD_HASH_MISMATCH` 検査によって両者の一致が保証されるため実装が割れる余地はないものの、1 語の補足で確実になる。併せて、この digest が結合 v1 の `selection_sha256`（行選択の digest）とは別物であることも 1 文あるとよい。

### R9-05（低・文書）結合 v1 は候補の出所を検証しない

再現条件: 期間履歴をまったく通さずに、区間・hash だけ辻褄の合う `validity_documents` を手で作って結合 v1 へ渡す。

実測: `VERIFIED_OFFLINE_BINDING` になる。実際、現在の専用例 `history_validity_binding_valid.json` 自体が期間履歴を経ていない人工束であり、成功する。結合 v1 は `validity_documents` が「期間履歴全体の合格から写されたもの」かを一切検査せず、digest の突き合わせもしない。

文書は「期間履歴 API 成功と結合 v1 成功を AND しただけでは、投入側が同じ選択を写したことを機械的に証明できない」と正しく書いている。ただ読み手には「AND すれば弱いながら証拠になる」とも読めるので、**写し規則を守らない候補でも結合 v1 は成功する**と一段具体化しておきたい。そうすると、結合 v2 の昇格条件に「共用選択モデルによる対応付け」が要る理由がそのまま伝わる。

重要度: 低（設計どおりの限界。誤りではない）。

## 宿題への回答: 人工入力例・固定出力・CLI の最小案

### 人工入力例（`examples/period_evidence_history_valid.json` 相当）

lots は「判定前の初版 ＋ 判定後の訂正」、events は単一初版。`decision_at` は既存例と同じ。`record_sha256` は既存の結合例の入力行 hash と一致させてあるので、**この例の有効候補をそのまま結合 v1 へ写せる**。すべての hash は実際に計算した値である。

```json
{
  "mode": "period_evidence_history_fixture_v1",
  "source_origin": "offline_fixture",
  "decision_at": "2026-09-12T06:50:00+09:00",
  "entries": [
    {
      "receipt_id": "artificial-period-lots-receipt-1",
      "revision_id": "artificial-period-lots-revision-1",
      "series_id": "artificial-period-lots-series",
      "subject": {"group": "lots", "code": "0001"},
      "observed_at": "2026-09-10T16:00:00+09:00",
      "recorded_at": "2026-09-10T16:00:00+09:00",
      "payload": {
        "group": "lots", "code": "0001",
        "record_sha256": "637cb0d97fcc5281cdbbd278a5b392150ad21e1def6bf614c1d5557993d351f5",
        "valid_from": "2026-09-10T00:00:00+09:00",
        "valid_until": "2026-09-13T00:00:00+09:00"
      },
      "sha256": "be8b261315746eca37ee4ad490e7830cade77dc52039cb1d0acec7bbc8e74a22",
      "supersedes": null
    },
    {
      "receipt_id": "artificial-period-events-receipt-1",
      "revision_id": "artificial-period-events-revision-1",
      "series_id": "artificial-period-events-series",
      "subject": {"group": "events", "code": "0001"},
      "observed_at": "2026-09-11T17:00:00+09:00",
      "recorded_at": "2026-09-11T17:01:00+09:00",
      "payload": {
        "group": "events", "code": "0001",
        "record_sha256": "89d0ec1b85e3300cd81e50da7ff3930fd60cf4a15d83a45992632a364097cde5",
        "valid_from": "2026-09-10T00:00:00+09:00",
        "valid_until": "2026-09-13T00:00:00+09:00"
      },
      "sha256": "f40e4cf4bc9cc5a1f3b3799d467837f17bda613b25f3f6375936360df01cdd5d",
      "supersedes": null
    },
    {
      "receipt_id": "artificial-period-lots-receipt-2",
      "revision_id": "artificial-period-lots-revision-2",
      "series_id": "artificial-period-lots-series",
      "subject": {"group": "lots", "code": "0001"},
      "observed_at": "2026-09-12T07:00:00+09:00",
      "recorded_at": "2026-09-12T07:00:00+09:00",
      "payload": {
        "group": "lots", "code": "0001",
        "record_sha256": "637cb0d97fcc5281cdbbd278a5b392150ad21e1def6bf614c1d5557993d351f5",
        "valid_from": "2026-09-10T00:00:00+09:00",
        "valid_until": "2026-09-14T00:00:00+09:00"
      },
      "sha256": "65458caadf9eed555a97cf9f823e72d113c5a9ec237085b86e07bfa1b10dc96a",
      "supersedes": {
        "revision_id": "artificial-period-lots-revision-1",
        "sha256": "be8b261315746eca37ee4ad490e7830cade77dc52039cb1d0acec7bbc8e74a22"
      }
    }
  ]
}
```

この例は、最上位 4 キー・entry 9 キー・payload 5 キー・subject との一致・timezone 付き時刻・`observed_at <= recorded_at`・`valid_from < valid_until`・`recorded_at` の束全体非減少をすべて満たす。lots の訂正は `valid_until` だけを直し `record_sha256` は変えない線形訂正で、記録が判定後なので選択は初版のままになる。

**この例の lots / events の期間 payload は、既存の専用例 `history_validity_binding_valid.json` の `validity_documents` の payload とバイト単位で同一である**（hash も `be8b2613…4a22` / `f40e4cf4…dd5d` で一致）。つまり有効候補をそのまま写せば、既存の結合例と同じ束になる。実測でも `VERIFIED_OFFLINE_BINDING`・2/2/0/0 を確認した。

### 固定出力（14 キー）

```json
{
  "mode": "period_evidence_history_fixture_v1",
  "source_origin": "offline_fixture",
  "status": "VERIFIED_OFFLINE_PERIOD_HISTORY",
  "reason_codes": [],
  "receipt_count": 3,
  "revision_count": 3,
  "noop_receipt_count": 0,
  "series_count": 2,
  "selected_series_count": 2,
  "future_revision_count": 1,
  "selection_sha256": "ba6486dd33d76cf9594f6e43795f45c70349bee87e40fa6f13e0abcb01d186ca",
  "ready_for_live": false,
  "current_signal": false,
  "read_only": true
}
```

内訳は、一意 receipt 3・一意 revision 3・NO_OP 0・系列 2（lots と events）・過去選択のある系列 2・判定後の初出 revision 1（lots の訂正）。`selection_sha256` は有効候補 2 件を `{group, code, series_id, revision_id, sha256}` で `(group, code)` 順（events が先）に並べた canonical JSON の SHA-256 で、実際に計算した値である。

### CLI の最小案

採用してよい。既存 3 CLI と同じ骨格で、安全読取は `packet_cli._mapping` をそのまま流用する（link/reparse 拒否、読取中の変化検出、重複キー拒否、非有限値拒否、1MiB ちょうど通過・超過拒否がそのまま効く。新規の読取実装は不要）。

- 本体 `aitrader/period_evidence_history_fixture.py`、入口 `inspect_period_evidence_history(bundle)`。CLI は `aitrader/period_evidence_history_fixture_cli.py`。
- 固定 stderr は既存の文体に合わせて「期間証拠履歴を検査できません。入力形式と系列の参照を確認してください。」
- 終了コードは、成功 0（stdout に 14 キー JSON 1 行）、不成功 2（stdout に JSON、単独理由）、引数・読取・その他の例外は 2 で stdout 空・固定 stderr 1 行。
- サイズ超過は検査を呼ばずに固定 stderr・終了 2・stdout 空。結合 CLI と同じ扱いにする。

### 最小の製品試験（14 件）

人工例の 14 キー固定値／NO_OP（receipt 不変・noop 加算）／`RECEIPT_CONFLICT` が NO_OP より優先されること／同 `revision_id` の使い回しが `REVISION_CONFLICT`（系列固定検査より先）／同系列の subject 変更が `SERIES_REFERENCE_INVALID`／`supersedes` の別系列参照が `SERIES_REFERENCE_INVALID`／未知・前方・自己・分岐・循環が `INVALID_SUPERSEDES`／`recorded_at` の逆行が `RECORDED_AT_REVERSED`／自己 hash 差が `PAYLOAD_HASH_MISMATCH`／`entry.sha256`・`record_sha256`・`supersedes.sha256` の書式破損が別々の理由になること／`PERIOD_INTERVAL_INVALID`（timezone 欠損・解釈不能・開始 >= 終了）／同 subject の有効候補 2 件で `PERIOD_OVERLAP_AT_DECISION`・6 件数 0・digest null／未来だけの系列は `selected_series_count` に入らず単体成功しうること／CLI の終了コードと固定 stderr と 1MiB 境界。

## 追加した試験

`build-codex/tests/test_period_copy_boundary_opus.py`（14 件、0.04 秒、全通過）を新規追加した。既存試験の期待値と実装コードは変更していない。

期間履歴 API は未実装なので、固定したのは**写しを受け取る側（実装済みの結合 v1）の挙動**である。写し規則どおりなら成功すること、提案した期間 payload が既存の専用例とバイト単位で一致すること、`revision_id` が結合境界の ASCII 書式を満たさないと必ず `IDENTIFIER_FORMAT_INVALID` になること（5 種＋128 文字の境界）、0 候補・空 list・余分 subject・行 hash 不一致・区間外の 5 通りが結合 v1 の診断に正しく現れること、そして結合 v1 が候補の出所を検証しないこと（R9-05 の限界）を機械可読にした。

## 指摘なしと確認した範囲

20 理由に落ち先の無い条件はない。到達不能なのは `SELECTION_HASH_INVALID` だけで、これは履歴 v1 と同じ fail-closed のガードである。逆に `INVALID_HASH` は、履歴 v1 では行 payload 検証器が先に弾くため実質到達不能だったが、期間履歴はその検証器を使わないため到達可能になる。補って正解である。

判定順序は一意に読める。NO_OP を逆行検査より先に扱うこと、revision の再取得・競合を系列の subject 固定より先に見ること、全 entry の構造検証を終えてから選択・重複・digest を評価することは、いずれも矛盾なく並んでいる。「期間重複によって壊れた未来 entry の検査を飛ばさない」という一文は、意図（未来 entry だからといって構造検証を省略しない）は読み取れるが、`PERIOD_OVERLAP_AT_DECISION` は候補の重複であって entry が壊れるわけではないので、言い回しとしては少し紛れる。

結合 v1 の 13 キー・閉じた 9 理由・`period_evidence_timed=false` 固定は、文書でも実装でも変わっていない。期間履歴の 20 理由が 9 理由へ混ざる書き方も無い（`INVALID_BUNDLE` は両 API に同名で存在するが、別 API の別語彙であることは明記されている）。

## 残る限界

期間履歴 API はまだ 1 行も実装されていない。本レビューは契約文書の読解と、既存 API（履歴 v1・結合 v1）の実測に基づくものである。提案した人工例・固定出力は私が契約どおりに計算した参照値であって、実装の保証ではない。写しの検証も「規則どおりに写した場合」と「規則を無視した場合」の両方を試したにすぎず、投入側が誤って写す全パターンを網羅したものではない。全体一括実測は私の側では再実行していない。Windows 実機・Codex ランタイム Python では実行していない。実原本・実公表時刻・実適用期間・当時の入手可能性の根拠、永続化・排他・電源断耐久・原本署名・全銘柄の網羅性・提供元の真正性は引き続き未保証である。自己申告時刻の整合が取れることを、実際に当時入手できた事実へ昇格させないこと。

終了時刻: 2026-09-13 09:30 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、第9回レビュー R9-01〜05（結合へ写す revision_id の ASCII 書式前提、payload 形と SELECTION_HASH_INVALID と code の理由対応、既知 series への再初版、digest の sha256 出所、結合 v1 が写しの出所を検証しない旨の明記）への対応と、提示した人工入力例・固定出力・CLI 案に基づく期間履歴 API の実装着手を行ってください。
```
