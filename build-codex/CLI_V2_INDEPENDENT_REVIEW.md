# Claude 独立確認 第13回: v2 CLI 2 本の読取境界・固定 stderr・終了コード

2026-09-17。実施者 Claude（Claude Code、Windows 実機、モデルは Claude Fable 5.1。サブエージェント 0 体）。依頼は[Claude引き渡しプロンプト](Claude引き渡しプロンプト.md)の B（更新 2026-09-17 21:52 JST、依頼 hash `b2a500f8…`）。

読んだのは B の指定どおり `evidence_history_fixture_v2_cli.py`・`history_validity_binding_v2_cli.py` の全文、`packet_cli._mapping`、`strict_input_cli._Parser`、CLI 試験 4 本（`test_history_v2_clis.py`、`test_evidence_history_fixture_v2_cli.py`、`test_history_validity_binding_v2_cli.py`、`test_history_v2_cli_contract_additions.py`）、契約書の CLI 節、契約案の採否追補。製品・既存試験・例・共通仕様は変更していない。追加は新規試験 1 ファイルのみ。

## 結論

**実装バグ 0 件。読取境界・固定 stderr・終了コードは契約どおり。** B の 5 ファイル sha256 は当方の実測と全件一致。指摘は 1 件（R13-01、低、v1 と共通の契約の穴）で、修正するかは Codex の判断に委ねる。

| 確認点 | 結果 |
|---|---|
| `_mapping` / `_Parser` の同一オブジェクト流用 | **一致。** 両 CLI とも `from .packet_cli import _mapping`、`from .strict_input_cli import _Parser`。v1 CLI と本文が同型で、差は import 先と description 文字列だけ |
| 1MiB 超過で API 非呼出 | **一致。** `test_history_v2_clis.py` が両 CLI でちょうど通過／+1 バイト拒否を固定。境界は `_mapping` 内（段 1 より前） |
| 固定 stderr 1 文・stdout 空 | **一致。** 読取・引数・例外のすべてで同じ 1 文。文言は v1 と同一（契約案の別文言は不採用で妥当。各 CLI 内で 1 種類であることが要件） |
| 終了コード 0/2 | **一致。** 成功 0、DATA_INCOMPLETE 2（stdout に JSON）、読取・引数・例外 2（stdout 空）。1 は出ない |
| 例外の詳細・入力パスの非漏出 | **一致。** `test_history_v2_clis.py` が固定 |

## 追加した反証 30 件（`tests/test_history_v2_cli_claude_contract.py`）

既存 4 ファイルが触れていない点だけ。両 CLI にパラメータ化。

- UTF-8 BOM 付き入力が受理され成功（`utf-8-sig` 経路）
- 空ファイル・空白のみ・非 UTF-8（Latin-1 / UTF-16 BOM）・最上位が `null`/文字列/数値 → API 非呼出・固定 stderr・終了 2（7 種 × 2）
- ディレクトリを `--input` に渡す → 固定 stderr・API 非呼出
- 引数エラー 4 種（未知オプション・値なし `--input`・未知 `--output`・位置引数）→ 固定 stderr、引数の値を stderr に echo しない
- 成功行が `sort_keys` の JSON 1 行ちょうどで、入力ファイルの内容・サイズ・mtime が不変
- 結合 v2 の E2（時点差）を実プロセスで実行し、stdout が期待値 JSON と完全一致・終了 2・stderr 空
- 期間履歴 v1 束を行履歴 v2 CLI に渡すと固定 stderr ではなく API の `INVALID_MODE`

実測: `30 passed / 1.04 秒`。Windows 11、Codex 同梱 CPython 3.12.14、`-p no:cacheprovider`、basetemp は Dropbox 外。既存 4 ファイルと全体試験は再実行していない（再実測 0 点）。

## 指摘

### R13-01（低・契約の穴、v1 と共通）`--help` / `-h` が usage を stdout に出し終了 0 になる

再現条件: `python -m aitrader.history_validity_binding_v2_cli --help`（`-h` も同じ。行履歴 v2 CLI も同じ）。
期待（契約）: stdout は API 結果の JSON 1 行または空。終了 0 は成功時のみ。
実測: usage 文（`usage: history_validity_binding_v2_cli.py [-h] --input INPUT` ＋ description）が stdout に出て終了 0、stderr 空。
原因: `_Parser.error` は ValueError にしているが、argparse の help は `error` を通らず `parser.exit()`（`SystemExit(0)`）で抜ける。`SystemExit` は `Exception` の派生ではないので `except Exception` に捕まらない。既存 v1 の 6 CLI すべてで同じ挙動（`_Parser` 共用）。
重要度: 低。データを処理する経路ではなく、安全読取・API 非呼出は保たれる。ただし「stdout は JSON か空」「0 は成功のみ」を機械的に信頼する呼出側は誤認し得る。
修正案（採否は Codex）: (a) v2 CLI だけ `_Parser(add_help=False, …)` にして `--help` を未知引数扱い（固定 stderr・終了 2）にする。v1 と挙動が分かれるが v1 は不変。(b) 契約に「`-h/--help` は usage を stdout に出して 0 で終わる例外」と明記して現状維持。(c) `_Parser` 側で `exit` を上書きして全 CLI を揃える（v1 の変更になるため今回は非推奨）。私の推奨は (b)。help の出力は argparse の既定で、呼出側が `--input` を必ず渡す運用なら実害がない。
新規試験には固定していない（契約が決まってから固定する）。

### 記録のみ（指摘ではない）

- argparse の省略形により `--inp <path>` が `--input` として受理される。`--input` を 2 回渡すと後勝ち。いずれも v1 と共通で、契約「`--input` のみ」の字義から外れるが、受理される入力は同じファイル読取に限られる。

## sha256（レビュー時点。B の記載と一致）

```text
8c3ca4225bb496749ccc932f80774684fa1e7b8edbe6c486222bae622016c30e  aitrader/evidence_history_fixture_v2_cli.py
b955d509427383ce007003909792cf9622be34be15f161772456b887a02a0edf  aitrader/history_validity_binding_v2_cli.py
4a246ce1fb0a1a342571a3a6003896e40d57616d729376e7b19fa2d63f2f96d7  tests/test_evidence_history_fixture_v2_cli.py
31e1a7b9854745649fce53bc95da218d4ac74eddc677e08461a92919e24c2559  tests/test_history_validity_binding_v2_cli.py
84da927cb7a2ccb197b58c5faf73c9e4a064cc2548f06d9618233619da77b4d0  tests/test_history_v2_clis.py
07745c9c98a0d36a1464f97ea43de8d07e5d664b4d309525b44e13630e21690a  tests/test_history_v2_cli_contract_additions.py
fbf162145b3608df08d4644a946fad472b4c61c6de1328336f2cf8a663292c12  tests/test_history_v2_cli_claude_contract.py（新規, 6122 bytes, 2026-09-17T21:54:48+09:00）
```

## 範囲外

保存契約 D13-01〜03、API 全件確認、全体試験、v1 CLI の変更。`--help` 以外の未知経路は探索していない（読取中のファイル変化検出は `_mapping` の v1 試験に依拠）。

終了時刻: 2026-09-17 21:56 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、第13回 CLI 独立確認（バグ 0、反証 30 件全通過）の受領と、R13-01（--help が stdout・終了 0 になる契約の穴）の採否判断を行ってください。
```
