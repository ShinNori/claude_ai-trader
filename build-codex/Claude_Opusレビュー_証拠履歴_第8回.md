# Claude Opus 独立レビュー 第8回: R7-01〜03 の対応と、期間証拠の観測・記録履歴の設計候補

2026-09-13。実施者 Claude Opus（Cowork、Linux コンテナ）。依頼は[キャッチボール文書](Claude_Opusキャッチボール.md)の B（更新 2026-09-13 08:18 JST）。第1〜7回は[原レビュー](Claude_Opusレビュー_証拠履歴.md)、[第2回](Claude_Opusレビュー_証拠履歴_第2回.md)〜[第7回](Claude_Opusレビュー_証拠履歴_第7回.md)。

今回は観点ごとに 7 体のサブエージェントを並列で走らせ、親が突き合わせて統合した（ユーザー指示）。各体の結論は親がすべて再現・検算しており、裏の取れなかった主張は本文へ入れていない。

実装コード・既存試験の期待値・共通仕様・合成データ・専用例は変更していない。追加したのは専用の新規試験 1 ファイルのみ。実 API・売買審査・LINE・証券接続・発注・見張り再開・Claude 公開はいずれも行っていない。一時ファイルはすべて Dropbox 外のレビュー用コンテナに置いた。

## 結論

**R7-01〜03 の対応はすべて妥当で、実装バグは 0 件。**成功条件へ「reason_codes が空であること（status=VERIFIED_OFFLINE_BINDING と同値）」が入り、私が挙げた反例（matched=required でも期間証拠の余分 subject で不成功）まで本文に書かれている。AttributeError の見送りも支持できる。**製品コードは第7回時点とバイト単位で同一**であることを全数照合で確かめた。

**期間証拠の観測・記録履歴（PERIOD_EVIDENCE_TIMING_PLAN.md）は、いまのままでは実装に入れない。**止めているのは 2 件で、どちらも「決めていない」ことが原因である。ひとつは期間系列のキーと「訂正／追加区間」の区別（R8-01）、もうひとつは複数区間を持つ期間履歴から、結合 v1 が要求する「subject ごとちょうど 1 件」の期間証拠へどう絞るか（R8-02）。前者は設計案自身が決定事項として挙げているが、選択肢と帰結が書かれていない。後者は設計案にも既存文書にも記述がなく、放置すると凍結済みの strict_validity v1 契約と衝突する。

**残る指摘は 5 件。**R8-01・R8-02 が中（実装前に決める必要がある）、R8-03〜05 が低。すべて設計・文書の課題で、実装バグはない。本文の後半に、この 2 件を解いた**最小の次の契約案**を置いた。

## 検証環境と実測（Codex 実測と区別）

Linux コンテナ、CPython 3.11.15、pytest 9.1.1。Windows 実機・Codex ランタイムでは実行していない。対象を読み取り専用で複製し、Dropbox へ書き戻していない（本レビュー文書と追加試験の 2 ファイルを除く）。

**「文書と新規試験のみ」という申告を全数照合で裏取りした。**第7回時点の `aitrader/` 全ファイルと今回の版を sha256 で比較し、差異は 0。`tests/` は `test_binding_multicode_codex.py` が 1 ファイル純増しただけで、既存 20 ファイルはバイト単位で同一。`examples/` も 4 ファイルとも無変更。第7回で私が追加した 96 件は無変更のまま全通過している。

私が実行した関連範囲は **21 ファイル 464 passed、1.91 秒、失敗 0 件**。Codex 報告「463 passed / 1 skipped / 1.64 秒」と合計 464 で一致する。差の 1 件は実 symlink 作成試験で、本コンテナは root 権限のため skip されず実行されて通過した（Codex 側の Windows 権限不足による skip と同じ試験）。今回追加した 10 件を含めると **474 passed、1.97 秒**である。

**Codex 全体実測（前回 2728 passed / 10 skipped / 562 warnings / 305.83 秒）は Codex の実測で、私は再実行していない。**Codex がこれを「後続 19 件・今回追加群を含む最新全体とは表記しない」と断っている点も、README 末尾・キャッチボール文書の双方で確認した。私の 464 件・474 件、および第7回の 6,000 束・2,428 束・4,000 束の各検査はすべて私の実測である。

## 重点項目への回答

| 重点 | 判定 |
|---|---|
| R7-01 成功条件へ reason_codes 空を明記 | **指摘なし。反例の束を作り、文書どおりに読めることを実測** |
| R7-02 AttributeError を現状維持とする判断 | **妥当。ただし前提の明記を推奨（R8-05）** |
| R7-03 2 銘柄 4 subject の複合理由と件数分配 | **指摘なし。6 件とも期待値は契約どおり。未固定の組合せを私が 10 件追加** |
| 期間系列キーを subject だけにするか | **R8-01。決めていない。系列 ID を明示する案を推奨** |
| 訂正と追加区間の区別 | **R8-01。履歴 v1 の線形モデルをそのまま持ち込むと表現できない** |
| 原本文書と receipt/revision の関係 | **R8-03。二重の識別子体系になる。写し方を 1 文で決めれば済む** |
| 実装前に決めるべき矛盾・不足 | **R8-01〜04。最小の次の契約案を本文に提示** |

## R7-01〜03 の対応確認

**R7-01。**F-05/F-07 節の成功条件は「required>0、matched=required、extra=0、時点一致、かつ reason_codes が空であること（status=VERIFIED_OFFLINE_BINDING と同値）。matched=required でも期間証拠の余分 subject などの理由があれば不成功」となった。第7回で私が挙げた反例そのものが本文に入っている。実際に `expected_codes` にない銘柄の期間証拠を 1 件足した束を作り、`VALIDITY_NOT_SATISFIED`・matched=required=2・extra=0 で `DATA_INCOMPLETE` になることを再実測した。出力節・段順序節・matched 定義節と矛盾する言い回しも残っていない。**指摘なし。**

**R7-02。**見送りの判断は妥当である。理由は 2 つある。第一に、私が第7回で試した 63 経路に加えて、今回 JSON 由来の型だけを使った 2〜3 箇所同時変異 4,000 ケースを回しても、AttributeError を含む未捕捉例外は 1 件も出なかった。第二に、AttributeError を 6 種へ足しても守りは増えない。構造検査（`isinstance` と `set(x)` の一致）を通過する dict／str のサブクラスを Python API へ直接渡せば、`__getitem__` や `strip()` から**任意の**例外を投げられるからである。実際に `__getitem__` だけを差し替えた dict サブクラスで、結合 API・期間検査 API・入力 API の 3 つから AttributeError が外へ出ることを確認した（履歴 API は `.get()` 経由のため漏れなかった）。守るべきはこの入口ではなく、契約の前提の方である（R8-05）。Codex が第7回節へ「4 モジュールが現在同じ広さの捕捉を持つと主張するものではない」「CLI の Exception 捕捉とメモリ API の例外境界は別契約」と書いたのは正確である。

**R7-03。**`tests/test_binding_multicode_codex.py` の 6 件を 1 件ずつ契約と突き合わせ、**誤った期待値の焼き込みはなかった**。damage_count=3 の束は名実ともに 3 理由の複合で、体裁だけの複合試験ではない。corrected が「訂正回数ではなく subject 数」であること、同一 subject への複数訂正が 1 に丸められること、4 subject 一致でも期間証拠の余分 subject で不成功になることを、それぞれ正しく突いている。ただし **`extra_subject_count` を一度も非ゼロにしておらず、銘柄数も 2 止まり**である。私が下記の組合せを自分で組んで実測したところ、実装はすべて契約どおりだった（下表）。試験として固定されていなかっただけである。

| 組合せ | 実測 |
|---|---|
| 2 銘柄 4 subject ＋ 履歴だけの余分 subject 1 件 | `EXTRA_SUBJECT_PRESENT` 単独、required=4・matched=4・extra=1 |
| 同上、余分 subject が lots と events の 2 件 | extra=2、matched は 4 のまま |
| 余分 subject が未来にしか無い場合 | extra=1（未来だけの余分も数える） |
| 3 銘柄 6 subject で別々の subject に欠落と行 hash 差 | 2 理由併記、required=6・matched=4 |
| 4 件数すべて非ゼロ | `EXTRA_SUBJECT_PRESENT`＋`ROW_HASH_MISMATCH`、(4,3,1,1) |
| 2 subject に未来訂正 | 成功のまま corrected=2 |
| 欠落 1 件と訂正 2 件の併存 | `HISTORY_SELECTION_MISSING`、(4,3,0,2) |
| 4 subject で時点不一致 | `DECISION_TIME_MISMATCH` 単独、(4,0,1,1)、digest null |
| 4 subject で書式違反 | `CODE_FORMAT_INVALID`、4 件数 0、digest null |

## 期間証拠の設計候補への指摘

### R8-01（中・設計課題）期間系列のキーと、訂正／追加区間の区別が決まっていない

再現条件: 同じ `lots/0001` について、区間 A（2026-01-01〜2026-06-01、単元 100）と区間 B（2026-06-01〜2026-12-01、単元 200）がどちらも真正に成立する人工束を作る。

期待: B を「A の訂正」と「A とは別の追加区間」のどちらとして登録するかが、規則から一意に決まること。

実測（文書読解）: 決まらない。規則 3 は「訂正は同じ期間系列の直前 revision と hash を明示する」とだけ書き、系列の同一性をどう決めるかを書いていない。系列キーを subject だけにすると、B を登録した時点で A は「末尾一版」から外れ、過去の対象期間を失う（文書自身が末尾で指摘している）。かといって B を supersedes=null の新しい初版にすると、履歴 v1 の分岐拒否と同じ規則を持ち込んだ場合に衝突する。設計案は規則 1〜6 を先に書き、最後に「系列キーは未確定」「規則 3〜5 は系列キー確定後に具体化する」と断る構成になっているため、読み手はどこまでが暫定か判別できない。

重要度: 中（この 1 点が決まらないと内部データモデルが定義できず、実装の起点が無い）。

推奨する単一の結果: **entry に `series_id` を明示させ、投入側に宣言させる。**訂正は同じ `series_id` の線形チェーン、追加区間は新しい `series_id`。API は区間の重なりから訂正か追加かを推測しない。こうすると規則 3 の「直前 revision と hash」はそのまま使えて、履歴 v1 の線形モデルも系列単位でそのまま流用できる。代案は「系列キーに区間（valid_from と valid_until）を含める」だが、これだと valid_until を直す訂正が別系列になってしまうので採らない方がよい。

### R8-02（中・設計課題）複数区間の期間履歴から、結合 v1 の「ちょうど 1 件」への橋渡しが無い

再現条件: 期間履歴が `lots/0001` について 2 つの区間を保持している状態で、その内容を結合 v1 の `validity_documents` に渡す。

期待: 結合 v1 が受け取る形が一意に決まること。

実測（文書読解）: 書かれていない。凍結済みの `STRICT_VALIDITY.md` は「各銘柄の lots と events について証拠をちょうど 1 件ずつ要求する。欠落、余分な対象、同じ対象の重複、文書 ID の重複、原本 hash の不一致を拒否する」と定めている。したがって期間履歴が複数区間を保持したまま全件を渡すと、ただちに v1 契約違反になる。設計案の「候補のデータ関係」表は期間 payload・revision・receipt・過去選択の 4 単位を挙げるだけで、この変換に触れていない。

重要度: 中（凍結契約との接続点が空白で、実装が二通りに割れる）。

推奨する単一の結果: 「期間履歴から、評価時点で選択された各系列の版のうち **`valid_from <= as_of < valid_until` を満たすものがちょうど 1 件**であることを要求し、その 1 件だけを `validity_documents` へ写す」と明記する。2 件以上になる束は期間履歴側で拒否する（重なりは投入側に直させる）。0 件は拒否せず、欠落として結合側の `VALIDITY_NOT_SATISFIED` に任せる。この規則にすると、既存 v1 を一切変えずに接続できる。

### R8-03（低・設計課題）期間 revision の識別子と `validity_documents.id` が二重になる

`validity_documents` は `{id, sha256, payload}` を持ち、`id` は一意な文字列である。新設計の期間 revision は別に `revision_id` を持つ。同じ「期間宣言」に 2 系統の識別子が並ぶことになるが、対応規則（1 対 1 か、1 つの `id` に複数 revision が対応し得るか）が決まっていない。設計案自身が「IDの同一性と原本 document との参照構造…は未確定」と認めている。

推奨: 「結合へ写すとき、`validity_documents.id` には選択された期間 revision の `revision_id` をそのまま使う」と 1 文で決める。二重体系にならず、F-01 の ID 書式も 1 つの規則で済む。

### R8-04（低・文書）`period_evidence_timed=true` の昇格先が決まっていない

結合 v1 は `period_evidence_timed=false` 固定で、出力キーは 13 個で固定である。設計案は「true の意味は未確定」と書くだけで、昇格したときに 13 キーへ足すのか、新しい mode の別 schema にするのかが読めない。

推奨: 「true の導入は新 mode（結合 v2）でのみ行い、結合 v1 の 13 キー・閉じた 9 理由には手を加えない」と 1 文足す。併せて、昇格の最小条件（行側と期間側の過去選択を同一 as_of で評価し、必須 subject ごとに `record_sha256` 一致と半開区間を満たすこと）を書いておくと、次に迷わない。

### R8-05（低・文書）例外境界の前提を「JSON 由来の値だけを信頼する」と明記する

再現条件: `__getitem__` だけを例外送出に差し替えた dict サブクラス（構造検査は素通りする）を、`inspect_history_validity_binding` へ Python から直接渡す。`expected_codes` の要素を `strip()` を差し替えた str サブクラスにしても同じ。

期待: 契約から、この入力が範囲内か範囲外か読めること。

実測: 結合 API・`inspect_strict_validity`・`inspect_strict_input` の 3 つで、注入した AttributeError がそのまま外へ出る（`inspect_evidence_history` は `.get()` 経由のため漏れなかった）。一方、JSON から作れる値だけを使った変異では 4,000 ケースで未捕捉例外 0 件。つまり **CLI 経由では到達不能で、Python API を直接呼ぶ側が敵対的オブジェクトを渡したときだけ起きる**。

重要度: 低（実害はない。脅威モデルの外にあることが書かれていないだけ）。

推奨: R7-02 の記述へ「本 API は `json.loads` 由来の値（素の dict / list / str / int / float / bool / None）だけを信頼する。`__getitem__` や `strip` を差し替えた Python オブジェクトの直接注入は契約範囲外」と 1 文足す。これで「AttributeError を足すか」という問い自体が正しく閉じる。なお 4 入口を見直すときのために 1 点記録しておくと、`strict_input.py` の参照解決を囲む内側の except は `(ValueError, TypeError, OverflowError)` の 3 種で、兄弟モジュールの内側 4 種より狭い。現在の `_resolve` は ValueError しか投げないため実害はないが、見直しのチェックリストには載せておきたい。

## 最小の次の契約案（R8-01・R8-02 を解いた形）

設計案への対案として、実装着手の可否を判断できる粒度まで具体化した。**新 mode の採用・実装・schema 確定を意味しない。**

**mode と依存。**`period_evidence_history_fixture_v1` / `source_origin=offline_fixture`。既存 4 API を呼ばず、変更もしない独立 API とする。履歴 v1 の内部構造（構造検証と評価時点選択の分離）を手本にするが、payload の形が違うので流用はしない。

**入力。**最上位は `mode` / `source_origin` / `decision_at` / `entries` の 4 キー。各 entry は `receipt_id` / `revision_id` / `series_id` / `subject` / `observed_at` / `recorded_at` / `payload` / `sha256` / `supersedes` の 9 キーちょうど。`payload` は現行の期間宣言 5 キー（`group` / `code` / `record_sha256` / `valid_from` / `valid_until`）をそのまま使う。`sha256` は payload の canonical JSON の SHA-256。`supersedes` は同じ `series_id` の末尾 revision だけを参照でき、別系列・別 subject・前方参照・自己参照・分岐は拒否する。

**出力。**14 キー（履歴 v1 の 13 キーに `series_count` を足した形）: `mode` / `source_origin` / `status` / `reason_codes` / `receipt_count` / `revision_count` / `noop_receipt_count` / `series_count` / `selected_series_count` / `future_revision_count` / `selection_sha256` / `ready_for_live` / `current_signal` / `read_only`。`period_evidence_timed` は**出さない**（結合 v1 の出力に手を入れないため）。銘柄・ID・区間・本文は返さない。

**理由コード。**履歴 v1 と同じく失敗時は単独理由・全件数 0・`selection_sha256=null`。語彙は履歴 v1 と同形（`INVALID_BUNDLE` / `INVALID_MODE` / `INVALID_ORIGIN` / `INVALID_DECISION_AT` / `INVALID_ENTRIES` / `INVALID_ENTRY` / `INVALID_IDENTIFIER` / `INVALID_SUBJECT` / `INVALID_TIMESTAMPS` / `RECORDED_AT_REVERSED` / `INVALID_PAYLOAD` / `PAYLOAD_HASH_MISMATCH` / `INVALID_SUPERSEDES` / `RECEIPT_CONFLICT` / `REVISION_CONFLICT` / `SELECTION_HASH_INVALID`）に、期間固有の 3 種を足す。`PERIOD_INTERVAL_INVALID`（`valid_from >= valid_until`、timezone 欠損）、`SERIES_REFERENCE_INVALID`（`series_id` を跨ぐ参照）、`PERIOD_OVERLAP_AT_DECISION`（下記）。

**評価と選択。**系列ごとに「初出 `recorded_at <= decision_at` の末尾 revision」を選ぶ。観測だけが判定前で記録が判定後なら選ばない。選ばれた版のうち `valid_from <= decision_at < valid_until` を満たすものを有効候補とし、**同一 subject の有効候補が 2 件以上なら `PERIOD_OVERLAP_AT_DECISION` で拒否**する。0 件は拒否しない（欠落の判定は結合側の責務）。`selection_sha256` は有効候補を `{group, code, series_id, revision_id, sha256}` で subject 順に並べた配列の hash とする。

**成功条件。**`reason_codes` が空であること（`status=VERIFIED_OFFLINE_PERIOD_HISTORY` と同値）。有効候補が 0 件でも内部整合が正しければ成功する（履歴 v1 と同じ境界であり、実行可能な入力が揃った意味ではない）。

**結合への橋渡し。**この API は件数と digest しか返さない。結合へ渡す `validity_documents` は投入側が有効候補 1 件を写して作り、`id` には選択された期間 revision の `revision_id` をそのまま使う。これで既存 v1 の「subject ごとちょうど 1 件」を満たしたまま接続でき、識別子も二重にならない。

**`period_evidence_timed` の昇格。**この API では扱わない。将来 true にするのは結合 v2 だけとし、行側と期間側の過去選択を同一 as_of で評価して、必須 subject ごとに `record_sha256` 一致と半開区間を満たしたときに限る。結合 v1 は false 固定のまま据え置く。

**最小の反証試験（12 件）。**適用開始は過去だが期間初出記録が判定後（欠落扱い）／観測は判定前・記録は判定後（選ばない）／記録と区間開始がともに as_of ちょうど（候補かつ期間内）／区間終了が as_of ちょうど（半開区間で不適合）／判定前の初版と判定後の訂正（初版を使う）／同値の未来訂正（revision を区別し初版を維持）／旧版の再取得（系列末尾も過去選択も巻き戻さない）／同一 receipt の完全一致再投入（NO_OP）／同一 receipt で内容差（`RECEIPT_CONFLICT`）／別系列の revision を supersedes が参照（`SERIES_REFERENCE_INVALID`）／同一 subject で有効候補が 2 件（`PERIOD_OVERLAP_AT_DECISION`）／期間 payload だけ差し替えて旧 hash を流用（`PAYLOAD_HASH_MISMATCH`）。

**この版でやらないこと。**`record_sha256` が実際の行 hash と一致するかの検査（結合側の責務）、行履歴・期間検査・結合 v1 との突き合わせ、`period_evidence_timed` の昇格、UTC 正規化、F-01 の ASCII 書式（結合境界だけの規則）、DB 保存・永続化・実取込・通信。いずれも本案の範囲外である。

## 追加した試験

`build-codex/tests/test_binding_multicode_gaps_opus.py`（10 件、0.06 秒、全通過）を新規追加した。既存試験の期待値と実装コードは変更していない。

内容は上の表の 9 組合せと、2 銘柄が健全なままであることの確認である。Codex の複数銘柄試験が触れていない「余分 subject × 複数銘柄」「3 銘柄 6 subject」「4 件数すべて非ゼロ」「corrected が 2 subject に跨る」「複数銘柄での段4・段5 の打切り」を機械可読に固定した。試験を書く過程で、`recorded_at` は入力順に逆行できないため余分 subject を未来訂正より前に置く必要があることも分かった（これは履歴 v1 の既存契約どおりの挙動である）。

## 指摘なしと確認した範囲

R7-01〜03 の対応はいずれも過不足がない。実測の帰属（Codex 実測と Claude 実測の区別）、未実装の表記（設計案を実装済みと読ませない書き方）、文書間の整合（成功条件・段順序・corrected の定義・CLI の 1MiB 規約・例外境界の方針）にも食い違いは見つからなかった。過去節に残る「未実装」の記述についても、第6回対応節の「過去節は各レビュー時点の記録」という断りが機能している。

`PERIOD_EVIDENCE_TIMING_PLAN.md` の反証表 13 行は、いずれも既存 v1 の契約と整合しており、設計上の期待として妥当である。特に「行 hash は同じだが期間 payload だけ変更し旧 payload hash を流用」を拒否する行と、「検査に通る古い期間 revision を探して都合よく戻す処理は採らない」という規則 5 は、この工程で最も効く安全側の決めである。

第1回で私が追加した 54 件、第3回の 11 件、第4回の 30 件、第5回の 30 件、第6回の 29 件、第7回の 96 件は今回も全通過している。

## 残る限界

全体一括実測は私の側では再実行していない。Windows 実機・Codex ランタイム Python では実行していない。今回の設計レビューは文書読解と既存 API の挙動に基づくもので、期間証拠履歴はまだ 1 行も実装されていない。私が示した最小の契約案は設計案であって、採用の決定でも実装の保証でもない。`series_id` を投入側に宣言させる案は「誰が区間の同一性を決めるか」を投入側へ移す設計であり、投入側が誤って宣言した場合の検出力は上がらない。実原本・実公表時刻・実適用期間・当時の入手可能性の根拠、永続化・排他・電源断耐久・原本署名・全銘柄の網羅性・提供元の真正性は引き続き未保証である。人工束が検査を通ることを、実売買適格性へ昇格させないこと。

終了時刻: 2026-09-13 08:32 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、第8回レビュー R8-01〜05（期間系列キーへの series_id 明示、結合へ渡す期間証拠を有効候補1件に絞る規則、revision_id と validity_documents.id の対応、period_evidence_timed 昇格は結合v2 のみ、例外境界の前提を JSON 由来値と明記）への対応と、最小契約案の採否判断を行ってください。
```
