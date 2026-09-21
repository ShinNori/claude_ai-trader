# Claude Opus 独立レビュー 第10回: R9-01〜05 の反映と、期間証拠履歴 API / CLI の実装

2026-09-14。実施者 Claude Opus（Cowork、Linux コンテナ）。依頼は[キャッチボール文書](Claude_Opusキャッチボール.md)の B（更新 2026-09-14 05:37 JST）。第1〜9回は[原レビュー](Claude_Opusレビュー_証拠履歴.md)、[第2回](Claude_Opusレビュー_証拠履歴_第2回.md)〜[第9回](Claude_Opusレビュー_証拠履歴_第9回.md)。

観点ごとに 8 体のサブエージェントを並列で走らせ、親がすべて再現・検算して統合した。裏の取れなかった主張は本文へ入れていない。

実装コード・既存試験の期待値・共通仕様・合成データ・既存例は変更していない。追加したのは専用の新規試験 1 ファイルのみ。実 API・売買審査・LINE・証券接続・発注・見張り再開・Claude 公開はいずれも行っていない。一時ファイルはすべて Dropbox 外のレビュー用コンテナに置いた。

## 結論

**新規の期間証拠履歴 API / CLI に契約違反は 0 件。**20 理由はすべて到達可能で（`SELECTION_HASH_INVALID` だけは契約どおりの防御ガードで通常入力からは出ない）、判定順序・選択規則・半開区間・digest・14 キー出力・CLI の読取規約はいずれも契約どおりだった。第9回で私が提示した人工入力例はそのまま採用され、**固定出力 14 キーと digest `ba6486dd…186ca` が実測で一致**した。**既存 4 API は 1 バイトも変わっておらず**、新 API はそれらを一切呼んでいない（`strict_input` から `_aware` と `_canonical` だけを取り、行 payload 検証器 `_lot`/`_event` は流用していない）。

**R9-01〜05 はすべて反映された。**R9-03 について Codex は「subject が同じでも別でも `INVALID_SUPERSEDES`」という私の一般化を採らず、既定の優先順（`REVISION_CONFLICT` → `SERIES_REFERENCE_INVALID` → `INVALID_SUPERSEDES`）に合わせて整理した。**この判断は正しい。**実装の分岐順・契約の判定順序・実測の 6 ケースすべてが Codex の説明と一致した。私の推奨は「subject が同じ場合」に限れば妥当で、一般化した部分が先行判定と衝突していた。

**残る指摘は 3 件で、すべて低。**実装を直すのは R10-01 の 1 行だけで、これも契約違反ではなく兄弟モジュールとの揃え直しである。

依頼の次工程（結合 v2 の最小契約案）は本文の後半に置いた。**結合 v2 の実装・schema 確定・DB 保存・実接続を意味しない。**

## 検証環境と実測（Codex 実測と区別）

Linux コンテナ、CPython 3.11.15、pytest 9.1.1。Windows 実機・Codex ランタイムでは実行していない。対象を読み取り専用で複製し、Dropbox へ書き戻していない（本レビュー文書と追加試験の 2 ファイルを除く）。

**「既存資産は変更なし」を全数照合で裏取りした。**第9回時点の `aitrader/*.py` 18 ファイルと `strategies/` 3 ファイル、`tests/` の既存 23 ファイル、`examples/` の既存 4 ファイルは、いずれも sha256 が完全一致。増えたのは `period_evidence_history_fixture.py` / `_cli.py`、試験 2 ファイル、例 1 ファイルだけである。

私が実行した関連範囲は **25 ファイル 561 passed、2.22 秒、失敗 0 件**。新規分は API 試験 55 件・CLI 試験 18 件で、Codex 報告「新規 API 55 成功、CLI 17 成功 / 1 skip」と合計 73 で一致する（差の 1 件は実 symlink 作成試験。当方は root 権限のため skip されず通過）。今回追加した 30 件を含めると **591 passed、2.52 秒**である。

**Codex の全体実測（2945 passed / 11 skipped / 562 warnings / 481.24 秒）は Codex の実測で、私は再実行していない。**当方の作業コピーには `common/tests` と `ops/tests` が無く、aitrader 関連の 561 件しか回せないためである。README 末尾がこの数値を「Codex 配下実測」と明示し、私の 488 件（第9回）と区別している点も確認した。

| 検査 | 規模 | 結果 |
|---|---|---|
| 20 理由の到達性 | 20 種 | 全種到達。`SELECTION_HASH_INVALID` のみ注入でのみ再現 |
| 2 欠陥同時の優先順 | 20 組 | 契約の判定順序と全一致 |
| 不成功時の不変条件（単独理由・6 件数 0・digest null） | 上記すべて＋フュзz | 違反 0 |
| 無作為破壊フュзz（構造） | 21,000 束 | 契約違反 0、未捕捉例外 0、入力改変 0、非決定 0 |
| 無作為フュзz（意味的） | 9,000 束 | 違反 0。成功 374 束。20 種中 18 種が自然出現 |
| Python レベル異常入力 | 19 種 × 11 箇所 | 未捕捉例外 0（素の JSON 型の範囲） |
| CLI の挙動 | 11 ケース | 終了コード・stdout・stderr・1MiB 境界すべて契約どおり |

## 重点項目への回答

| 重点 | 判定 |
|---|---|
| 20 理由と先行判定 | **指摘なし。20 種すべて到達可能、優先順 20 組が契約と一致** |
| 全未来 entry の検証 | **指摘なし。未来 entry の構造破損 3 種すべてで全体拒否** |
| NO_OP と時刻逆行 | **指摘なし。NO_OP が先で、逆行検査に到達しない** |
| 再取得が初出・head を変えない | **指摘なし。revision_count・future・選択・digest・head すべて不変** |
| 全束 revision 識別 | **指摘なし。同一内容でも別 series なら `REVISION_CONFLICT`** |
| series の subject 固定 | **指摘なし。R9-03 の整理も妥当（下記）** |
| 半開期間 | **指摘なし。4 境界を実測** |
| 選択後の 0/1/2 候補 | **指摘なし。0 は空配列 digest で成功、2 は全体拒否** |
| 期限外訂正から旧版へ戻らない | **指摘なし。selected は数えるが候補にしない** |
| digest が期間 payload 自己 hash | **指摘なし。行 hash 版とは別値になることも確認** |
| 全不成功時の 6 件数 0・digest null | **指摘なし。3 万束で違反 0** |
| CLI の安全読取流用 / 1MiB / 固定 stderr | **指摘なし。`_mapping` を同一オブジェクトとして流用** |
| R9-03 の整理の整合性 | **妥当。実装・契約・実測の三者が一致** |

## 実装の確認内容

### 判定順序と 20 理由

契約の「最小の判定順序」（封筒 → mode → origin → decision_at → entries → entry ごとに キー/canonical 化 → 識別子 → receipt 再投入/競合 → subject → 時刻と逆行 → payload 形と subject 一致 → 期間 → 自己 hash → supersedes 形 → revision 再取得/競合 → series の subject 固定 → 訂正参照）を実装の分岐順と 1 対 1 で照合し、並びが完全に一致することを確認した。そのうえで 2 つの欠陥を同時に持つ束を 20 組作り、先に来る段の理由だけが返ることを実測した。

分類の細部も契約どおりである。`entry.sha256` の書式破損は `INVALID_HASH`、`payload.record_sha256` の書式破損は `INVALID_PAYLOAD`、`supersedes.sha256` の書式破損は `INVALID_SUPERSEDES`、payload の自己 hash 差は `PAYLOAD_HASH_MISMATCH`、期間時刻の timezone 欠損・解釈不能・`valid_from >= valid_until` は `PERIOD_INTERVAL_INVALID`、payload が 5 キーでない場合と `group`/`code` が subject と食い違う場合は `INVALID_PAYLOAD`。第9回 R9-02 で「読めない」と指摘した 3 箇所は、文書にも実装にも反映されている。

`SELECTION_HASH_INVALID` は、射影に使う値がすべて検証済みの文字列なので通常入力からは出ない。`_digest` がリスト入力のときだけ失敗するよう注入して初めて単独で再現できた。履歴 v1 と同じ fail-closed のガードで、設計どおりである。

### 選択・候補・digest

人工例の 14 キーは `receipt_count=3` / `revision_count=3` / `noop_receipt_count=0` / `series_count=2` / `selected_series_count=2` / `future_revision_count=1` / `selection_sha256=ba6486dd33d76cf9594f6e43795f45c70349bee87e40fa6f13e0abcb01d186ca` で、第9回に私が計算した提案値と完全に一致した。

半開区間は 4 境界すべて契約どおり（`valid_from == decision_at` は候補、`valid_until == decision_at` は候補にならない、開始が 1 秒後・終了が 1 秒前はいずれも候補にならない）。有効候補 0 件のときの digest は空配列 hash `4f53cda1…202b945` で、自分で計算した値と一致した。同一 subject の候補が 2 件になる束は `PERIOD_OVERLAP_AT_DECISION` 単独・6 件数 0・digest null になる。

**期限外の訂正から旧版へ戻らない**ことも実測した。判定前に記録された訂正版が区間外の系列では、`selected_series_count` はその系列を数えるが有効候補にはならず、digest はもう一方の subject 1 件だけの射影になる。初版へフォールバックする挙動は無い。

digest の射影に使う `sha256` が **期間 payload の自己 hash（`entry.sha256`）** であることも、射影を自分で組み直して確かめた。`payload.record_sha256`（行の hash）で組むと別の値になる。第9回 R9-04 の指摘どおりに定義箇所へ明記されている。

### 系列・識別子と R9-03

6 ケースを実測し、Codex の説明と完全に一致した。

| 束 | 実測 | 決まる場所 |
|---|---|---|
| 既知 series・同 subject・新 revision・`supersedes=null` | `INVALID_SUPERSEDES` | head があるのに null なので訂正参照の段 |
| 既知 series・**別 subject**・新 revision・`supersedes=null` | `SERIES_REFERENCE_INVALID` | series の subject 固定が訂正参照より先 |
| 既知 series・別 subject・正しい末尾参照 | `SERIES_REFERENCE_INVALID` | 同上 |
| `supersedes` が別 series の既知 revision | `SERIES_REFERENCE_INVALID` | 参照先の series 不一致 |
| `supersedes` が未知 revision | `INVALID_SUPERSEDES` | 参照先が未知なので訂正参照の段 |
| 同じ `revision_id` を内容違いで再利用 | `REVISION_CONFLICT` | revision 再取得/競合が series 固定より先 |

順序はコードの分岐順から必然で、たまたまの結果ではない。私が第9回で「subject が同じでも別でも `INVALID_SUPERSEDES`」と一般化した部分は、確かに既定の優先順と衝突する。**Codex の絞り込みが正しい。**

抜け穴も探したが見つからなかった。同一 `revision_id` を**同一内容**で別 series に出しても、識別子タプルに `series_id` が入っているので再取得と誤認せず `REVISION_CONFLICT` になる。同 series の末尾でない旧版を参照する、自己参照、前方参照はいずれも `INVALID_SUPERSEDES`。行履歴 API と同じ文字列 ID を使っても、両者はモジュール状態を持たないので干渉しない。

## 指摘事項

### R10-01（低・堅牢性）`entry["sha256"]` だけブラケットで読み直しており、兄弟と揃っていない

再現条件: `.get()` と `__getitem__` が食い違う dict サブクラス（`sha256` キーの `__getitem__` だけ例外を投げる）を entry として直接渡す。

期待: 契約が「JSON 由来の素の値だけを信頼する」と決めている以上、これは**契約範囲外の入力**であり、この一件をもって契約違反とは言わない。

実測: 期間履歴 API は `KeyError` を呼出元へ漏らす。一方、**同じ攻撃で履歴 v1 は落ちない**。履歴 v1 は `supplied_hash = entry.get("sha256")` と変数に取ってから比較しているのに対し、期間履歴 API は `_hash_format(entry.get("sha256"))` で検査したあと `entry["sha256"] != payload_hash` と読み直しているためである。`subject_value["group"]` や `supersedes["revision_id"]` のブラケット読みは履歴 v1 にも同型で存在するので踏襲だが、この 1 箇所だけは新しく持ち込まれた差である。

重要度: 低（通常の JSON 入力からは到達しない。3 万束のフュзzでも未捕捉例外は 0 件だった）。

推奨: `.get()` の結果を変数に取り、`if supplied_hash != payload_hash` と比較する形へ 1 行直して兄弟と揃える。併せて、このファイルの `_HEX` だけ `^…$` のアンカーが無い（`fullmatch` で使っているので実害はない）点も、揃えるなら同時に。例外境界を 5 モジュールで見直す話（第7回 R7-02、第8回 R8-05）は据え置きのままでよい。

### R10-02（低・文書）digest の同名フィールドが 2 つの API にあることの注記が未反映

第9回 R9-04 では「射影の `sha256` が `entry.sha256` であることの明記」と「結合 v1 の `selection_sha256`（行選択の digest）とは別物であることの一文」を推奨した。前者は反映されたが、後者が入っていない。期間履歴の `selection_sha256` と結合 v1 の `selection_sha256` は同名で中身が違うので、結合 v2 で両方を同時に扱うときに取り違えやすい。定義のそばへ 1 文足しておきたい。

### R10-03（低・文書）結合 v1 の一次仕様書に第9回対応節が無い

`HISTORY_VALIDITY_BINDING_PLAN.md` は第8回 R8-05 の節で終わっており、第9回対応の節が無い。R9-05（結合 v1 は `validity_documents` が期間履歴の選択結果から写されたものかを検証しない）は期間履歴側の文書にだけ書かれている。結合 v1 の読み手はその限界に気づけない。結合 v1 側にも 1 文置くか、第9回対応節を足して相互参照にするのが安全である。

## 次工程: 結合 v2 の最小契約案

依頼にある「入力・段・理由・両内部モデルの同一時点評価・UTC 正規化」の最小案を示す。**採用・実装・schema 確定・DB 保存・実接続のいずれも意味しない。**

**狙い。**v1 の弱点は 2 つある。第一に、`validity_documents` が期間履歴の選択結果から作られたかを検証できない（R9-05）。第二に、そのため `period_evidence_timed` を false のまま据え置くしかない。v2 では**投入側の写しを受け取るのをやめ、期間履歴の束そのものを受け取って結合側が候補を選ぶ**。これで両方が同時に解ける。

**mode と依存。**`history_validity_binding_fixture_v2` / `source_origin=offline_fixture`。依存は `strict_input_v1`・`evidence_history_fixture_v2`・`period_evidence_history_fixture_v1`。**結合 v1 は据え置き**（13 キー・閉じた 9 理由・`period_evidence_timed=false` 固定）。履歴 v2 は UTC 正規化を入れる版で、F-03 の既決どおり結合 v2 と同時に導入する。

**入力。**最上位 5 キー `mode` / `source_origin` / `input` / `history` / `period_history`。v1 の `validity_documents` は**受け取らない**（投入側が写しを作る余地を無くす）。`period_history` は期間履歴 v1 の束そのもの。

**段。**v1 の 5 段を保ち、部品が 1 つ増えた分だけ広げる。段1 は封筒。段2 は部品の mode 値差（`history.mode` と `period_history.mode`。どちらも dict で mode キーがある場合の値差だけを直接判定し、依存 API を呼ばない。v1 の R5-01 の決め方をそのまま踏襲）。段3 は 3 部品の構造・内部整合（期間履歴の 20 理由はすべて `INVALID_BUNDLE` へ畳む。内側の理由を外へ出さない）。段4 は F-01 の書式（期間履歴の `receipt_id` / `revision_id` / `series_id` も対象に加える。これで R9-01 の非対称が消える）。段5 は比較。

**段5 の内部順序。**(1) `input.as_of` と `history.decision_at` と `period_history.decision_at` の三者一致を先に判定し、1 つでも違えば `DECISION_TIME_MISMATCH` 単独で打ち切る。(2) 行側モデルを `as_of` で 1 回、期間側モデルを `as_of` で 1 回評価する（**両内部モデルの同一時点評価**）。(3) 必須 subject ごとに、行の選択 hash と期間の有効候補の `record_sha256` の一致、半開区間の充足を見る。

**理由。**v1 の 9 種に `PERIOD_HISTORY_MODE_MISMATCH` を 1 つだけ足した 10 種に閉じる（履歴 mode と対称にするための最小の追加）。期間側の候補欠落・重複・不適合はすべて `VALIDITY_NOT_SATISFIED` へ畳む。期間履歴の構造破損は段3 の `INVALID_BUNDLE`。

**出力。**v1 の 13 キーに `period_series_count` と `period_candidate_count` を足した 15 キー。`period_evidence_timed` は**この mode でだけ true になり得る**。true の条件は、必須 subject ごとに有効候補がちょうど 1 件あり、その `record_sha256` が行の選択 hash と一致し、半開区間を満たし、かつ `reason_codes` が空であること。ひとつでも欠ければ false。`ready_for_live` / `current_signal` は false、`read_only` は true のまま。

**UTC 正規化。**receipt 同一性の比較**のためだけ**に、時刻文字列を UTC・マイクロ秒 6 桁へ正規化してから canonical JSON を作る（F-03 の案 A）。原本は書き換えない。`as_of` / `decision_at` / 区間境界の比較は従来どおり同一時点比較で、正規化の有無に関係なく結果は変わらない。**v1 の完全一致規則は据え置き**、履歴 v2 と結合 v2 でのみ効かせる。混在（v1 履歴 ＋ v2 結合）は段2 の mode 値差で拒否する。

**最小の反証試験（12 件）。**三者の時点が一致し全必須 subject に候補がある成功束で `period_evidence_timed=true` になること／`period_history.decision_at` だけずらすと `DECISION_TIME_MISMATCH` 単独になること／期間側が `PERIOD_OVERLAP_AT_DECISION` を返す束は段3 の `INVALID_BUNDLE` になること／必須 subject の候補が 0 件なら `VALIDITY_NOT_SATISFIED` かつ timed=false ／候補の `record_sha256` が行選択と違えば `VALIDITY_NOT_SATISFIED` ／区間外だけの subject が結合側でも欠落として現れること（v1 では見えなかった経路）／期間履歴に必須外の subject があっても結合の必須集合の判定を変えないこと／`period_history.mode` の版差が `PERIOD_HISTORY_MODE_MISMATCH` 単独になること／期間の ID が F-01 書式に反すると段4 で落ちること／UTC 表記と JST 表記の同一時点が receipt 同一性で一致すること（v2 のみ）／同じ束を v1 の履歴と混ぜると拒否されること／成功時も `ready_for_live` / `current_signal` が false であること。

**この版でやらないこと。**DB 保存・永続化・実原本の取込・日次シグナル接続・実二者審査と通知・実接続。`period_evidence_timed=true` は「宣言された時刻と過去選択の検査に通った」という意味だけで、当時の実際の入手可能性・提供元の真正性・実公表時刻を証明しない。結合 v1 は変更しない。

## 追加した試験

`build-codex/tests/test_period_history_review_opus.py`（30 件、0.11 秒、全通過）を新規追加した。既存試験の期待値と実装コードは変更していない。

人工例の 14 キーと digest、半開区間の 4 境界、有効候補 0 件の空配列 digest、期限外訂正から旧版へ戻らないこと、digest が期間 payload の自己 hash であること（行 hash 版と違う値になることも）、未来 entry の構造検証 3 種、NO_OP が逆行検査より先であること、再取得が件数・選択・digest・head を変えないこと、revision 同一性 4 項それぞれの差が `REVISION_CONFLICT` になること、R9-03 の三分、2 欠陥同時の優先順 6 組、無作為破壊 300 束での契約不変条件と入力不変・決定性、そして既存 4 API を壊しても期間履歴 API が動くこと（非依存の確認）を固定した。

## 指摘なしと確認した範囲

依頼が名指しした 12 論点すべてで、実装は契約どおりだった。新規 API は既存 4 API を呼ばず、行 payload 検証器も流用していない。CLI は `packet_cli._mapping` を同一オブジェクトとして流用しており、link/reparse 拒否・読取中の変化検出・重複キー・非有限値・1,048,576 バイトちょうど通過と +1 バイト拒否がそのまま効く。固定 stderr は 1 種類だけで、既存 3 CLI と同じ文体である。終了コードは成功 0・不成功 2・読取/引数エラー 2（stdout 空）。

Codex の新規試験 55 件・18 件も読み、誤った期待値の焼き込みは見つからなかった。実際に API を呼ぶ試験の hash と件数は私の実測と一致する。

第1〜9回で私が追加した 274 件（54 + 11 + 30 + 30 + 29 + 96 + 10 + 14）は今回も全通過している。

## 残る限界

Codex の全体実測（2945 件）は、手元に `common/tests` と `ops/tests` が無いため私の側では再現できていない。Windows 実機・Codex ランタイム Python では実行していない。期間履歴 API は人工入力のオフライン検査であり、自己申告された時刻の整合を確かめるだけで、当時実際に入手できた事実・提供元の真正性・実公表時刻を証明しない。結合 v2 はまだ設計案で、本文の契約案は採用でも実装でもない。`period_evidence_timed=true` を将来立てても、それは検査に通ったという意味にとどまる。実原本・実適用期間の根拠、永続化・排他・電源断耐久・原本署名・全銘柄の網羅性は引き続き未保証である。人工束が検査を通ることを、実売買適格性へ昇格させないこと。

終了時刻: 2026-09-14 05:57 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、第10回レビュー R10-01〜03（entry["sha256"] のブラケット読みを兄弟と揃える、digest 同名フィールドの区別を注記、結合v1 の一次仕様書へ第9回対応節を追加）への対応と、提示した結合 v2 最小契約案（入力5キー・5段・10理由・両モデルの as_of 同一時点評価・UTC 正規化・15キー出力・反証12件）の採否判断を行ってください。
```
