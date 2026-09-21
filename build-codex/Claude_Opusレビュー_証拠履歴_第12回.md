# Claude 独立レビュー 第12回: 行履歴 v2・結合 v2 API 実装の重要差分 3 点

2026-09-17。実施者 Claude（Claude Code、Windows 実機、モデルは Claude Fable 5.1。サブエージェント 0 体）。依頼は[キャッチボール文書](Claude_Opusキャッチボール.md)の B（更新 2026-09-17 06:02:46 JST）。契約は [HISTORY_VALIDITY_BINDING_V2_CONTRACT.md](HISTORY_VALIDITY_BINDING_V2_CONTRACT.md)（第11回案の補正付き採用）。

読んだのは B の一覧の先頭 6 ファイル（実装 2・試験 2・期待値 JSON・人工例）だけ。README・継続プロンプト・ロードマップは読んでいない。製品コード・既存試験・期待値・例・共通仕様・合成データは変更していない。追加は新規試験 1 ファイルのみ。実 API・売買審査・LINE・証券接続・発注は行っていない。

## 結論

**実装バグ 0 件、契約違反 0 件。重大指摘なし。** 判断 3 点はすべて「一致」。既存 49 件と重複しない反証 30 件を新規追加し、全通過を実測した。軽微な所見が 1 件（R12-01、記録のみ。修正・往復は不要）。

| 判断 | 結果 |
|---|---|
| 1. 行履歴 v2: 2 path UTC 比較・書式ゲート・receipt 前の時刻解釈・原本 payload/hash 保持・v1 互換 | **一致。** 判定順は「キー/canonical → 識別子 → 時刻解釈 → receipt → subject → observed>recorded/逆行 → payload → hash → supersedes → revision/head」で契約どおり。正規化コピーは `dict(entry)` の浅い複製で 2 path だけ置換し、payload・sha256・supersedes に触れない。受入域は正規表現で固定され、Python 版に依存しない。`decision_at` は契約どおり v1 の `_aware` のまま |
| 2. 結合 v2: 段順・各 1 回評価/時点差非呼出・digest 防御・期間重複・subject 内短絡・extra 和集合 | **一致。** 段 1〜4 は v1 と同型で期間側の `series_id`/`supersedes` も F-01 対象。段 5a の三者不一致は評価前に打切り全 0。行 digest None は期間評価前に `INVALID_BUNDLE`、期間の `SELECTION_HASH_INVALID` も `INVALID_BUNDLE`、`PERIOD_OVERLAP_AT_DECISION` は候補空＋`VALIDITY_NOT_SATISFIED`。subject 内は `continue` で最初の不合格だけ。extra は行 `subjects` ∪ 期間全 revision の subject − 必須 |
| 3. 未検証境界の反証追加 | 30 件追加（下記）。全通過。既存 49 件の期待値は変更していない |

期待値 JSON（E1〜E9）は、第11回で私が参照計算した digest・件数と全項目一致していた（E1 `e6f112…`、E6 `194ecd…`、E7 `3ec233…`、series/candidate も同値）。「例から出力を作って期待値へ上書きしない」方式も試験の構造から確認した。

## 追加した反証 30 件（`tests/test_history_validity_binding_v2_claude_contract.py`）

既存 49 件が触れていない境界に限定した。命名は TEAM_WORKFLOW の新規則。

行履歴 v2（15 件）:
- 秒未満ゼロ埋め（`.000000` / `.0`）と zone 差の再投入が NO_OP になること
- observed > recorded の判定と recorded 逆行の判定が **zone をまたいでも**成立すること（既存は同 zone のみ）
- 再投入で payload だけ変えた束が `RECEIPT_CONFLICT`（hash 検査に到達しない。正規化が 2 path に限られる裏付け）
- `decision_at` は 7 桁秒未満を受け、entry 側は拒否する非対称を契約どおり固定
- 識別子破損が時刻解釈より先、時刻解釈が逆行・payload 破損より先
- 非文字列の時刻 5 種（None / int / list / dict / bool）が例外でなく `INVALID_TIMESTAMPS`
- 年 1 の**正確な**境界: `0001-01-01T00:59:59+01:00` は範囲外、`01:00:00+01:00` はちょうど UTC 年 1 の 0 時で範囲内、負のオフセットは範囲内
- 年 9999 の最終 entry を `23:59:59.999999+09:00` にしても受理
- 束全体を UTC/−05:00 で表記し直しても 13 キー出力が同一で入力は不変

結合 v2（14 件）:
- 三者の時点を 3 通りの表記（+09:00 / Z / −05:00 + 秒未満）にしても E1 と同一出力
- 段 2: `history` が list でも `period_history` の mode 差は隠れない。`mode: null` は不一致
- 期間 entries が空なら段 3 `INVALID_BUNDLE`（件数 0・digest null）
- 行履歴のコード書式違反が期間重複より先（段 4 > 段 5）
- 行 hash 差＋期間候補なしは `ROW_HASH_MISMATCH` 単独（subject 内短絡）
- 重複＋行欠落は `["HISTORY_SELECTION_MISSING", "VALIDITY_NOT_SATISFIED"]` 昇順、matched 0、候補 0、digest は E6 と同一
- 行履歴側だけの未来 extra subject が `EXTRA_SUBJECT_PRESENT`（既存は期間側と両側のみ）
- extra subject への as_of 後の訂正は `corrected_after_as_of_count` に数えない
- expected_codes 2 件の成功束: required 4・matched 4・series 4・candidate 4・timed true、digest は履歴 v2 単体 API の digest と同一（期間 digest ではない）。期間 subject を 1 つ抜くと matched 3・candidate 3・`VALIDITY_NOT_SATISFIED`
- 結合 v1 は v1 例で成功のまま、v2 束を渡すと `INVALID_BUNDLE`。v2 に v1 束を渡しても `INVALID_BUNDLE`
- 表記不変性 fuzz 300 束（仮説: 行履歴 v2 と三者時点の zone・秒未満の表記をどう変えても E1 の出力は不変。固定 seed。違反 0・入力改変 0）

## 所見

### R12-01（記録のみ・修正不要）年 1 の溢れ方向

契約 §3-3 の実測例「`0001-01-01T00:00:00+09:00` と `9999-12-31T23:00:00-05:00` が溢れる」は正しいが、私が第12回の初稿で「負のオフセットでも年 1 で溢れる」と書いた反証は誤りで、実装が正しかった（負のオフセットは UTC が進む）。実装・契約とも変更不要。契約に「溢れるのは年 1 では正のオフセット、年 9999 では負のオフセットに限る」と 1 文足すと、次に読む人が同じ誤解をしない。

## 実測（Codex 実測と区別）

- 当方: 新規 30 件を 1 回実行。`30 passed / 0.18秒`。Windows 11、`C:/Users/s/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`（CPython 3.12.14）、`-p no:cacheprovider`、basetemp は Dropbox 外（`C:/Users/s/AppData/Local/Temp/ai-trader-r12-claude`）。初稿の 1 件失敗は上記 R12-01 の期待値誤りで、修正後に全通過。既存 49 件と全体試験は再実行していない（再実測 0 点）。
- Codex 実測（引用のみ、未再実行）: 2026-09-17 全体 3024 passed / 11 skipped、新規 49 件。

対象ファイルの sha256（レビュー時点）:

```text
42466e58736ff52d17f628629d04fac543cff01a6520775fa9cc9d7db7f224cb  aitrader/evidence_history_fixture_v2.py
946c6e98c6400391eadb278307c50e48ed9f19329c37381e164331383d58af99  aitrader/history_validity_binding_v2.py
3d6b445515bdb55876377e03573c88bec9d1190cb407ca6d832018bde39ded09  tests/test_evidence_history_fixture_v2.py
ed60b413c3e984efc6c18918bc5b2c031d2d72014f18c311f39cb1d993a264df  tests/test_history_validity_binding_v2.py
9bdb9ec864334f23ae12413d35983d16a22eb4ff8a0a1d7de4ca17fe23b4c13b  tests/binding_v2_expected.json
381454c638620b9f9eb66550a4e667b5bb4b4ad02399ad12b220e9e67353f7cf  examples/history_validity_binding_v2_valid.json
eaea04af2ff5f49f9d2182da7ff456993f3e28feb1683e623b953fdba9e57b32  tests/test_history_validity_binding_v2_claude_contract.py（新規, 16453 bytes, 2026-09-17T14:27:14+09:00）
```

## 指摘なしと確認した範囲・残る限界

作者の自己検証（49 件）と本独立レビュー（コード読解＋30 件）を区別する。専用 v2 CLI・保存・実接続は未実装で範囲外。`period_evidence_timed=true` は人工履歴の検査に通った意味にとどまる。fuzz は表記不変性の 1 仮説だけで、構造破壊 fuzz は今回行っていない（第10回の 3 万束は期間 v1 単体が対象で、結合 v2 の構造 fuzz は未実施）。

終了時刻: 2026-09-17 14:29 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、第12回レビュー（実装バグ 0・契約違反 0、追加反証 30 件全通過、R12-01 は記録のみ）の受領と、新規試験ファイルを含めた関連試験の Windows 実測、README への短い記録を行ってください。
```
