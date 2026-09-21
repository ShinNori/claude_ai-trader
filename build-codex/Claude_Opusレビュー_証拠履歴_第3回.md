# Claude Opus 独立レビュー 第3回: R2-01〜09 対応と結合 v1 契約の分類・優先の確定

2026-09-12。実施者 Claude Opus（Cowork、Linux コンテナ）。依頼は[キャッチボール文書](Claude_Opusキャッチボール.md)の B（更新 16:05 JST）。第1回・第2回は[Claude_Opusレビュー_証拠履歴.md](Claude_Opusレビュー_証拠履歴.md)、[同 第2回](Claude_Opusレビュー_証拠履歴_第2回.md)。

実装コード・既存試験の期待値・共通仕様・合成データは変更していない。追加したのは専用の新規試験 1 ファイルのみ。実 API・売買審査・LINE・証券接続・発注・見張り再開・Claude 公開はいずれも行っていない。一時ファイルはすべて Dropbox 外のレビュー用コンテナに置いた。

## 結論

**R2-01・R2-02・R2-04〜R2-09 は解決を確認した。実装バグは今回も 0 件。** とくに R2-02 は、Codex が追加した 5 経路に私が独自に足した 10 経路を加えた**計 18 経路すべてで例外の素通りが消えた**ことを実測で確認した。

**R2-03（出力スキーマと理由コード）は方向としては解決したが、分類と優先に残る曖昧さが 3 点ある。**うち 1 点は設計判断ではなく「既存 API の戻り値からは決められない」という実装上の制約で、結合 API を書く前に必ず解く必要がある（R3-01）。依頼が名指しした「期間証拠の構造破損と期間不適合の分類」「mode 差と構造破損の優先」は、いずれも具体例で再現できる曖昧さとして残っていた。

## 検証環境と実測（Codex 実測と区別）

Linux コンテナ、CPython 3.11.15、pytest。Windows 実機・Codex ランタイムでは実行していない。対象を読み取り専用で複製し、Dropbox へ書き戻していない。

私が再実行した関連範囲は **195 passed、1.05 秒、失敗 0 件**で、Codex 報告の「関連 195 passed」と件数が一致した（所要 2.01 秒との差は環境差）。新規 5 件（`test_strict_validity_r2_defense.py`）も一致した。

| ファイル | 件数 |
|---|---|
| test_evidence_history_fixture.py | 29 |
| test_evidence_history_fixture_cli.py | 5 |
| test_evidence_history_opus_review.py（第1回で私が追加） | 54 |
| test_history_cli_size_boundary.py | 2 |
| test_history_validity_binding_example.py | 4 |
| test_strict_input.py | 48 |
| test_strict_validity.py | 38 |
| test_strict_validity_cli.py | 5 |
| test_strict_validity_defensive_resolution.py | 5 |
| test_strict_validity_r2_defense.py（今回 Codex 新規） | 5 |
| 今回私が追加した `test_validity_reason_classification_opus.py` | 11 |
| 合計 | **206 passed、0.97 秒** |

**全体 2580 passed / 9 skipped は Codex の変更前の前回実測であり、今回どちらも再実行していない。**README 末尾の「今回再実行していない」という但し書きは正確である。

## 依頼の重点項目への回答

| 重点 | 判定 |
|---|---|
| 結合 v1 の依存 / receipt 意味論 | **無矛盾。指摘なし** |
| v2 への UTC 正規化延期 | **無矛盾。指摘なし**（F-03 行と R2 節の表現が一致した） |
| 出力 13 キー | **一致。指摘なし** |
| 閉じた理由 9 種の分類 | **R3-01。期間証拠の 2 分類が既存の理由コードから決定できない** |
| 理由の優先 | **R3-02。mode 差・書式・構造破損・封筒の順序が未定義** |
| 構造破損時ゼロ件と部品間不一致の診断件数の区別 | **R3-03 を除き明確。判定時点不一致だけ件数と digest が確定しない** |
| R2-02 の 5 経路＋既存 5 件で閉じるか | **閉じた。私の追加 10 経路も含め 18/18 が固定理由で閉鎖** |
| 入力キー欠落時の INVALID_BUNDLE 併記 | **区別できている。指摘なし** |
| 例の封筒と v1 設計の一致 | **一致。指摘なし** |

## R2-01〜09 の対応確認

**R2-01（版境界）は解決。** F-03 行が「履歴 v2 と結合 v2 を同時導入するときのみ」へ書き換わり、R2 節の「結合 v1 は履歴 v1 の canonical JSON 完全一致」と矛盾しなくなった。mode 差を `HISTORY_MODE_MISMATCH` で拒否する規定も入り、版の混在経路が閉じている。

**R2-02（防御範囲）は解決。** `strict_validity.py` の try が `data["input"]` から選択行の解決までを覆い、`as_of is None` が `INPUT_INVALID_AS_OF` で先に弾かれるようになった。`inspect_strict_input` を常時 VERIFIED へ差し替えた状態で 18 経路を試し、**未捕捉例外は 0 件**だった。

| 経路 | 実測 |
|---|---|
| 第2回で素通りした 5 経路（expected_codes 削除 / as_of 削除 / as_of 不正値 / input が配列 / input キー削除） | すべて DATA_INCOMPLETE。順に `INPUT_SOURCE_REFERENCE_INVALID`、同、`INPUT_INVALID_AS_OF`、同前者、`INPUT_SOURCE_REFERENCE_INVALID` ＋ `INVALID_BUNDLE` |
| 既存 2 経路（source_documents 空 / sources のコード削除） | 変化なく `INPUT_SOURCE_REFERENCE_INVALID` |
| 私の追加 10 経路（input が文字列 / expected_codes が文字列・非文字列要素 / sources が非 dict / sources[group] が非 dict / source_documents が非 list・要素が非 dict / as_of が数値 / 参照の document_id 欠落 / pointer が None / payload が深い入れ子） | すべて DATA_INCOMPLETE、固定理由 1〜2 件、入力不変 |

入力キー欠落時に `INVALID_BUNDLE` が併記されるのは、最上位キー集合の検査が防御より前に走るためで、依頼の指摘どおり区別できている。理由コードは昇順で返り、`verified_evidence_count` は常に 0 だった。

**R2-04〜R2-09 は結合案へ反映済みであることを確認した。** `corrected_after_as_of_count>0` が成功を妨げないこと、`future_revision_count` との違い、subject 単位の絞り込みと未来 entry の全保持、コード書式を適用する 6 箇所と UTF-8 バイト一致、専用例への封筒追加、1MiB が保証件数ではないこと、追加反証 3 件。いずれも私の第2回の提案と一致し、削られた条件はない。

専用例は独立に検算した。最上位は `mode` / `source_origin` / `input` / `history` / `validity_documents` の **5 キーちょうど**、`mode="history_validity_binding_fixture_v1"`、`source_origin="offline_fixture"`、`history.mode="evidence_history_fixture_v1"`、`input.mode="strict_input_v1"`。`as_of` と `decision_at` は同一文字列。lots・events とも、履歴 payload が strict_input の参照行と同一、履歴 `sha256` が行の canonical hash と一致、期間証拠の `record_sha256` が同じ行 hash、期間証拠 document の自己 hash も正しい。3 部品を個別に通すといずれも VERIFIED だった。**封筒と v1 設計は一致している。**

## 指摘事項

### R3-01（中・実装の制約）期間証拠の「構造破損」と「期間不適合」は、既存の理由コードからは分類できない

再現条件: `examples/strict_validity_valid.json` の期間証拠 1 件を種類別に壊し、`inspect_strict_validity` の `reason_codes` を見る。

期待（結合案の規定）: 構造 / 型 / 原本自己 hash の破損なら `INVALID_BUNDLE`、構造が正しい証拠の欠落・重複 subject・行 hash 不一致・期間外なら `VALIDITY_NOT_SATISFIED`。

実測: **2 つの既存理由コードが両方の分類にまたがる。**

| 壊し方 | 結合案が意図する分類 | 実測の reason_codes |
|---|---|---|
| `record_sha256` を整数にする | INVALID_BUNDLE | `['VALIDITY_SUBJECT_INVALID']` |
| payload に余分キーを足す | INVALID_BUNDLE | `['VALIDITY_SUBJECT_INVALID']` |
| `group` を `"prices"` にする（構造は正しい対象違い） | VALIDITY_NOT_SATISFIED | `['VALIDITY_SET_MISMATCH', 'VALIDITY_SUBJECT_INVALID']` |
| `valid_from` を整数にする | INVALID_BUNDLE | `['VALIDITY_PERIOD_INVALID']` |
| `valid_from` の timezone を落とす | INVALID_BUNDLE | `['VALIDITY_PERIOD_INVALID']` |
| `valid_from >= valid_until` にする（型は正しい） | VALIDITY_NOT_SATISFIED | `['VALIDITY_PERIOD_INVALID']` |

残りの損傷は片方にしか現れない。`INVALID_VALIDITY_DOCUMENTS`（documents が空・重複 ID・文書キー集合違反・sha256 形式違反）と `VALIDITY_HASH_MISMATCH`（document 自己 hash）は構造側のみ、`VALIDITY_SET_MISMATCH` / `VALIDITY_SUBJECT_DUPLICATE` / `VALIDITY_RECORD_MISMATCH` / `VALIDITY_NOT_YET_EFFECTIVE` / `VALIDITY_EXPIRED` は不適合側のみである。

影響: 結合 API が `inspect_strict_validity` の戻り値だけを見て分類しようとすると、**型破損を `VALIDITY_NOT_SATISFIED`（診断件数つき）として返すか、対象違いを `INVALID_BUNDLE`（全件数 0）として返すかのどちらかの誤りが必ず出る。**前者は「壊れた入力に対して部品間比較の件数を出す」ことになり、結合案が明示的に禁じている挙動である。

重要度: 中（結合 API 実装の前提）。実装バグではなく、契約と既存戻り値の粒度の不整合。

修正案: 次の (c) を推奨する。

- (c) **結合境界で期間証拠の形だけ先に検査する。** 結合はどのみち F-01 の書式検査を境界で行うので、そこに `validity_documents` の要素形（`id`/`sha256`/`payload` の 3 キー、payload の 5 キー、`record_sha256` が 64 桁小文字 hex、`valid_from`/`valid_until` が timezone 付き文字列、`sha256` が payload の canonical hash）を足す。ここで落ちたものを `INVALID_BUNDLE` とし、通ったものだけ `inspect_strict_validity` へ渡す。以後の失敗はすべて `VALIDITY_NOT_SATISFIED` に落ちる。v1 の内部は一切触らない。
- (b) 代替: 分類を「観測できるもの」で定義し直す。`INVALID_VALIDITY_DOCUMENTS` と `VALIDITY_HASH_MISMATCH` のみ `INVALID_BUNDLE`、他はすべて `VALIDITY_NOT_SATISFIED`。文書へ「よく形の整った文書の内部の型破損は不適合として扱う」と明記する。実装は軽いが、結合案の現在の文言は書き換えが要る。
- (a) 非推奨: `strict_validity` 側で理由コードを分割する。既存試験の期待値に触れるため v2 が必要になる。

分類の現状は今回追加した試験で機械可読に固定した（後述）。

### R3-02（中・設計課題）先行拒否の優先順位が未定義

再現条件: 次の 2 例を結合 v1 の契約に当てはめる。

例 1: `history.mode` を `evidence_history_fixture_v2` にし、同時に `validity_documents` を空配列にする。
例 2: 最上位に 6 つ目のキーを足し（封筒破損）、同時に `history.mode` を v2 にする。

期待: 返る `reason_codes` が一意に決まること。

実測（文書読解）: 決まらない。結合案には「構造破損は単独 INVALID_BUNDLE で全件数 0、digest=null」「履歴 mode 差は HISTORY_MODE_MISMATCH で拒否する」「形式/版の先行拒否では安全な集計を行わず全件数 0/digest=null」の 3 文があるが、**相互の順序が書かれていない**。例 1 は「`INVALID_BUNDLE` 単独」「`HISTORY_MODE_MISMATCH` 単独」「両方を昇順」の 3 通りに読め、3 番目は「構造破損は単独」と衝突する。例 2 も同様である。

あわせて、書式検査（`CODE_FORMAT_INVALID` / `IDENTIFIER_FORMAT_INVALID`）が「先行」と書かれている一方、コードや ID は部品の内部にあるため構造を読めないと取り出せない。書式と構造のどちらが先かも決まらない。

重要度: 中（実装が分岐する）。

修正案: 全順序を 1 段落で固定する。推奨は次のとおりで、**同じ段のものだけ併記し、段が違えば先の段で打ち切る**。

1. 最上位封筒（5 キー・`mode`・`source_origin`）の不一致 → `INVALID_BUNDLE` 単独、全件数 0、digest=null。
2. `history.mode` が依存 v1 と異なる → `HISTORY_MODE_MISMATCH` 単独、全件数 0、digest=null。
3. 各部品の構造・内部整合の不成立（3 API のいずれかが不合格、R3-01 の境界検査を含む） → `INVALID_BUNDLE` 単独、全件数 0、digest=null。
4. 書式違反 → `CODE_FORMAT_INVALID` / `IDENTIFIER_FORMAT_INVALID`（併記可）、全件数 0、digest=null。
5. 部品間の比較 → `DECISION_TIME_MISMATCH` / `HISTORY_SELECTION_MISSING` / `ROW_HASH_MISMATCH` / `VALIDITY_NOT_SATISFIED` / `EXTRA_SUBJECT_PRESENT`（併記可）、診断件数と digest を返す。

この順なら「全件数 0 を返すのは 1〜4、件数を返すのは 5 だけ」と 1 行で言い切れる。

### R3-03（低〜中・設計課題）判定時点不一致のときの件数と digest が確定しない

再現条件: `input.as_of` を `2026-09-12T06:50:00+09:00`、`history.decision_at` を `2026-09-12T06:51:00+09:00` にし、他はすべて正常な結合束を作る。

期待: 出力が一意に決まること。

実測（文書読解）: 決まらない。「同時点の検査が不成立なら matched=0 として時点の異なる成功を返さない」は `required` と `extra` を返す読み方になるが、「前提検査失敗で安全な選択を得られない場合も件数 0/digest=null」は判定時点不一致も前提検査とみなす読み方を許す。`selection_sha256` の扱いも書かれていない。

重要度: 低〜中。

修正案: R3-02 の段 5 に置き、`required` と `extra` と `corrected_after_as_of_count` は返し、`matched=0`、**`selection_sha256=null`** と決めることを推奨する。digest を null にする理由は、履歴の digest が「要求された時点とは違う時点」の選択に対するものだからで、これを返すと呼び出し側が別時点の digest を保存・比較しかねない。

### R3-04（低〜中・文書と実装の不一致）`INPUT_INVALID_AS_OF` がどの契約文書にも無い

再現条件: `build-codex/*.md` を `INPUT_INVALID_AS_OF` で検索する。

期待: `strict_validity` が返しうる理由コードは `STRICT_VALIDITY.md` に列挙されていること。F-09 節は `INPUT_SOURCE_REFERENCE_INVALID` を明記している。

実測: 一致 0 件。コードは返すが、どの文書にも記載がない。`STRICT_VALIDITY.md` は今回更新されていない（更新時刻は第2回時点のまま）。

重要度: 低〜中（契約文書と実装の乖離）。

修正案: `STRICT_VALIDITY.md` の F-09 節へ 1 文足す。「入力の `as_of` を timezone 付き時刻として解釈できない場合は `INPUT_INVALID_AS_OF` で拒否し、期待数は維持、検証済み件数は 0 とする」。あわせて結合案の対応表へ「`INPUT_*` 系はすべて `INVALID_BUNDLE` へ写像する」と明記すると、9 種の閉じた集合との対応が完結する。

### R3-05（低・堅牢性）失敗時の `expected_evidence_count` は壊れた入力から算出されうる

再現条件: `inspect_strict_input` を常時 VERIFIED へ差し替え、`input.expected_codes` を配列ではなく文字列 `"0001"` にする。

期待: 安全に集合を確定できないので、期待数は 0 か、少なくとも意味のある値であること。

実測: `expected_evidence_count=4` が返る。文字列を反復して 1 文字ずつを銘柄コードとして扱い、`{"0","1"} × {lots, events}` の 4 件になったためである。`status` は `DATA_INCOMPLETE`、`verified_evidence_count=0` なので**誤承認は起きない**。

重要度: 低（失敗結果の中の診断値であり、判定には使われない）。

修正案: 結合 API は `required_subject_count` を `inspect_strict_validity` の `expected_evidence_count` から**再利用しない**と明記する。required は、検証を通った入力の `expected_codes` からのみ算出する。結合案の「構造破損等で安全に集合を確定できなければ全件数 0」と整合させるには、この一文が要る。

### R3-06（低・文書）「全件数 0」の対象が明示されていない

結合案の「全件数 0」が、`required_subject_count` / `matched_subject_count` / `extra_subject_count` / `corrected_after_as_of_count` の **4 つすべて**を指すのか、最初の 3 つだけなのかが書かれていない。`period_evidence_timed` / `ready_for_live` / `current_signal` が false 固定、`read_only` が true 固定であることは明記されているので、件数側も同じ水準で書き切ることを勧める。推奨は「4 件数すべて 0、`selection_sha256=null`」。

### R3-07（低・運用）内側 3 部品の診断が `INVALID_BUNDLE` へ畳み込まれる

「部品の内部整合不成立は `INVALID_BUNDLE` へ分類し、元の理由や入力本文をそのまま外へ返さない」という方針自体は妥当である。ただし結果として、たとえば価格の `price_at` が `as_of` より後という**ごく普通の入力不備**も、結合 API からは `INVALID_BUNDLE`・全件数 0 としか見えない。

重要度: 低（安全側の設計で、誤承認は起きない）。

修正案: 結合案へ運用の一文を足す。「`INVALID_BUNDLE` が返った場合の原因特定は、`strict_input` / `evidence_history_fixture` / `strict_validity` の 3 CLI を個別に実行して行う。結合 API は原因を要約しない」。

## 追加した試験

`build-codex/tests/test_validity_reason_classification_opus.py`（11 件、0.04 秒、全通過）を新規追加した。既存試験の期待値と実装コードは変更していない。

損傷種別ごとに `strict_validity` が返す理由コードを固定し、R3-01 の「2 つのコードが両方の分類にまたがる」事実を機械可読な形で残す。結合の対応表を書くときの入力表として使える。構造側にしか現れないコードと不適合側にしか現れないコードも、確定部分として別に固定した。現在の挙動の追認であって是認ではない旨を docstring に明記してある。R3-01 の修正案 (a) を採る場合はこの試験が落ちるので、変更が意図的だと分かる。

## 指摘なしと確認した範囲

結合 v1 の依存関係（`strict_input_v1` / `evidence_history_fixture_v1` / `strict_validity_fixture_v1`）と receipt 同一性（canonical JSON 完全一致）、UTC 正規化を履歴 v2 と結合 v2 の同時導入まで延期する記述は、F-03 行と R2 節の双方で一致しており矛盾がない。出力 13 キーは数も名前も過不足がない。理由 9 種は、私が列挙できた失敗（封筒破損・版差・書式違反・3 部品の内部不成立・判定時点差・過去選択欠落・行 hash 差・期間不適合・余分 subject）のすべてに割り当てられ、**割り当て先のない失敗は見つからなかった**。曖昧なのは割り当ての境界（R3-01〜R3-03）であって、集合の網羅性ではない。

R2-02 の防御は 18 経路で閉じ、捕捉する例外型に過剰もない。専用例の封筒・時点・値・hash はすべて独立検算で一致した。第1回で私が追加した 54 件は今回も全通過しており、今回の変更が履歴モデルの性質を壊していない。

## 残る限界

全体一括実測（2580 件）は Codex・私とも今回再実行していない。Windows 実機・Codex ランタイム Python では実行していない。結合 API 本体・内部共用選択モデル・F-01 の書式検査・F-03 の時刻正規化はいずれも未実装で、本レビューはコードではなく契約文書と既存部品の戻り値に対する点検である。永続化・排他・電源断耐久・原本署名・実公表時刻・実適用期間・全銘柄の網羅性・提供元の真正性は引き続き未保証。人工入力の内部整合が正しいことを、実売買適格性へ昇格させないこと。
