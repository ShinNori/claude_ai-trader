# Claude 独立確認 第14回: 人工束 put/verify の原子性境界・hash 照合・API 呼出回数

2026-09-20。実施者 Claude（Claude Code、Windows 実機、モデルは Claude Fable 5.1。サブエージェント 0 体）。依頼は[Claude引き渡しプロンプト](Claude引き渡しプロンプト.md)の B（更新 2026-09-17 22:40:24 JST、マーカー `d0d39bd8…`）。

読んだのは B の指定どおり `evidence_bundle_store.py`・`evidence_bundle_store_cli.py` の全文、既存試験 2 本の試験名と並行試験 2 件の本文、契約案末尾の Codex 採否 2 節、依存の `_mapping`/`_is_link`/`_unique_object`・`db._runtime_path_guard`。README・継続プロンプトは読んでいない。製品・既存試験・例・共通仕様は変更していない。追加は新規試験 1 ファイルのみ。

## 結論

**単一スレッドの経路に実装バグは無く、hash 照合・API 最大 1 回・破損非上書き・型込みの全キー比較は契約（末尾の「優先補足」節）と一致する。指摘は 1 件（R14-01、中）: 同一束の多重並行 put で、健全な記録に対して `WRITE_FAILED` と偽の `RECORD_CORRUPT` が返る。** データは壊れない（最終記録は常に健全）が、契約の「並行初回 put は両者 STORED を許す」および Codex 自身の試験の期待（結果は STORED/NO_OP のみ）に反する。

| 確認点 | 結果 |
|---|---|
| raw hash 照合 | **一致。** ID＝生バイト sha256。verify は base64 の正規形・長さ・ファイル名 ID・記録内 hash の 4 者一致を API の前に検査。put の既存確認は再現性に加えて投入バイトとの完全一致も見る |
| API 最大 1 回・早期拒否 0 回 | **一致。** 保存先・ID・入力・記録形式・hash・API 版の各拒否で 0 回。STORED で 1 回、NO_OP は再読検査の 1 回だけ、OUTPUT_MISMATCH も 1 回（実測） |
| 原子性境界 | **逐次では一致。** 一時ファイル→flush→fsync→`os.replace` 復帰で成功。置換失敗は確定ファイル不在・内側診断保持・場所欄 null。**多重並行は R14-01** |
| snapshot と記録 tmp の失敗分類 | **一致。** snapshot 作成失敗は `WRITE_FAILED`（API 0 回）、記録書込/置換失敗は `WRITE_FAILED`（API 1 回、内側 status 保持）。snapshot は正常終了時に残らない（実測） |
| NO_OP 非更新 | **一致。** `stored_at`・mtime 不変。別パスの同一バイトも NO_OP |
| 破損非上書き | **一致。** 型破損・重複キー・健全だが出力が変わった記録のいずれも put は `RECORD_CORRUPT` で拒否し、バイト不変 |
| output 型差を含む全キー比較 | **一致。** `true`→`1`、非 dict（list/null/str/0）とも API 1 回の後 `OUTPUT_MISMATCH` |
| 理由順 | **一致。** verify は ID 書式→保存先→記録読取→形式→破損→API 版→評価。採否 2 節の食い違い（不正 ID の理由、入力読取失敗の返し方）は実装が末尾の「優先補足」に従っており、B の「末尾優先」と整合 |

## 指摘

### R14-01（中・並行時の誤分類）同一束の多重並行 put が `WRITE_FAILED` と偽の `RECORD_CORRUPT` を返す

再現条件: 空の保存先へ、同じ入力ファイルを 8 スレッドが同時に `put`（Barrier で同時開始）。40 回反復。Windows 11 / NTFS / CPython 3.12.14。
期待: 契約「優先補足」4「並行初回 put では両者 STORED と時刻後勝ちを許す」、および `test_same_bundle_concurrent_puts_leave_reproducible_record` の期待（結果 ⊆ {STORED, NO_OP}）。
実測（put 320 回）: STORED 118 / NO_OP 173 / **`WRITE_FAILED` 27（8.4%）/ `RECORD_CORRUPT` 2（0.6%）**。検証スレッド 2 本を同時に回した別条件では `WRITE_FAILED` 38、verify 側に一過性の `RECORD_UNREADABLE` 7。**最終記録は 40 回とも `REPRODUCED` で、原バイトも一致（データ破損は 0）。** 一時ファイルは 40 回中 12〜20 回で残留した。Codex の既存試験は 2 スレッドのため衝突がほぼ起きず検出できない。
原因（コード読解）: (1) Windows の `os.replace` は置換先を別スレッドが開いている間 `PermissionError` になり、`put` はこれを `WRITE_FAILED` に写す。(2) 既存確認の安全読取は前後の `lstat`（inode・mtime）一致を要求するため、読取中に別スレッドの置換が入ると「観測中に変化」で失敗し、`_verify` が `RECORD_UNREADABLE`、`put` がそれを一律 `RECORD_CORRUPT` に写す。(3) 失敗時に自分の一時ファイルを消さない。
重要度: 中。データは壊れず再試行で直るが、`RECORD_CORRUPT` は契約上「上書きしない。人手で除去」の案内と結び付いており、**健全な記録を利用者が削除する誤誘導**になり得る。日次運転で複数プロセスが同じ束を投入する設計にするなら先に直す価値がある。単一プロセス運用なら実害は無い。
修正案（採否は Codex）:
- (a) `os.replace` が失敗したら既存記録を再読し、健全かつ投入バイトと一致なら `NO_OP` を返す（自分の一時ファイルは best-effort で削除）。
- (b) put の既存確認で読取失敗（`RECORD_UNREADABLE` 相当）のときは短い間隔で数回だけ再読し、それでも読めない場合に限り `RECORD_CORRUPT` とする。形式・hash の不一致は従来どおり即 `RECORD_CORRUPT`。
- (c) 直さない場合は契約に「多重並行では `WRITE_FAILED` と一過性の `RECORD_CORRUPT` が出得る。再試行で解消し、記録の削除は verify が繰り返し失敗した場合に限る」と明記し、既存試験の期待を合わせる。
私の推奨は (a)+(b)。どちらも新しい語彙・新しい保証を足さず、API 呼出回数も最大 1 回のまま保てる（(a) の再読は NO_OP 判定と同じ verify 1 回で、その put では自前の評価結果を捨てる）。
新規試験には状態語彙を固定していない（安全性の不変条件だけを固定）。

### 記録のみ

- `stored_at` の検査は Python の `fromisoformat` に依存し、基本形式 `20260917T130000Z` も受理する（第11回 R11-01 と同種の版依存）。`stored_at` は同一性に使わない情報欄なので実害なし。UTC 以外の offset（`+09:00`）は `RECORD_CORRUPT` で、採否の「UTC の記録作成時刻」と整合。
- 保存先ガードは `db._runtime_path_guard` を `<home>/evidence_bundles` に適用しており、同関数が見る `market.duckdb` 系の観測はこのディレクトリ内では常に不在で無害。

## 追加した反証 37 件（`tests/test_evidence_bundle_store_claude_contract.py`）

関数は 16、parametrize 展開で 37（型破損 9 種・不正 ID 6 種・非 dict output 4 種・引数エラー 6 種の境界固定のため 30 目安を超過）。既存 48 件と重複しない点だけ。

- 健全だが出力が変わった記録への put: `RECORD_CORRUPT`・非上書き・API は再読検査の 1 回のみ
- 保存済み `output` が非 dict（4 種）: API 1 回の後 `OUTPUT_MISMATCH`、内側 status と `record_path` を保持
- 記録の型破損 9 種（`stored_at` の offset/naive/数値、base64 の改行・過剰 padding、`bundle_bytes` の float/bool/+1、hash の大文字）: verify・put とも `RECORD_CORRUPT`、API 0 回
- 重複キーの記録: verify `RECORD_UNREADABLE`、put `RECORD_CORRUPT`、非上書き、API 0 回
- `evidence_bundles` や home が通常ファイル: `STORE_PATH_INVALID`、API 0 回
- 不正 ID 6 種（None/int/大文字/改行付き/空白付き/bytes）と `Path` 型: `RECORD_UNREADABLE`、保存先を作らない
- 別パスの同一バイト: NO_OP・`stored_at`/mtime 不変・ディレクトリに一時ファイルが残らない
- 保存先ディレクトリ内に置いた入力: 既存記録と入力の両方が不変
- verify は `OUTPUT_MISMATCH` 時も何も書かない（名前・サイズ・mtime 不変）
- `os.replace` 失敗: `WRITE_FAILED`、内側診断保持、`stored_at`/`record_path` は null、確定ファイル不在、API 1 回
- 記録の base64 復号が入力と完全一致、`output` が期待値 E1 と一致
- 8 スレッド×5 回の同時 put: 最終記録は健全・原バイト一致、STORED が 1 件以上、失敗結果は場所欄を主張しない（R14-01 の安全側の不変条件）
- CLI: 2 回目の put は NO_OP・終了 0・13 キーが sort 済み、引数エラー 6 種は固定 stderr で API 非呼出

## 実測（Codex 実測と区別）

- 当方: 新規 37 件を 1 回実行し `37 passed / 1.91 秒`。並行不変条件の試験は追加で 5 回反復し全通過。R14-01 の計測は Dropbox 外の一時 home を使う単発スクリプト（put 640 回・verify 1,600 回、fuzz ではなく同一入力の反復）。Windows 11、Codex 同梱 CPython 3.12.14、`-p no:cacheprovider`、basetemp は Dropbox 外。既存 48 件と全体試験は再実行していない（再実測 0 点）。
- Codex 実測（引用のみ）: 2026-09-17 関連 186 passed / 5 skipped。

sha256（レビュー時点）:

```text
7dedcee4fe3eb68003b86d408430f760ee9097506cd06e55068d79cb0c8c9761  aitrader/evidence_bundle_store.py（13,847 bytes。B の記載と一致）
adc28cf739f83dbe2bec63ffd368aa8da6d9241e33e80462f5e0b5bd633b8e6d  aitrader/evidence_bundle_store_cli.py
3bbe35a9bf4dc0210b0687ed9de902dd773cae9b57690e6f270f7627cbfa0cc3  tests/test_evidence_bundle_store.py
f27c17d7b1323eda196b5302ce93cc41b5b15e2a88788bf70e1f8a1ae09338b4  tests/test_evidence_bundle_store_cli.py
1436cb1e62cf2709acef56e85dfbe0b7c7c95d073babef162ccef53df4d9fb82  tests/test_evidence_bundle_store_claude_contract.py（新規, 11,183 bytes, 2026-09-20T09:09:10+09:00）
```

## 契約文書の整理について（依頼）

契約案末尾に Codex の採否節が 2 つあり（「Codex 採否・実装契約」と「Codex採否・優先補足」）、不正 ID の理由と入力読取失敗の返し方で食い違う。実装は後者に従っている。前者に「後者に置換済み」と 1 行入れるか削除して、優先節を 1 つにしてほしい。これは 2026-09-17 22:34 の質問（担当の一本化）の実質的な解でもある。

## 範囲外

電源断保証、真正性の証明、実取込、増分 DB、一覧/削除、日次接続、複数プロセス（別 Python プロセス）間の競合計測（今回はスレッドのみ）。

終了時刻: 2026-09-20 09:11 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、第14回 put/verify 独立確認の受領と、R14-01（同一束の多重並行 put で WRITE_FAILED と偽の RECORD_CORRUPT が出る）の採否判断・修正、契約末尾の採否 2 節の一本化を行ってください。
```
