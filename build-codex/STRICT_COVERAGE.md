# 人工原本の対象範囲検査

2026-09-12。`strict_coverage_v1` は、呼出側が明示したページ集合と銘柄日付集合が人工原本に揃うかを検査する独立オフライン機能。strict_input_v1の入力形式・研究用処理・DBを変更しない。

## 入力契約

すべてJSON。以下に記載したキーだけを許し、余分なキーも拒否する。

| 場所 | 必須キーと値 |
|---|---|
| 最上位 | `mode="strict_coverage_v1"`、`source_origin="offline_fixture"`、`request`、`source_documents` |
| request | `start`、`end`：実在するYYYY-MM-DDでstart≦end。`expected_pages`：空でない一意のページID配列。`expected_records`：銘柄日付行の配列 |
| source_documentsの各文書 | `id`：一意の文書ID、`sha256`：payloadのcanonical SHA-256小文字64桁、`payload` |
| payload | `page_id`、`rows`：銘柄日付行の配列。一文書に一ページ |
| 銘柄日付行 | `code`、`date`：requestの両端を含む期間内の日付。価格・公表・単元の値はこの行に含めない |

IDとcodeは前後空白のない非空文字列。外部サービスの実銘柄形式や営業日は推測しない。hashはstrict_inputと同じJSON表現（UTF-8、ensure_ascii=False、sort_keys=True、区切り`,`と`:`、allow_nan=False）を対象とし、実ファイルの生バイトやサービス真正性の証明ではない。

期待ページと実ページの集合が完全一致し、期待銘柄日付と全ページ横断の銘柄日付集合が完全一致することを要求する。期待側の重複、文書ID/ページの重複、同一ページ内・ページ間の銘柄日付重複、期間外行、原本hash不一致を拒否する。

期待行が明示的な空配列で、すべての期待ページが存在して全rowsが空なら成功する。期待ページ自体を空にして無取得を成功扱いすることはできない。

**ページ別の所属や行順序は検査しない。** ある行を別の存在するページへ移してhashを更新しても、全体集合が同じなら成功する。提供元のページ分割・並び順・継続tokenの正当性を、この人工契約から推測しない。この入口単独はstrict_inputの価格原本との同一性を結合検査しない。選択価格の原本から行を導く操作は[価格原本の結合検査](STRICT_PRICE_COVERAGE.md)を使う。

## 結果と限界

`VERIFIED_OFFLINE_COVERAGE` は指定された人工集合との一致だけを示す。期待集合自体が正しいこと、市場全体・営業日・公開された全銘柄を網羅すること、価格鮮度や売買適格性を保証しない。不足・矛盾は`DATA_INCOMPLETE`と固定の`reason_codes`一覧を返す。銘柄、日付、原本文書ID、本文は返さない。

件数は、妥当な期待ページ数、一意で妥当な期待銘柄日付数、一意で妥当な観測銘柄日付数、ID/hash形式/canonical化まで検査できた文書数。文書数にはhash不一致やpayload内容不正の文書も含むため、件数だけで合格と判断しない。必ずstatusとreason_codesを使う。

成功・失敗とも `ready_for_live=false`、`current_signal=false`、`read_only=true`。DB保存・通知・予約・ネットワーク通信は行わない。

## 操作

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader\build-codex'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.strict_coverage_cli --input .\examples\strict_coverage_valid.json
```

例は架空銘柄0001の2日・2ページ。Python入口は `aitrader.strict_coverage.inspect_strict_coverage(bundle)`。CLIは終了コード0が人工集合一致、2が不足または入力不正。読取可能な不足はJSON、引数/ファイルエラーは固定日本語メッセージ。既存の安全な読取で1MiB超、重複JSONキー、非有限数値、link/reparse、読取中の変化を拒否する。

## 検証記録

新規API83件とCLI7件、計90件。既存strict_inputの52件と合わせて **142 passed（1.21秒）**。CLIは実プロセスで起動し、既存台帳を変更せず、新規ファイルを作らず、本文を出力しないことを確認した。サンプルの実CLIも2ページ/2行一致で成功した。

初回の統合実行は巨大入力をpytestのケース名にしたためWindowsの試験準備で2 errorsとなった。短いケースIDを付けて修正後、巨大入力の拒否まで検証済み。製品コードの失敗ではない。ページ所属のレビュー指摘は上記の集合単位契約と限界を明記し、未定義のページ配分規則を追加しなかった。

完成までの工程は[進捗表](COMPLETION_ROADMAP.md)、残る資料不足は[次段階メモ](STRICT_INPUT_NEXT.md)、最新全体実測は[README](README.md)。
