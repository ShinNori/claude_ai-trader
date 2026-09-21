# Claude Opus 独立レビュー 第7回: 結合 v1 API / CLI と内部共用選択モデルの実装

2026-09-13。実施者 Claude Opus（Cowork、Linux コンテナ）。依頼は[キャッチボール文書](Claude_Opusキャッチボール.md)の B（更新 2026-09-13 07:55 JST）。第1〜6回は[原レビュー](Claude_Opusレビュー_証拠履歴.md)、[第2回](Claude_Opusレビュー_証拠履歴_第2回.md)、[第3回](Claude_Opusレビュー_証拠履歴_第3回.md)、[第4回](Claude_Opusレビュー_証拠履歴_第4回.md)、[第5回](Claude_Opusレビュー_証拠履歴_第5回.md)、[第6回](Claude_Opusレビュー_証拠履歴_第6回.md)。

今回から実装レビューである。実装コード・既存試験の期待値・共通仕様・合成データ・専用例は変更していない。追加したのは専用の新規試験 1 ファイルのみ。実 API・売買審査・LINE・証券接続・発注・見張り再開・Claude 公開はいずれも行っていない。一時ファイルはすべて Dropbox 外のレビュー用コンテナに置いた。

## 結論

**実装バグは 0 件。**契約（段1〜段5、13 キー、閉じた 9 理由、4 件数、固定フラグ、書式の 6 箇所と全 ID 参照、CLI の 1MiB 規約）と実装の食い違いは、独立に組んだ参照実装との差分照合・無作為破壊・例外注入のいずれでも見つからなかった。第6回で「契約の側から着手可能」と判定した通りに書かれている。

**共用モデル化による履歴 v1 の退行もない。**第6回時点の実装と今回の実装へ同じ 6,000 通りの履歴束を通し、`inspect_evidence_history` の戻り値が **1 件の差異もなく一致**した。`SELECTION_HASH_INVALID` の固定応答も保たれている。Codex が自分で見つけて直したという退行は、私の側からも再発していないことを確認できた。

**残る指摘は 3 件で、すべて低。**実装を直すものは 1 件もない。R7-01 は契約文書の言い回し、R7-02 は次版向けの堅牢性の提案、R7-03 は Codex 側試験の穴（専用例が 1 銘柄しかないため、複数必須 subject の件数分配が製品試験で固定されていない）である。私の追加試験でその穴は埋めた。

## 検証環境と実測（Codex 実測と区別）

Linux コンテナ、CPython 3.11.15、pytest 9.1.1。Windows 実機・Codex ランタイムでは実行していない。対象を読み取り専用で複製し、Dropbox へ書き戻していない（本レビュー文書と追加試験の 2 ファイルを除く）。

私が実行した関連範囲は **19 ファイル 362 passed、1.44 秒、失敗 0 件**。内訳は既存 15 ファイル 299 件と新規 4 ファイル 63 件で、Codex 報告の「既存関連 15 ファイル 299 passed / 1.17 秒」「新規専用 4 ファイル 62 passed / 1 skipped / 0.64 秒」と一致する（Codex 側の 1 skip は実 symlink 作成の Windows 権限不足によるもので、Linux では実行され通過した。したがって私の側は 63 passed）。今回追加した 96 件を含めると **458 passed、2.12 秒**である。

**Codex 全体実測（common/tests・build-codex/tests・ops/tests で 2728 passed / 10 skipped / 562 warnings、305.83 秒、境界 19 件を含まない）は Codex の実測であり、私は再実行していない。**私の 362 件・458 件・下記の無作為検査はすべて私の実測で、Codex が再現したとは書かれていない。この区別は README 末尾とキャッチボール文書のとおり正確である。以前の 2580 件は過去実測である。

| 検査 | 規模 | 結果 |
|---|---|---|
| 段5 の理由・4 件数を独立参照と差分照合 | 2,428 束（段5 到達分） | 不一致 **0**。15 通りの理由組合せを網羅 |
| 束全体の無作為破壊で契約不変条件 | 4,000 束 | 違反 **0**（13 キー・閉じた 9 理由・段別件数・入力不変・決定性） |
| 期間証拠だけを無作為破壊し段3/段5 の分類 | 4,000 束 | 独立参照との分類不一致 **0**（構造 2,881／非構造 1,119） |
| 共用モデル化前後の履歴 v1 戻り値 | 6,000 束 | 差異 **0**（成功 1,664、理由 10 種以上） |
| 内部 9 箇所への例外注入 | 63 経路 | 文書化された 6 種 54 経路はすべて `INVALID_BUNDLE` 単独へ fail-closed |
| Python レベルの異常入力 | 14 種 | 未捕捉例外 **0**（NaN・inf・10^400・深い入れ子・tuple・datetime ほか） |
| CLI の終了コードと出力 | 10 通り | 契約どおり |

## 重点項目への回答

| 重点 | 判定 |
|---|---|
| 段1/2 の部品非呼出 | **指摘なし。呼出回数 0 を実測** |
| 段2 のキー過不足と mode 差 | **指摘なし。余分キー・`decision_at` 欠落と併存しても段2 単独** |
| 段3／段4 の優先 | **指摘なし。構造破損＋書式違反は `INVALID_BUNDLE` 単独、書式違反＋時点不一致は書式単独** |
| 段5 不一致時の as_of 1 回評価と strict_validity 非呼出 | **指摘なし。呼出 0 回、3 件数のみ返す** |
| 同時点の UTC/JST | **指摘なし。`+00:00` 表記でも成功** |
| 未来だけの余分 subject | **指摘なし。`EXTRA_SUBJECT_PRESENT`、extra=1** |
| 選択欠落と別 subject の corrected 併存 | **指摘なし。`HISTORY_SELECTION_MISSING` かつ corrected=1** |
| hash／期間不適合の理由併記と matched | **指摘なし。subject 単位で 1 ずつ下がることを 4 subject 束で確認（R7-03）** |
| 遅延 INPUT_* 時の全診断破棄 | **指摘なし。4 件数 0・digest null** |
| 書式の 6 箇所と全 ID 参照 | **指摘なし。単独違反でも段4 で捕まることを箇所ごとに実測** |
| 13 キー／閉じた 9 理由／固定 false フラグ | **指摘なし。4,000 束で違反 0** |
| CLI（1MiB ちょうど通過・+1 で API 非呼出） | **指摘なし** |

## 実装の確認内容

### 段の順序と呼出し

段1 は封筒（5 キー・mode・source_origin）だけを見て `INVALID_BUNDLE` 単独を返す。段2 は `history` が辞書で `mode` キーがあり値が違うときだけ `HISTORY_MODE_MISMATCH` 単独を返し、依存 API を呼ばない。両段で `inspect_strict_input` / `_validate_history` / `inspect_strict_validity` を差し替えた計測器を入れ、**呼出回数 0** を確認した。R5-01 の決着どおり、`mode` が v2 で余分キーがあっても `decision_at` を落としても段2 で止まる。

段3 は `inspect_strict_input` → `_validate_history` → 期間証拠の事前形検査の順で、どれが落ちても `INVALID_BUNDLE` 単独・4 件数 0・digest null。`history.source_origin` 差（依存側は `INVALID_ORIGIN`）、`input.mode` / `input.source_origin` 差も段3 に畳まれ、10 種目の理由は増えていない。空の `history.entries` は段3、空の `validity_documents` は段5 の `VALIDITY_NOT_SATISFIED`（R6-04 の決着どおり）。

段4 は段5 より先に打ち切り、段3 は段4 より先に打ち切る。段5 は時点一致を先に判定し、不一致なら `DECISION_TIME_MISMATCH` 単独・matched=0・digest null で、`strict_validity` を呼ばない。空の期間証拠を同時に持たせても呼出は 0 回だった。

### 書式検査（F-01）

コード書式は 6 箇所へ届く。入力側（`expected_codes`・`sources[group]` のキー・選択行の code）は相互に一致が要るため一括でしか崩せないが、小文字 `abcd`・6 文字・3 文字・全角のいずれでも `CODE_FORMAT_INVALID` になり、4 文字・5 文字・大文字は通った。履歴側と期間証拠側は単独で崩せるので個別に確かめた。**履歴に `abcd` の余分 subject を足すと、段5 の `EXTRA_SUBJECT_PRESENT` ではなく段4 の `CODE_FORMAT_INVALID` になる**（正しい順序）。期間証拠に 2 文字コードの文書を足した場合も同じである。

ID 書式は receipt_id・revision_id・supersedes の参照 revision_id・原本 document ID・sources の document_id・期間証拠 ID のすべてへ届いた。128 文字は通り 129 文字で落ちる、記号始まり・内部空白・非 ASCII は落ちる、という境界も確認した。JSON Pointer に ID 規則を適用していないことも、pointer を含む束が通ることから確かめられる。

### 段5 の診断

段5 の理由と 4 件数を、契約だけを読んで別に組んだ参照実装と突き合わせた。時刻・履歴・期間証拠へ無作為な意味的変更を加えた束のうち段5 へ到達した **2,428 通りで不一致 0**。出現した理由の組合せは 15 通りで、`VALIDITY_NOT_SATISFIED` 単独から `EXTRA_SUBJECT_PRESENT` ＋ `ROW_HASH_MISMATCH` ＋ `VALIDITY_NOT_SATISFIED` の 3 併記まで含む。matched は subject 単位で正しく増減し、`selection_sha256` は成功時に公開履歴 API の digest と一致した。全版が未来の履歴では matched=0・`HISTORY_SELECTION_MISSING` でありながら digest は空配列の hash として返る（空配列 digest を充足へ焼き込んでいない）。

### 共用モデル

`_validate_history` は選択を行わず構造・内部整合だけを見て `_ValidatedHistory` を返し、`evaluate(at)` が選択・digest・全 subject・訂正 subject を算出する。公開履歴 API は `decision_at`、結合 API は段4 通過後の `as_of` で 1 回だけ呼ぶ。`evaluate` は naive datetime を拒否する。**第6回時点の `evidence_history_fixture.py` と今回の版へ同じ 6,000 束を通し、戻り値の差異は 0 だった。**receipt 完全一致・NO_OP・再取得・線形訂正・digest の公開形はいずれも保たれている。内部モデルの選択行や payload は公開 JSON へ出ていない（出力は 13 キーのみ）。

### CLI

正常束で終了 0・JSON、時点不一致で終了 2・JSON（`DATA_INCOMPLETE`）、1,048,576 バイトちょうどで終了 0、+1 バイトで終了 2・stdout 空・固定文「履歴と適用期間を検査できません。入力形式とサイズを確認してください。」。存在しないファイル・引数なし・未知引数・壊れた JSON・重複キー・非有限値もすべて同じ固定文と終了 2 で、stdout は空だった。既存の安全読取器を流用しているため、link/reparse と読取中の変化の拒否も内側 3CLI と同じである（R6-01 の決着どおり）。

## 指摘事項

### R7-01（低・文書）「成功条件」が十分条件に読める

再現条件: 専用例へ、`expected_codes` にない銘柄（`0002`）の期間証拠を 1 件足す。他はすべて正常。

期待: 成功可否が文書から一意に読めること。

実測: 実装は `VALIDITY_NOT_SATISFIED` で `DATA_INCOMPLETE` を返す（正しい）。ところがこの束は **required=2、matched=2、extra=0、時点一致**をすべて満たしており、F-05/F-07 節の「成功条件は required>0、matched=required、extra=0」だけを読むと成功に見える。実装入口の節には「余分な期間 subject によって全体不適合でも個々の正常 subject の matched 診断は維持する」と書かれているので読み解けはするが、成功条件そのものの記述とは離れている。

重要度: 低（実装は安全側で正しい。文書だけの問題）。

推奨: 成功条件へ「かつ reason_codes が空であること（status が VERIFIED_OFFLINE_BINDING であることと同値）」を 1 句足す。matched=required でも他の理由が立てば不成功、という関係を成功条件の場所で読めるようにする。

### R7-02（低・堅牢性の提案、次版）例外境界に AttributeError が含まれない

再現条件: `_hash` / `_validity_shape` / `_format_reasons` / `_resolve` / `_aware` / `inspect_strict_input` / `inspect_strict_validity` / `_validate_history` / `_canonical` のいずれかが `AttributeError` を上げるよう注入する。

期待: 公開 API が固定応答へ倒れること。

実測: 文書化された 6 種（KeyError・IndexError・TypeError・ValueError・OverflowError・RecursionError）は 9 箇所すべてで `INVALID_BUNDLE` 単独・digest null へ倒れた（54 経路）。`AttributeError` だけは 9 箇所すべてで呼出元へ抜ける。ただし**通常の入力から `AttributeError` へ至る経路は見つからなかった**。到達するのは内部が退行した場合だけで、これは `strict_validity` の F-09 防御や履歴 v1 と同じ境界である。

重要度: 低（現時点で到達不能。将来の退行時に例外が外へ出るだけ）。

推奨: 増やすなら結合・履歴・入力・期間の 4 モジュールで同時に `AttributeError` を足す。片方だけ広げると境界が揃わなくなるので、次版でまとめて扱うのが良い。今回は据え置きでも実害はない。

### R7-03（低・試験）専用例が 1 銘柄しかなく、複数必須 subject の件数分配が製品試験で固定されていない

再現条件: `expected_codes` を 2 銘柄にした束（required=4）で、片方の期間だけ期限切れにする／片方の履歴選択だけ落とす／片方だけ行 hash を変える。

期待: matched が subject 単位で 1 ずつ下がること。

実測: 実装は正しく `required=4`・`matched=3` を返し、理由も `VALIDITY_NOT_SATISFIED` / `HISTORY_SELECTION_MISSING` / `ROW_HASH_MISMATCH` と分かれた。5 文字コードを混ぜた 2 銘柄束も成功する。**バグはない。**ただし `examples/history_validity_binding_valid.json` は 1 銘柄（required=2）で、Codex の新規 4 ファイルもこの例から派生しているため、複数 subject に分配される件数は製品試験で固定されていない。件数の按分は将来の変更で崩れやすい箇所である。

重要度: 低（現状は正しい。回帰の網が粗いだけ）。

推奨: 2 銘柄の束を 1 つ製品試験へ足す（専用例そのものを増やす必要はない）。今回私が追加した 5 件がそのまま雛形になる。

## 追加した試験

`build-codex/tests/test_binding_product_review_opus.py`（96 件、0.57 秒、全通過）を新規追加した。既存試験の期待値と実装コードは変更していない。

内容は、契約だけから組んだ段5 参照実装との差分照合（無作為 200 束、入力不変と決定性も同時に確認）、無作為構造破壊 300 束での契約不変条件、書式検査が 6 箇所と全 ID 参照へ届くことと境界（4/5 文字、128/129 文字）、段3→段4→段5 の打切り順、段1/2 の部品非呼出と時点不一致での `strict_validity` 非呼出、遅延 `INPUT_*` での全件数破棄、文書化された 6 種の例外注入 54 経路の fail-closed、成功時 digest と公開履歴 digest の一致、全版未来の束、matched=required でも不成功になる束、NaN・inf・10^400 の fail-closed、そして 2 銘柄束での件数分配である。

試験内の参照実装は「契約どおりに私が書いた版」であって、Codex 実装の写しではない。両者が 2,428 束で一致したことが今回の主な根拠である。

## 指摘なしと確認した範囲

依頼が名指しした 12 論点すべてで、実装は契約どおりだった。閉じた 9 理由の外は 1 度も現れず、段1〜4 では常に 4 件数 0・digest null、段5 では常に digest が非 null で matched ≤ required、成功時だけ matched=required かつ extra=0 になる。固定フラグ（`period_evidence_timed` / `ready_for_live` / `current_signal`=false、`read_only`=true）は 4,000 束すべてで保たれた。出力に銘柄・ID・値・時刻の本文は現れない。入力は 4,000 束すべてで書き換えられず、同じ束を 2 回渡せば同じ結果が返る。

第1回で私が追加した 54 件、第3回の 11 件、第4回の 30 件、第5回の 30 件、第6回の 29 件は今回も全通過している。

## 残る限界

全体一括実測（2728 件）は私の側では再実行していない。Windows 実機・Codex ランタイム Python では実行していない。今回の差分照合は「契約どおりに私が書いた参照」との一致であり、契約そのものが実データに対して正しいことの証明ではない。無作為検査は私が選んだ変異の範囲での網羅であって、全入力空間の網羅ではない。結合 v1 はオフラインの人工入力専用で、DB 保存・実原本の取込・日次シグナル接続・実二者審査／通知・履歴 v2 の UTC 正規化はいずれも未実装である。実原本・実公表時刻・実適用期間・入手可能性の根拠、永続化・排他・電源断耐久・原本署名・全銘柄の網羅性・提供元の真正性は引き続き未保証である。人工束が `VERIFIED_OFFLINE_BINDING` になることを、実売買適格性へ昇格させないこと。

終了時刻: 2026-09-13 08:15 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、第7回レビュー R7-01〜03（成功条件へ reason_codes 空を明記、例外境界へ AttributeError を足すかの判断、複数銘柄束の製品試験追加）への対応と、次工程の着手を行ってください。
```
