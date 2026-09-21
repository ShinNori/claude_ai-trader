# Claude Opus 独立レビュー 第2回: F-01〜F-10 対応と結合 API 実装前の契約点検

2026-09-12。実施者 Claude Opus（Cowork、Linux コンテナ）。依頼は[キャッチボール文書](Claude_Opusキャッチボール.md)の B。第1回は[Claude_Opusレビュー_証拠履歴.md](Claude_Opusレビュー_証拠履歴.md)、Codex の対応は[OPUS_EVIDENCE_REVIEW_RESPONSE.md](OPUS_EVIDENCE_REVIEW_RESPONSE.md)。

実装コード・既存試験の期待値・共通仕様・合成データは変更していない。今回は追加の反証試験も作っていない（後述のとおり、現行コードで再現する不具合が 1 件もなく、残る指摘はすべて未実装の設計契約に対するものだったため）。実 API・売買審査・LINE・証券接続・発注・見張り再開・Claude 公開はいずれも行っていない。一時ファイルはすべて Dropbox 外のレビュー用コンテナに置いた。

## 結論

**実装バグは今回も 0 件。** F-09 と F-10 の実装、新しい結合例の値・時点・hash の結び付きはいずれも独立検算で正しかった。

ただし**結合 API を実装する前に決めるべき契約が 3 点、未確定のまま残っている**（R2-01 版境界、R2-02 防御範囲、R2-03 出力スキーマと理由コード）。いずれも「今のコードが間違っている」のではなく「次に書くコードの仕様が一意に定まらない」種類の問題である。加えて F-09 の防御は、Codex 自身が試験で定義した想定（strict_input の VERIFIED 不変条件が将来壊れる）に対して**カバー範囲が 7 経路中 2 経路にとどまる**ことを実測で確認した。

## 検証環境と実測（Codex 実測と区別）

Linux コンテナ、CPython 3.11.15、pytest。Windows 実機・Codex ランタイムでは実行していない。対象ファイルを読み取り専用で複製し、Dropbox へ書き戻していない。

私が再実行した関連範囲は **185 passed、1.59 秒、失敗 0 件**。件数は Codex 報告の「関連 185 件」と一致した（所要時間は環境差）。内訳は次のとおりで、Codex 新規 11 件（2+4+5）も一致した。

| ファイル | 件数 |
|---|---|
| test_evidence_history_fixture.py | 29 |
| test_evidence_history_fixture_cli.py | 5 |
| test_evidence_history_opus_review.py（第1回で私が追加） | 54 |
| test_strict_input.py | 48 |
| test_strict_validity.py | 38 |
| test_strict_validity_defensive_resolution.py（Codex 新規） | 5 |
| test_history_validity_binding_example.py（Codex 新規） | 4 |
| test_history_cli_size_boundary.py（Codex 新規） | 2 |

**全体 2580 passed / 9 skipped、361.00 秒は Codex の実測であり、私は再現していない。**内訳の算術（2515 + 54 + 11 = 2580）は整合している。

## 依頼の重点項目への回答

| 重点 | 判定 | 要旨 |
|---|---|---|
| F-01 の範囲、原本・参照・JSON Pointer の混同 | **混同なし。対象フィールドの列挙が不足** | ID 規則と Pointer を明確に分離できている。ただし銘柄コード規則を適用する場所が列挙されていない（R2-07） |
| F-03 の UTC 正規化採用と v1 再投入契約の維持に矛盾がないか | **文単体は無矛盾。ただし結合 v1 がどちらの意味論かが未定義** | R2-01。最重要 |
| required / matched / extra と診断件数の意味が一意か | **件数の定義は一意。不一致の内訳が復元できず、理由コードが未定義** | R2-03 |
| corrected_after_as_of_count が再投入・再取得・未来初版を誤計数しないか | **誤計数しない設計。成功可否への影響が未記載** | R2-04 |
| period_evidence_timed=false が成功時も必須か | **必須と明記済み。問題なし** | 将来 true になる条件だけ補足推奨（R2-10） |
| F-09 の例外境界が不足/過剰でないか | **不足。過剰はない** | R2-02。実測で 5 経路が素通り |
| F-10 の 1MiB 境界と説明が一致するか | **一致。指摘なし** | 結合束での実質上限だけ補足推奨（R2-08） |
| 新しい例の入力・履歴・期間証拠が同じ値/時点で結び付くか | **結び付いている。指摘なし** | 封筒（mode/source_origin）だけ不足（R2-06） |

## 指摘事項

### R2-01（中・設計課題）結合 API がどの履歴意味論の上に立つかが未定義

再現条件: [結合案](HISTORY_VALIDITY_BINDING_PLAN.md)の F-02 行「内部共用選択モデルによる単一 API を採用」と、F-03 行「次の取込/結合用取得モデルでは…案 A を採用する」「既存履歴 v1 は canonical JSON の完全一致と RECEIPT_CONFLICT を維持」を並べて読む。

期待: 最初に実装する結合 API の receipt 同一性規則が一意に決まること。

実測（文書読解）: 決まらない。F-03 は「結合用取得モデル」を UTC 正規化側に置いているが、F-02 は v1 の検証済み選択モデルを共用すると書いている。さらに F-01 は「結合境界では入力・履歴両方に同じ共用検査を適用する」として新しいコード/ID 書式を**結合 v1 から効かせる**読み方になる一方、F-03 の正規化は「次の版」へ送られている。結果として、結合 v1 は「F-01 は適用・F-03 は未適用」という混在状態になるが、それが意図なのかどうかが本文から確定できない。末尾の「次工程は共用モデルと版境界の実装・反証」は課題の存在を認めているだけで、境界そのものを決めていない。

重要度: 中。実装着手時に最初に詰まる。

修正案: 表の下に版の対応を 1 行で固定する。推奨は次のとおり。

- 結合 v1 の mode は `history_validity_binding_fixture_v1`、依存は `strict_input_v1` + `evidence_history_fixture_v1` + `strict_validity_fixture_v1`。receipt 同一性は **v1 のまま（canonical JSON 完全一致）**。
- F-01 の書式検査は結合 v1 の**結合境界でのみ**適用し、内側の 3 つの v1 API の受入規則は変更しない。
- F-03 の UTC 正規化は `evidence_history_fixture_v2` と `history_validity_binding_fixture_v2` を**同時に**導入するときに初めて効かせる。v1 と v2 を混在させない。
- 結合 API は受け取った履歴 bundle の `mode` が自分の依存版と一致しない場合に固定理由で拒否する（履歴 v1 は既に `mode` を検査しているので追加費用はほぼない）。

### R2-02（中・実装の不足）F-09 の防御が、Codex 自身の想定シナリオの一部しか覆っていない

再現条件: `aitrader/strict_validity.py` の `inspect_strict_input` を「常に VERIFIED を返す」関数へ差し替える（Codex の `test_strict_validity_defensive_resolution.py` と同じ手法）。そのうえで入力側を 1 箇所ずつ壊す。

期待: 追加されたコメント「a future validation-order regression must still fail closed here」のとおり、いずれも `DATA_INCOMPLETE` と固定理由で閉じる。

実測: 7 経路のうち **2 経路だけが閉じ、5 経路は例外が外へ出た**。

| 壊し方 | 実測 |
|---|---|
| `source_documents` を空配列にする | DATA_INCOMPLETE / `INPUT_SOURCE_REFERENCE_INVALID`（想定どおり） |
| `sources["lots"]["0001"]` を削除 | DATA_INCOMPLETE / `INPUT_SOURCE_REFERENCE_INVALID`（想定どおり） |
| `expected_codes` を削除 | **未捕捉 KeyError: 'expected_codes'** |
| `as_of` を削除 | **未捕捉 KeyError: 'as_of'** |
| `as_of` を `"not-a-time"` にする | **未捕捉 TypeError: '<' not supported between instances of 'NoneType' and 'datetime.datetime'** |
| `input` を配列にする | **未捕捉 TypeError: list indices must be integers or slices, not str** |
| `input` キーを削除 | **未捕捉 KeyError: 'input'** |

原因は、`try` の範囲が原本辞書の構築と参照解決だけで、その手前の `data["input"]`・`strict_input["expected_codes"]`・`strict_input["as_of"]` が外に置かれていること、および `as_of` が `None` のまま期間比較（`as_of < valid_from`）へ到達しうることである。

**現行の実行経路ではいずれも到達しない**（`inspect_strict_input` が実際に検査しているため）。したがって稼働中の不具合ではない。ただし Codex が守ると宣言した想定はまさに「その前提が将来崩れる」ケースなので、防御としては不完全である。CLI 経由なら `except Exception` が最終的に終了コード 2 へ落とすが、固定の読取エラー文になり構造化 JSON は返らない。**結合 API は Python 入口を直接呼ぶため、この差は結合後に表面化する。**

過剰な捕捉は見当たらなかった。捕捉している 6 例外型はいずれも参照解決で現実に起こりうる型で、`MemoryError` などを巻き込んでもいない。

重要度: 中（結合実装前に閉じるべき）。

修正案: 防御範囲を「入力検査の結論に依存するすべてのフィールド取得」まで広げる。具体的には `strict_input = data["input"]` から選択行の解決までを 1 つの `try` に入れ、併せて `as_of is None` を期間比較の前に固定理由（`INPUT_INVALID_AS_OF` など）で弾く。試験は現行 5 件に上記 5 経路を足して 10 件にする。

### R2-03（中・設計課題）結合 API の出力スキーマと理由コード集合が未固定

再現条件: 結合案の「結合時に要求する一致」1〜5 と F-05/F-07 行、F-06 行を読み、結合 API の戻り値を一意に書き起こそうとする。

期待: 既存 3 モードと同じ水準で、フィールド名と取りうる理由コードが列挙されていること。

実測: `required_subject_count` / `matched_subject_count` / `extra_subject_count` / `corrected_after_as_of_count` / `period_evidence_timed` / `ready_for_live` / `current_signal` / `read_only` は決まっているが、**`mode`・`source_origin`・`status` の文字列、どの digest を返すか（項目 5 の「digest」が履歴の `selection_sha256` を指すのか別物か）、そして理由コードが 1 つも決まっていない**。

とくに問題なのは、`required - matched` が次の 3 つの異なる失敗を区別せずに畳み込むことである。

1. 必須 subject に過去選択が存在しない（履歴が足りない、または全版が未来）。
2. 過去選択はあるが、strict_input が参照する行の hash と一致しない（入力と履歴が別の版を見ている＝第1回で実例を示した状況）。
3. 行 hash は一致するが、適用期間検査に落ちた。

件数の**定義**は一意だが、件数から**原因**が復元できない。「正常な部品同士の不一致の場合だけ診断件数を返す」という方針は妥当なので、あとは理由コードで原因を示す設計にすればよい。

重要度: 中。

修正案: 結合案へ次を追記する。`mode="history_validity_binding_fixture_v1"`、`source_origin="offline_fixture"`、成功 `status="VERIFIED_OFFLINE_BINDING"`、不足 `status="DATA_INCOMPLETE"`。digest は履歴の `selection_sha256` をそのまま `selection_sha256` として再掲する（新しい digest を作らない）。理由コードは閉じた集合とし、複数該当時はソートして返す。推奨集合は `DECISION_TIME_MISMATCH`（項目 1）、`HISTORY_SELECTION_MISSING`（上記 1）、`ROW_HASH_MISMATCH`（上記 2）、`VALIDITY_NOT_SATISFIED`（上記 3）、`EXTRA_SUBJECT_PRESENT`（F-07）、`CODE_FORMAT_INVALID` / `IDENTIFIER_FORMAT_INVALID`（F-01）、`HISTORY_MODE_MISMATCH`（R2-01）、および構造破損時の単独理由 `INVALID_BUNDLE`。構造破損時は既存 3 モードと同じく全件数 0・digest なしで返す、と明記する。

### R2-04（低〜中・設計課題）corrected_after_as_of_count が成功可否へ与える影響が未記載

集計定義そのものは妥当である。「必須 subject のうち、判定後に初出記録された訂正版（supersedes 非 null）が 1 件以上ある subject 数」「複数訂正でも 1」「再取得/再投入は数えない」「未来の初版だけでは数えない」は、v1 モデルの挙動と正確に対応する。履歴 v1 では初出時にしか版が登録されず、再取得は版も選択も動かさないので再投入・再取得は構造的に数に入らない。また subject の 2 件目以降の版は必ず supersedes 非 null なので、「未来の初版を除く」条件は自動的に満たされる。第1回で行った無作為検査（45,305 比較）の結果とも矛盾しない。

未記載なのは、**この件数が 0 でなくても成功してよいのか**である。成功条件は `required>0` / `matched=required` / `extra=0` の 3 つだけが書かれているので、字面上は「訂正あり」でも成功する。しかし読み手が安全側に倒して失敗条件へ加える実装も十分ありうる。

重要度: 低〜中（実装が二通りに割れる）。

修正案: 「`corrected_after_as_of_count>0` は成功を妨げない。過去選択を使う限り結果は正しく、この件数は先読み危険が近いことを運用者へ示す警告値である」と 1 文で明記する。あわせて、履歴 v1 の `future_revision_count`（**版**数・全 subject）と本件数（**subject** 数・必須のみ・訂正のみ）は別物で、一方を他方の代用にしないことも書き添える。

### R2-05（低〜中・設計課題）extra=0 の運用帰結と、絞り込み時の落とし穴が未記載

`extra=0` を成功条件にすると、実運用では「履歴 bundle をその日の必須 subject だけへ絞ってから結合へ渡す」前処理が必須になる。方針としては賛成（第1回で拒否を推奨したのは私である）が、**絞り込みの正しいやり方が書かれていない**のは危険である。

subject 単位で絞るのは安全だが、**残した subject の entry を判定時点で切り詰めると `corrected_after_as_of_count` が過小になる**。判定後の訂正はまさに切り捨てたい範囲に入るからで、「未来の分は要らない」という自然な発想がそのまま警告値を消してしまう。

重要度: 低〜中。

修正案: 「絞り込みは subject 単位でのみ行う。残した subject については判定時点より後に記録された entry も含めて全件渡す。時刻による切り詰めをしない」と明記する。

### R2-06（低〜中・成果物）結合例に封筒（mode / source_origin）がない

再現条件: `examples/history_validity_binding_valid.json` の最上位キーを見る。`input` / `history` / `validity_documents` の 3 つだけで、`mode` と `source_origin` がない。

実測: そのため結合 API が実装された時点で、この例は最上位の形が変わる。Codex の `test_history_validity_binding_example.py` は最上位を直接読んでいるので、例を直せば試験も直すことになる。既存試験の期待値を触らない運用方針と噛み合わない。あわせて、この例の `validity_documents` はそのままでは `inspect_strict_validity` へ渡せず、試験側で `mode` と `source_origin` を組み立て直している。

重要度: 低〜中（今なら 2 行で済む）。

修正案: いま `"mode": "history_validity_binding_fixture_v1"` と `"source_origin": "offline_fixture"` を最上位へ足す。既存 4 試験は最上位キーの集合を検査していないので、追加しても落ちない（`test_binding_example_same_instant_and_complete_subjects` などはいずれも部分キーの参照のみ）。

### R2-07（低・契約の欠落）F-01 の銘柄コード規則を適用するフィールドが列挙されていない

ID 規則については「原本 document ID、期間 document ID、receipt/revision ID と参照 ID へ同じ ID 規則を使う」「JSON Pointer は ID ではなく、この制限を適用しない」と書き分けられており、**原本・参照・Pointer の混同は起きていない**。これは依頼の重点どおり確認できた。

一方、銘柄コードは「銘柄コードは ASCII 大文字英数字 4〜5 文字」とだけ書かれ、どこに現れるコードへ適用するかが列挙されていない。結合の対象範囲には少なくとも次の 6 箇所がある。`input.expected_codes` の各要素、`input.sources[group]` の**辞書キー**、各 lots/events 行の `code`、`history.entries[].subject.code`、`history.entries[].payload.code`、`validity_documents[].payload.code`。とくに辞書キーとしてのコードは見落としやすい。

重要度: 低。

修正案: 上の 6 箇所を列挙し、「同一 subject を指すこれらは**すべてバイト単位で同一**でなければならない」と付記する。参考として、現在の 4 つの同梱例（`strict_input_valid` / `strict_validity_valid` / `evidence_history_valid` / `history_validity_binding_valid`）に F-01 の書式を機械的に当てはめたところ、ID・コードとも違反は 1 件もなかった。規則を入れても既存例は壊れない。

### R2-08（低・文書）1MiB 上限は結合束では履歴の件数上限として効く

F-10 の記述と実装は一致している。上限は 1,048,576 バイトちょうどまで許容し、1 バイト超過で検査を開始せず固定文と終了コード 2 を返す。Codex の 2 件の境界試験を私の環境でも再実行し、いずれも通ることを確認した。**この項目に指摘はない。**

補足として、結合の入力は入力・履歴・期間証拠を 1 ファイルへ束ねる形（新しい例がその形）なので、1MiB は実質的に履歴 entry の件数上限になる。新しい例の履歴 entry は 1 件あたり約 378 バイトで、**上限まで entry だけで埋めると約 2,700 件**である。結合案に「結合束にも同じ 1MiB 上限が適用され、履歴の件数上限として効く」と 1 文を足すことを勧める。分割や上限引き上げを自動で行わない現在の方針は維持でよい。

### R2-09（低・設計課題）反証リストが F-05/F-06/F-07 の新規則を覆っていない

「必要な反証」6 項目は第1回の時点のままで、今回固定した契約に対応する反証が入っていない。次の 3 つを追加することを勧める。余分 subject が未来にしか現れない履歴を拒否すること。必須 subject に判定後の訂正があるとき、成功しつつ `corrected_after_as_of_count` が 1 になること。コード/ID の書式違反（全角・内部空白・制御文字・128 文字超）を結合境界で拒否すること。

### R2-10（低・文書）period_evidence_timed が将来 true になる条件

「成功/失敗とも `period_evidence_timed=false` を必須出力にする」は明記されており、依頼の重点として**問題なし**である。ひとつだけ、将来この値が true になりうる条件（期間証拠自身が `observed_at` / `recorded_at` を持ち、履歴 subject として選択されたとき）を先に書いておくと、後から解釈が割れない。

### R2-11（参考・軽微）試験ファイル間の結合

`test_strict_validity_defensive_resolution.py` が `test_strict_validity` から `_bundle` を import している。pytest の rootdir 挿入に依存しており、単体選択でも動くことは確認したが、`test_strict_validity._bundle` の変更がこちらの意味を黙って変える結合が生じている。共用したい人工入力は `tests/` 直下の helper か fixture へ出すほうが安全である。指摘というより次に増やすときの注意点。

## 指摘なしと確認した範囲

新しい結合例 `examples/history_validity_binding_valid.json` は、モジュール自身の canonical 実装を使って独立に検算した結果、次のすべてが一致した。lots・events とも履歴 payload が strict_input の参照行と**同一の値**であること、履歴の `sha256` がその行の canonical hash と一致すること、期間証拠の `record_sha256` が同じ行 hash を指すこと、期間証拠 document の自己 hash が正しいこと、原本 document の自己 hash が正しいこと、`input.as_of` と `history.decision_at` が**同一時点**（同一文字列）であること、履歴 subject 集合が必須集合（lots/events × 0001）と完全一致し余分がないこと、適用期間が `valid_from <= as_of < valid_until` を満たすこと。期間証拠に取得時刻フィールドが無いことも Codex の試験が明示的に確認しており、「当時知り得た」を主張していない点は適切である。

F-09 の実装は、捕捉している例外型に過剰がなく、`expected_evidence_count` を維持したまま `verified_evidence_count=0` で閉じる形も妥当である（不足は R2-02）。履歴側の 3 箇所のコメント追加は、到達不能である理由と将来変更時の意図を正しく説明している。

第1回で私が追加した 54 件は今回も全通過しており、F-09/F-10 の変更が履歴モデルの性質（過去時点再現・順序非依存・版識別・失敗時の件数 0）を壊していないことを確認した。

## 残る限界

全体一括実測（2580 件）、Windows 実機、Codex ランタイム Python では実行していない。永続化・排他・電源断耐久・原本署名・実公表時刻・実適用期間・全銘柄の網羅性・提供元の真正性は引き続き対象外で未保証である。結合 API 本体・F-01 の書式検査・F-03 の時刻正規化はいずれも未実装なので、本レビューはコードではなく契約文書に対する点検である。人工入力の内部整合が正しいことを、実売買適格性へ昇格させないこと。
