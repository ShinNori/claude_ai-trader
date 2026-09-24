# 人工結合 v2 束の最小保存・再読契約案（D13-01〜03 への回答）

2026-09-17。Claude（Claude Code、Fable 5.1、サブエージェント 0 体）作成。依頼は [キャッチボール文書](Claude_Opusキャッチボール.md) の D13 依頼（更新 2026-09-17 20:13:34 JST）。一次資料は [STRICT_EVIDENCE_STORAGE_PLAN.md](STRICT_EVIDENCE_STORAGE_PLAN.md) の「最新状況と次の限定契約案」。既存慣行として `db.py` の保存先ガード（Dropbox 外・link 拒否）、`packet_cli._mapping` の安全読取、`backtest_artifacts.py` の一時書込→`os.replace` を参照した。

本書は契約案であり、実装・DB スキーマの確定・試験実行・公開は行っていない。製品・既存契約・試験・共通仕様は変更していない。採否と実装は Codex。

## 結論

**3 判断とも「採用」で、次のとおり一意に固定する。**

| 判断 | 採否 | 固定した契約（要点） |
|---|---|---|
| D13-01 保存単位と識別 | 採用 | 単位は「読取規約を通った結合 v2 束の生バイト列 1 件」。保存 ID は生バイトの SHA-256（内容アドレス）。利用者が ID を与えない。同一バイト再投入は NO_OP、異なるバイトは別 ID（衝突は起きない）。空白・キー順・BOM が違えば別単位。行/期間の ID 名前空間は束内のまま、保存先全体の一意制約へ拡張しない。別束の統合・増分追加はしない |
| D13-02 書込と障害 | 採用 | 束 1 件 = 記録ファイル 1 本（JSON 封筒、束は base64 で原バイト保持）。同一ディレクトリの一時名へ書込→`fsync`→`os.replace` で確定。成功は `os.replace` 復帰後のみ。一時ファイルは読取側が無視。同 ID 並行投入は「どちらが勝っても同じ内容」。電源断は「確定後のファイルは完全か不在か」までしか保証しない（ディレクトリ項目の耐久は保証外） |
| D13-03 再読と再現 | 採用 | 不成功束も保存する（保存操作の成否と束の検査結果は別欄）。ID＝生バイト hash、`entry.sha256`＝payload hash、`record_sha256`＝行 hash、`selection_sha256`＝行選択 digest を混同しない。再読は 記録の安全読取→形式版→base64→hash 一致→JSON 再解析→v2 API を 1 回→保存時出力と全キー比較。不一致は失敗。API 呼出は 保存 1 回・再読 1 回で、二重評価しない |

## 1. D13-01 — 保存単位と識別

- **単位**: `_mapping` と同じ読取規約（通常ファイル・link 拒否・1,048,576 バイト以下・UTF-8/UTF-8-sig・最上位 dict・キー重複なし・非有限値なし）を通った **1 ファイルの生バイト列**。読取規約を通らないものは保存対象にならない（`INPUT_UNREADABLE`、API 非呼出）。
- **ID**: `bundle_sha256` = 生バイト列の SHA-256（hex 64）。保存先（`<home>/evidence_bundles/`）内で一意。利用者は ID を指定しない。
- **同一再投入**: 同じバイト列 → 同じ ID → 既存記録が完全なら `NO_OP`（書き換えない、`stored_at` も更新しない）。
- **衝突**: 異なるバイト列は必ず異なる ID なので「同じ ID で内容が違う」状態は正常経路で生じない。生じ得るのは記録の破損だけで、再読時に `RECORD_CORRUPT` として拒否する。
- **別表記**: 空白・改行コード・キー順・BOM の有無が違えば別バイト列＝別 ID＝別単位。canonical JSON による同一視はしない（保存は「何を投入したか」の証拠であり、意味の同一性は再読後の出力比較で示す）。
- **名前空間**: 行履歴の `receipt_id`/`revision_id`、期間履歴の `receipt_id`/`revision_id`/`series_id` の一意性は各束の内部制約のまま。保存先全体で同名 ID が別束にあっても衝突としない。束をまたぐ履歴の統合・差分追加・既存 home（market.duckdb）への取込は対象外。
- **束の版**: 最上位 `mode` を保存前に検査しない。mode が違えば v2 API が段 1 で `INVALID_BUNDLE` を返し、その結果ごと保存される（D13-03）。

## 2. D13-02 — 書込と障害

### 2-1 形式（単一ファイル）

保存先: `<home>/evidence_bundles/<bundle_sha256>.bundle.json`。`<home>` は `db.default_home()` と同じ解決（`AI_TRADER_HOME` または `~/.ai-trader`）で、`db._runtime_path_guard` と同じ規則（Dropbox 配下拒否・link/junction/reparse 拒否）を `evidence_bundles` ディレクトリまで適用する。

記録ファイル（JSON 1 オブジェクト、`sort_keys`、`ensure_ascii=False`）:

| キー | 値 | 由来 |
|---|---|---|
| `store_format` | `"evidence_bundle_store_v1"` | 固定 |
| `bundle_sha256` | 生バイトの SHA-256 | ファイル名と同一 |
| `bundle_bytes` | 生バイト長 | 1,048,576 以下 |
| `bundle_base64` | 生バイト列の base64（標準、改行なし） | 原バイトを 1 バイトも変えない。BOM も保持 |
| `api_mode` | `"history_validity_binding_fixture_v2"` | 評価に使った API の mode 定数 |
| `output` | v2 API の 15 キー出力そのもの | 保存時に 1 回だけ呼んだ結果 |
| `stored_at` | 保存確定時刻（timezone 付き ISO 8601） | 情報。同一性判定に使わない |

記録ファイルのサイズ上限は 2 MiB（束 1 MiB の base64 ≈ 1.34 MiB ＋ メタ）。再読時の安全読取もこの上限で行う。

### 2-2 手順と成功境界（put）

1. 保存先ガード（Dropbox 外・link なし）。失敗 → `STORE_PATH_INVALID`、API 非呼出。
2. 入力を `_mapping` 相当で読み、同時に生バイト列を得る。失敗 → `INPUT_UNREADABLE`、API 非呼出。
3. `bundle_sha256` を計算。既存記録 `<id>.bundle.json` があり再読検査（§3）が `REPRODUCED` なら `NO_OP` で終了（API 呼出は再読検査の 1 回のみ）。既存が破損なら `RECORD_CORRUPT` で終了（上書きしない。人手で除去）。
4. 解析済み値で v2 API を **1 回**呼ぶ。例外は `INVALID_BUNDLE` に畳まれる（API 側の防御）。
5. 記録 JSON を同一ディレクトリの `<id>.bundle.json.<乱数>.tmp` へ書き、`flush`＋`os.fsync`、close。
6. `os.replace(tmp, final)`。復帰した時点で初めて成功（`STORED`）。失敗 → `WRITE_FAILED`、tmp は残っても構わない（読取側は `.tmp` を記録として扱わない）。

### 2-3 障害境界

| 事象 | 結果 | 再試行 |
|---|---|---|
| 手順 5 で異常終了 | `.tmp` だけ残る。`get`/`verify` は `RECORD_NOT_FOUND` | put を再実行すると新しい tmp で書き直す。古い tmp は無視され、人手で削除可 |
| 手順 6 の直前で異常終了 | 同上 | 同上 |
| 同 ID を並行 put | 両者とも同じバイト列・同じ API 出力。`os.replace` はどちらが後でも完全なファイルに置き換える。`stored_at` だけが後勝ち | 不要。どちらも `STORED` または `NO_OP` |
| 異なる束を並行 put | 別ファイルなので干渉しない | 不要 |
| 電源断 | `os.replace` 復帰前なら記録は不在。復帰後は NTFS 上で「完全なファイルが存在」か「存在しない」のどちらか。ディレクトリ項目の耐久（ディレクトリ fsync）は Windows で行えないため保証しない | 再 put |
| 保存先が Dropbox 配下 | `STORE_PATH_INVALID`。書込前に拒否 | 保存先を変える |

DuckDB は使わない（単一 JSON ファイルで足り、複数 DB の原子性を持ち込まない）。既存 `market.duckdb` とは無関係。

## 3. D13-03 — 再読と再現

### 3-1 手順（verify）

1. 保存先ガード。2. `<id>.bundle.json` を安全読取（通常ファイル・link 拒否・2 MiB 以下・UTF-8・キー重複なし）。無ければ `RECORD_NOT_FOUND`、読めなければ `RECORD_UNREADABLE`。
3. `store_format` が `evidence_bundle_store_v1` でない、または必須 7 キーちょうどでない → `UNSUPPORTED_FORMAT`。
4. base64 復号 → 生バイト。`sha256(生バイト) == ファイル名の id == bundle_sha256` かつ `len == bundle_bytes` でなければ `RECORD_CORRUPT`。
5. 生バイトを `_mapping` と同じ JSON 規則で再解析。失敗 → `RECORD_CORRUPT`。
6. `api_mode` が現行 API の MODE と異なる → `UNSUPPORTED_FORMAT`（未対応版。再評価しない）。
7. v2 API を **1 回**呼び、`output` と 15 キー全てを比較。一致 → `REPRODUCED`、不一致 → `OUTPUT_MISMATCH`（束は健全だが評価が変わった＝実装差分の検知）。

### 3-2 不成功束の扱い

保存する。保存操作の結果（`status`）と束の検査結果（`bundle_status` / `bundle_reason_codes`）を別欄にする。`DATA_INCOMPLETE` の束も `STORED` になり、再読で同じ `DATA_INCOMPLETE` が再現すれば `REPRODUCED`。理由: 拒否された束そのものが「何を投入し何が拒否されたか」の証拠であり、成功束に限ると拒否の再現性を検査できない。売買適格性とは無関係（`ready_for_live`/`current_signal` は常に false）。

### 3-3 hash の対応表（混同禁止）

| 名前 | 計算対象 | 目的 |
|---|---|---|
| `bundle_sha256`（新） | 投入ファイルの生バイト列 | 保存 ID・改変検知（束全体） |
| `entry.sha256`（既存） | 各 entry の `payload` の canonical JSON | payload の自己 hash |
| `payload.record_sha256`（既存・期間） | 対象行レコードの canonical JSON | 行と期間の結合 |
| `selection_sha256`（既存・結合 v2 出力） | 行選択の射影 | 行選択の同一性。期間・束全体の改変検知には使えない |

### 3-4 API 出力（put / verify 共通、固定 13 キー）

`mode`=`evidence_bundle_store_v1` / `source_origin`=`offline_fixture` / `status` / `reason_codes` / `bundle_sha256` / `bundle_bytes` / `bundle_status`（15 キー出力の `status`、未評価なら null） / `bundle_reason_codes`（同、未評価なら `[]`） / `stored_at`（未保存なら null） / `record_path`（保存先相対 `evidence_bundles/<id>.bundle.json`、未保存なら null） / `ready_for_live`=false / `current_signal`=false / `read_only`=false（put）・true（verify）。

`status` の閉じた語彙: `STORED` / `NO_OP` / `REPRODUCED` / `DATA_INCOMPLETE`。`reason_codes` の閉じた語彙（不成功時 1 件のみ、最初の不合格で打切り）: `STORE_PATH_INVALID` / `INPUT_UNREADABLE` / `WRITE_FAILED` / `RECORD_NOT_FOUND` / `RECORD_UNREADABLE` / `UNSUPPORTED_FORMAT` / `RECORD_CORRUPT` / `OUTPUT_MISMATCH`。

### 3-5 段と呼出回数

| 操作 | 段 | 呼ぶもの | 回数 |
|---|---|---|---|
| put | 1 保存先ガード → 2 入力読取 → 3 既存確認 → 4 評価 → 5 一時書込 → 6 確定 | `_mapping` 相当 1、既存があれば verify 1（その中の API 1）、無ければ API 1 | API は必ず 1 回 |
| verify | 1 保存先ガード → 2 記録読取 → 3 形式版 → 4 hash → 5 再解析 → 6 API 版 → 7 評価・比較 | 安全読取 1、API 1 | API は 1 回 |

put 後に確定ファイルを読み返して再評価はしない（二重評価の新保証を足さない）。

### 3-6 CLI（1 API = 1 CLI の慣行）

`aitrader/evidence_bundle_store_cli.py put --input <file> [--home <dir>]`、`… verify --id <sha256> [--home <dir>]`。読取は `_mapping` を同一オブジェクトとして流用（put の入力）。終了コード: `STORED`/`NO_OP`/`REPRODUCED` → 0、`DATA_INCOMPLETE` → 2（stdout に JSON）、引数・読取・例外 → 2（stdout 空、固定 stderr 1 文 `人工束を保存・再読できません。保存先と入力形式を確認してください。`）。`-h/--help` は R13-01(b) と同じ例外扱い。

## 4. 人工入力と固定出力

E1 = `examples/history_validity_binding_v2_valid.json`（6,148 バイト、CRLF、BOM なし）。生バイト sha256 = `381454c638620b9f9eb66550a4e667b5bb4b4ad02399ad12b220e9e67353f7cf`（実測。これが保存 ID）。

put（初回）の固定出力:

```json
{"bundle_bytes": 6148, "bundle_reason_codes": [], "bundle_sha256": "381454c638620b9f9eb66550a4e667b5bb4b4ad02399ad12b220e9e67353f7cf", "bundle_status": "VERIFIED_OFFLINE_BINDING", "current_signal": false, "mode": "evidence_bundle_store_v1", "read_only": false, "ready_for_live": false, "reason_codes": [], "record_path": "evidence_bundles/381454c638620b9f9eb66550a4e667b5bb4b4ad02399ad12b220e9e67353f7cf.bundle.json", "source_origin": "offline_fixture", "status": "STORED", "stored_at": "<保存時刻>"}
```

`stored_at` だけは固定できない（試験では timezone 付き ISO 8601 であることと、NO_OP で変わらないことを検査する）。記録の `output` は `tests/binding_v2_expected.json` の E1 と全キー一致。verify の固定出力は上の `status` を `REPRODUCED`、`read_only` を true、`stored_at` を記録の値にしたもの。

## 5. 反証表（実装時の試験案。上限 30 件目安）

| # | 束・操作 | 期待 |
|---|---|---|
| 1 | E1 を put | `STORED`、ID `381454c6…`、`bundle_status` VERIFIED、記録 `output` が E1 期待値と一致 |
| 2 | 同じファイルを再 put | `NO_OP`、`stored_at` 不変、ファイル mtime 不変、API 1 回のみ |
| 3 | E1 末尾に改行 1 個を足して put | 別 ID `b5536b2e…`（実測先頭 16 桁）で `STORED`。両記録の `output` は同一 |
| 4 | E1 に BOM を付けて put | 別 ID `d1c20261…` で `STORED`。base64 復号後のバイトに BOM が残る |
| 5 | E2（時点差）を put | `STORED`、`bundle_status` DATA_INCOMPLETE、`bundle_reason_codes` `["DECISION_TIME_MISMATCH"]`、終了 0 |
| 6 | 1,048,577 バイトの入力 | `INPUT_UNREADABLE`、API 非呼出、ファイル未作成 |
| 7 | 最上位が list の JSON | 同上 |
| 8 | `--home` が Dropbox 配下 | `STORE_PATH_INVALID`、API 非呼出 |
| 9 | `evidence_bundles` が symlink | 同上 |
| 10 | E1 の記録の base64 を 1 文字変えて verify | `RECORD_CORRUPT`、API 非呼出 |
| 11 | 記録の `bundle_sha256` だけ変えて verify | `RECORD_CORRUPT` |
| 12 | 記録の `store_format` を `v0` に | `UNSUPPORTED_FORMAT`、API 非呼出 |
| 13 | 記録の `api_mode` を v1 の mode に | `UNSUPPORTED_FORMAT`、API 非呼出 |
| 14 | 記録の `output.matched_subject_count` を 1 に | `OUTPUT_MISMATCH`（API 1 回） |
| 15 | 記録にキーを 1 個追加 | `UNSUPPORTED_FORMAT` |
| 16 | 存在しない ID を verify | `RECORD_NOT_FOUND` |
| 17 | `.tmp` だけ残った状態で verify | `RECORD_NOT_FOUND`（tmp を記録として読まない） |
| 18 | `.tmp` が残った状態で put | `STORED`（新しい tmp で確定。古い tmp は残存） |
| 19 | 手順 5 で例外を注入して put | `WRITE_FAILED`、確定ファイル不在 |
| 20 | `os.replace` で例外を注入 | `WRITE_FAILED`、確定ファイル不在、tmp 残存 |
| 21 | 同一束を 2 スレッドで put | 両者 `STORED` または片方 `NO_OP`。最終ファイルは完全で verify が `REPRODUCED` |
| 22 | 異なる束 2 件を並行 put | 双方 `STORED`、互いに干渉なし |
| 23 | 既存記録が破損した状態で同 ID を put | `RECORD_CORRUPT`、上書きしない |
| 24 | put 中の API 呼出回数 | 1（monkeypatch で計数） |
| 25 | verify 中の API 呼出回数 | 1 |
| 26 | put の入力ファイルが不変 | バイト・サイズ・mtime 不変 |
| 27 | 記録 JSON が `sort_keys` で 7 キーちょうど | 形式固定 |
| 28 | CLI: put 成功・verify 成功で終了 0、DATA_INCOMPLETE 束で終了 2＋JSON、引数不備で固定 stderr | v1 CLI 慣行 |
| 29 | 2 MiB を超える記録ファイルを verify | `RECORD_UNREADABLE`、API 非呼出 |
| 30 | v1 結合束（`history_validity_binding_valid.json`）を put | `STORED`、`bundle_status` DATA_INCOMPLETE `["INVALID_BUNDLE"]`（mode 差は保存を妨げない） |

fuzz は不要（内容アドレスと固定手順で状態空間が小さい）。

## 6. 未確定として残すもの

- `stored_at` の時計源（`datetime.now(timezone.utc)` を提案。JST 表記にするかは Codex）。
- 記録ファイルの拡張子・保存先ディレクトリ名（`evidence_bundles/`、`.bundle.json` は案）。
- 一時ファイルの掃除コマンドを持つか（本案は持たない。人手で削除）。
- 既存 `db._runtime_path_guard` を流用するか同型を複製するか（流用なら `db.py` の import で duckdb 依存が付く点に注意）。

## 7. 範囲外

実原本の取込、増分 DB、既存 home の移行、日次シグナル接続、提供元の真正性、複数 DB の原子性、電源断耐久の保証、保存済み束の一覧・削除 API。

更新時刻: 2026-09-17 22:27 JST。保存のみ・未公開・未送信。

## Codex採否・優先補足（2026-09-17採用、2026-09-25 R14統合）

旧「Codex 採否・実装契約」節は本節に置換済みとして削除し、採否の優先節をここへ一本化した。不正IDはRECORD_UNREADABLE、入力読取拒否はAPIのDATA_INCOMPLETE＋JSON・CLI終了2を維持する。

D13-01〜03を以下の修正付きで採用する。本節は上の草案と衝突する場合に優先する。新規オフライン保存API/CLIだけを対象とし、既存v1/v2・共通安全読取・共通Parser・db.pyは変更しない。

1. §6の4点：時計源はdatetime.now(timezone.utc)、ディレクトリevidence_bundlesと拡張子.bundle.jsonを採用。一時ファイル掃除コマンドは追加しない。db.default_homeと既存_runtime_path_guardを流用し、DB接続/作成は行わない。guard由来のduckdb import依存は既存依存を使用する。
2. API呼出は各操作で「最大1回」。保存先/入力/記録/形式/hash等で打切れば0回。NO_OP判定でverify相当の評価を1回済ませたら再評価しない。
3. SHA-256衝突を数学的に不可能とはしない。既存IDの記録が健全でも投入raw bytesとの完全一致を確認し、不一致ならRECORD_CORRUPTで上書きしない。同値JSONの別表記は別IDという通常規則を維持する。
4. stored_atは確定前に採取したUTCの記録作成時刻。os.replace復帰時刻を正確に表すとはしない。逐次NO_OPでは不変。並行初回putでは両者STOREDと時刻後勝ちを許す。
5. os.replaceの正常復帰を保存成功境界とするが、電源断後の「完全か不在」や復帰前なら必ず不在という保証は削除する。電源断/外部改変後は安全再読で検査する。ディレクトリ耐久、悪意ある同時パス差替えを完全に排除するOS機構の保証は含めない。
6. putの入力には_mappingを同一オブジェクトで流用し、生バイトは同等のlink/通常file/サイズ/前後stat検査付きで取得する。解析値と保存バイトの対応を検査し、単純な独立read_bytesは使用しない。元バイト列を再解析した値を評価対象にする。保存記録は2MiB上限、復号束は1MiB上限。
7. CLIは保存操作のstatusで終了値を決める。内側bundle_statusがDATA_INCOMPLETEでもSTORED/NO_OP/REPRODUCEDなら終了0。APIのDATA_INCOMPLETEは理由を含むJSONと終了2（INPUT_UNREADABLE等を含む）。引数/予期しない例外は固定stderr・stdout空・終了2。反証表28の「DATA_INCOMPLETE束で終了2」は保存操作自体の失敗に読み替える。helpは既存R13-01(b)の例外。
8. verifyのidは小文字ASCII16進64字だけ。パスに連結する前に拒否し、RECORD_UNREADABLEへ写像。出力比較は全キー/値の型も含める（trueと1を同一視しない）。記録のhash/長さ/base64/時刻等も型を検証し、未知の形式/API版と破損を区別する。

実装・関連試験の実測と操作例はREADMEの本工程完了節に記録する。固定例のbytes/hashは既存ファイルから確認し、examplesは変更しない。


### 公開操作と生バイトの読取補足

公開APIはput(input_path, home=None)とverify(bundle_sha256, home=None)。putは元入力を安全に一度生バイト読取し、排他的に作成する一時snapshotへ同じバイトを置いて、同一オブジェクトの_mappingで解析する。元入力の再読による別時点の値との取り違えを避ける。snapshot作成失敗はWRITE_FAILED、元入力/JSON読取不合格はINPUT_UNREADABLE。snapshotはbest-effortで削除し、確定記録として扱わない。verifyはメモリ上の同等JSON解析を行い、snapshot・ディレクトリ・記録を作らない。


保存hashは記録内容の整合検査であり署名ではない。記録とファイル名を共に改変できる者に対する真正性や、stored_atという申告時刻の外部証明は提供しない。出力比較は現在の同じmode実装での再現確認であり、実装バイナリの版固定/過去版実行環境の保存ではない。


### 理由順・失敗出力の補足

verifyはID形式を最初に検査し、その後に保存先ガードを行う。invalid IDとinvalid homeの複合不正はRECORD_UNREADABLEが先。安全読取のJSON不正はRECORD_UNREADABLE、7キー/保存版違いはUNSUPPORTED_FORMAT、base64/hash/長さ/stored_atの破損はRECORD_CORRUPT、API版違いはUNSUPPORTED_FORMAT。記録outputの形・型・値の相違はAPIを1回評価した後のOUTPUT_MISMATCHであり、trueと1も区別する。

失敗時も13キーを維持。判明済みのbundle_sha256/bundle_bytesは保持し、不明値はnull。未評価ならbundle_status=null・bundle_reason_codes=[]。書込失敗で評価済みなら内側のstatus/reasonsを診断に保持する。OUTPUT_MISMATCHでは再評価した内側結果と既存stored_at/record_pathを保持し、それ以外の失敗ではstored_at/record_path=null。putの既存記録再読失敗は一律RECORD_CORRUPT、記録を変更しない。

### R14-01採否・実装保留（2026-09-25）

第14回独立確認のR14-01（中）を受領し、推奨(a)+(b)と自分の一時ファイルのbest-effort後始末を採用する方針を決定。ただし製品ファイルへの書込みがAccess deniedで停止したため、修正は未反映・未検証であり、上記の現行契約・反証表は変更しない。

再開時の修正案は、putのRECORD_UNREADABLEに限り最大3回・間隔5msで再読し、形式/hash/出力不一致は即時拒否する。os.replace失敗後は安全な既存記録の形式/hash/API版/束JSONと投入raw一致を確認し、保存outputと既に評価した自前outputを型込みで照合できた場合だけNO_OPにする。API再呼出は行わず各操作最大1回を維持する。確認できなければWRITE_FAILED。自分のtmpのみbest-effort削除し、既存自作試験1件は非残留を必須とする期待へ変更予定（未変更）。

8スレッド以上の結果がSTORED/NO_OPのみとなる試験、API回数・破損非上書き・有限再読・後始末の回帰、および指定Windows関連実測は未実施。電源断/悪意ある外部変更/別実装版の競合保証を追加しない。修正案の保存場所と再開条件はQUESTIONS.mdの本依頼hash節を参照。

### R14-01採用確定（2026-09-25、上記保留記録より優先）
putのRECORD_UNREADABLEのみ5ms間隔で最大3回（初回を含む）再読し、形式/hash/出力不一致は即時拒否する。
置換失敗時は安全再読・投入raw一致・既評価outputの型込み一致を確認できた場合のみNO_OP、その他はWRITE_FAILED（API再呼出なし、各操作最大1回）。
自分のtmpのみbest-effortで削除する。Claude移植済みCodex案をSHA-256で受領確認。上記の未反映・未検証・変更予定は当時の停止履歴とする。
