# v2 CLI（行履歴 v2・結合 v2）の範囲と読取規約 — 契約案

2026-09-17。Claude（Claude Code）作成。採用・実装は Codex の判断。本書は未決契約の整理であり、実装・試験実行・公開・送信は行っていない。読んだのは `history_validity_binding_cli.py`、`evidence_history_fixture_cli.py`、`packet_cli._mapping`、`tests/test_history_validity_binding_cli.py` の試験名だけ。

## 結論（提案）

**v1 CLI 2 本と同型の新規モジュール 2 本を追加し、v1 CLI・`_mapping`・`_Parser` は 1 バイトも変えない。** 変わるのは import 先の API と固定文言だけ。第10回で期間履歴 CLI に適用した「`_mapping` を同一オブジェクトとして流用」をそのまま踏襲する。

| 項目 | 提案 | 根拠 |
|---|---|---|
| モジュール | `aitrader/evidence_history_fixture_v2_cli.py`、`aitrader/history_validity_binding_v2_cli.py` | v1 CLI に `--version` 等の分岐を足すと閉じた v1 契約が開く。既存 4 CLI と同じ「1 API = 1 CLI」 |
| 引数 | `--input <path>` のみ必須。他の引数なし | v1 と同一。`_Parser` を流用 |
| 読取 | `packet_cli._mapping` を同一オブジェクトとして流用 | link/reparse 拒否・読取中の変化検出・重複キー拒否・非有限値拒否・BOM 許容・最上位 dict 必須・1,048,576 バイトちょうど通過／+1 バイト拒否 が自動で効く |
| 1MiB 境界 | 段 1（封筒）より前のファイル読取境界。超過は API 非呼出・固定 stderr・終了 2・stdout 空 | v1 と同一。結合 v2 束は行履歴＋期間履歴で v1 より大きくなるが、上限は据え置く（人工束の検査用。実データ規模は範囲外） |
| 成功判定 | 行履歴 v2: `status == "VERIFIED_OFFLINE_HISTORY"` → 0。結合 v2: `status == "VERIFIED_OFFLINE_BINDING"` → 0。それ以外 2 | v1 と同じ status 文字列（API が v1 と同じ語彙） |
| stdout | 成功・不成功とも API 結果 JSON を 1 行（`ensure_ascii=False, sort_keys=True, allow_nan=False`）＋改行。読取/引数/例外時は空 | v1 と同一 |
| stderr | 読取/引数/例外時に固定 1 文＋改行。それ以外は空 | v1 と同一。文言は下記 |
| 固定文言 | 行履歴 v2: `証拠履歴v2を検査できません。入力形式と時刻表記を確認してください。`／結合 v2: `履歴v2と期間履歴の結合を検査できません。入力形式とサイズを確認してください。` | v1 の 2 文と同じ文体で、v2 の特徴（時刻表記・期間履歴）を 1 語だけ入れる。v1 の文言は変えない |
| 終了コード | 0 成功／2 不成功／2 読取・引数・例外（stdout 空） | v1 と同一。1 は使わない |
| v1 束を v2 CLI へ | API に到達し、行履歴 v2 は `INVALID_MODE`、結合 v2 は `INVALID_BUNDLE`（段 1 の mode 差）で JSON を出し終了 2 | CLI は mode を見ない。判定は API の段 1 に任せる（v1 と同じ分担） |
| v2 束を v1 CLI へ | 変更なし。v1 API が拒否する（行履歴 v1 `INVALID_MODE`、結合 v1 `INVALID_BUNDLE`） | 第12回で実測済み |
| `__main__` 登録 | v1 CLI が `__main__.py` に登録されていない（`packet_cli.generate` のみ）ので、v2 も登録しない。`python -m aitrader.<module> --input …` で起動 | 既存と同じ |

## やらないこと

- 標準入力からの読取、複数ファイル、出力先指定、`--pretty` 等の整形オプション、mode の自動判別。
- v1 CLI・`_mapping`・`_Parser`・既存試験の変更。
- DB 保存・実取込・実接続・日次シグナル接続。

## 試験案（Codex 実装時。v1 CLI 試験 6 件と同型＋v2 固有 3 件）

同型 6 件: 例ファイルで実 API に到達／成功と DATA_INCOMPLETE が JSON で出る／1MiB ちょうどは API 呼出・+1 バイトは非呼出／不正 JSON・非 dict は固定 stderr で API 非呼出／link 入力は固定 stderr で API 非呼出／引数不備と API 例外は固定 stderr。

v2 固有 3 件: v1 例を v2 CLI に渡すと JSON（`INVALID_MODE` / `INVALID_BUNDLE`）で終了 2／時刻表記を UTC に変えた行履歴 v2 例が v1 例と同じ digest を stdout に出す（第12回の表記不変性を CLI 越しに 1 件だけ固定）／固定 stderr が v1 の 2 文と異なり、かつ 1 種類だけであること。

例ファイル: 行履歴 v2 は `examples/evidence_history_valid.json` の mode 差し替えで足りるが、CLI 試験がファイルを要するなら `examples/evidence_history_v2_valid.json` を新規追加（v1 例は変更しない）。結合 v2 は既存の `examples/history_validity_binding_v2_valid.json` を使う。

## 未決として残すもの

- 1MiB 上限を結合 v2 で据え置くか。据え置きを提案するが、多銘柄の人工束で不足するなら別途引き上げ（全 CLI 共通の `_MAX_MAPPING_BYTES` に触れるため、その場合は v1 CLI 4 本の再実測が必要）。
- 固定文言の最終文面（上記は案。Codex が README の CLI 表記に合わせて調整してよい）。

更新時刻: 2026-09-17 20:09 JST。保存のみ・未公開・未送信。

## Codex採否（2026-09-17、21:46再公開依頼への対応）

依頼hash: b2a500f8f656d4d1410f11d08d40870c7b8911577861559390c894c3954e1b21。

- 1MiB据え置きは採用。人工束のオフライン検査という範囲に適合し、共用読取境界とv1契約を変更する必要がない。
- 固定文言案は不採用。実作業開始時点で両v2 CLIは14:41の作業により存在し、HISTORY_VALIDITY_BINDING_V2_CONTRACT.mdの採用済みCLI契約と既存test_history_v2_clis.pyが現文言を固定していた。今回の既存試験無変更条件と整合させ、行履歴は「証拠履歴を検査できません。入力形式と訂正の参照を確認してください。」、結合は「履歴と適用期間を検査できません。入力形式とサイズを確認してください。」を維持する。v1と異なる文言という提案試験も採用せず、各CLIで常に同じ1文となることを検査する。
- その他の読取・引数・JSON出力・終了コード・API委譲規約は採用済み実装と一致するため、その実装を再利用する。新規モジュールを今回初めて作成したとは記録しない。v1コード・既存試験・既存例は変更しない。
- UTC表記不変性は、同じv2入力のJST表記版とUTC表記版のdigest一致として検査する。v1 APIのdigestとv2 APIのdigestが一致するという保証は追加しない。
- 今回は不足する新規CLI試験2本を追加し（入力は一時ファイルに作成、例ファイル追加なし）、指定5ファイルと既存v2 CLI試験の関連範囲をWindowsで検証する。製品実装が変わらないため全体再実行は不要と判断。実測はREADME末尾を参照。


## Codex採否（2026-09-17、既存実装との差分調整）

本案受領前にv2 CLI 2本とtests/test_history_v2_clis.pyは実装済み。再実装せず既存成果を使う。モジュール分離、_mapping/_Parserの同一オブジェクト流用、引数、終了値、JSON、読取境界、未登録方針を採用する。

- 結合v2の1MiB据え置きを採用。既存の共通上限を変えず、境界はAPI段1より前、超過時はAPI非呼出。
- 固定文言案は不採用。既に実装・検証済みの行履歴「証拠履歴を検査できません。入力形式と訂正の参照を確認してください。」、結合「履歴と適用期間を検査できません。入力形式とサイズを確認してください。」を維持する。各CLI内で固定1文であることが要件であり、v1と異なる文言である必要はない。提案の「v1の2文と異なる」assertは追加しない。
- 試験ファイルをCLI別2本へ分割する必要はない。既存の共通CLI試験1本を使い、新たに不足する契約だけ専用ファイルへ追加する。既存試験期待値は変えない。

最新のWindows実測はREADME末尾。保存・再読契約D13-01〜03は別の未送信案として保持し、本CLI依頼へ混ぜない。

追補（21:46再公開依頼の本セッション実測）：別セッションによる上記「分割不要」の記録は保持するが、本セッションでは依頼どおり新規CLI試験を2ファイルに分けた。既存共通CLI試験も同じ実測に含めた。製品と既存試験の変更はない。別セッションの追加試験・実測とは合算しない。

## 第13回 R13-01 採否（2026-09-17）

(b)を採用。両v2 CLIの -h/--help はstdoutにusage・stderr空・終了0となる利用案内の例外として現状維持する。採用理由とデータ検査経路との区別はHISTORY_VALIDITY_BINDING_V2_CONTRACT.mdの「第13回 R13-01 採否」を正とする。製品・試験無変更。
