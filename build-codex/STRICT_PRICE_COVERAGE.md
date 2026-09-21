# 選択価格原本と要求範囲の紐付け

2026-09-12。`strict_price_coverage_v1` は[専用入力](STRICT_INPUT.md)の実際の選択価格から[対象範囲検査](STRICT_COVERAGE.md)の行を内部生成する。別々に作った銘柄日付表を根拠に価格原本を確認済みと扱うことを避けるための、オフラインの読み取り機能である。

## 処理順

1. strict_input_v1一式を検査する。原本hash、参照、価格基準、公表、単元、eventなどに不足があればここで止める。
2. `sources.prices`で選択した実レコードを、元のdocument_idとJSON Pointerで取得する。
3. そのprice_atを入力のas_ofと同じtimezoneへ変換して日付を取り出す。取引所の営業日や時刻を推測する規則ではない。
4. 選択した文書IDごとに、実レコードからcode/date行を内部生成しhashを計算する。外部から渡した代替行は使わない。
5. 利用者が明示したrequestの期待文書集合・銘柄日付集合・期間と照合する。

検査対象は **選択された価格行だけ**。原本文書中の全履歴行や外部APIの全ページではない。`scope="selected_price_records"`を常に返す。ここでexpected_pagesに指定するのは、元strict_inputの選択価格が入っているdocument_id。単元・eventだけを含む文書や未選択文書を期待ページへ含めない。ただし、それらの文書も最初のstrict_input検査では確認する。

## 入出力

Python入口は `inspect_strict_price_coverage(bundle, request)`。bundleは既存strict_input_v1形式、requestはstrict_coverage_v1のrequest部分（start/end/expected_pages/expected_records）そのもの。既存の両形式を変更しない。

成功は `VERIFIED_OFFLINE_PRICE_COVERAGE`。不合格は`DATA_INCOMPLETE`。入力検査の理由は`INPUT_`、範囲検査の理由は`COVERAGE_`を先頭に付ける。入力不合格では範囲検査を進めず件数をすべて0とする。日時変換や派生文書のcanonical化で表現範囲を超えた場合も、`INPUT_PRICE_COVERAGE_INVALID`と0件で拒否し、本文を返さない。

銘柄や原本ID、価格本文、時刻を結果へ転記しない。成功時も`ready_for_live=false`、`current_signal=false`、`read_only=true`。書込・台帳作成・候補生成・実通信は行わない。as_ofのtimezoneは入力者が指定した基準であり、JSTを強制したり営業日と読み替えたりしない。原本の価格が期待日と一致しても、その期待日自体が適切か、価格が新鮮か、調整の経済的意味が正しいかは証明しない。

## 操作

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader\build-codex'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.strict_price_coverage_cli --input .\examples\strict_input_valid.json --request .\examples\strict_price_coverage_request.json
```

終了コード0は人工選択価格との一致、2は不足・形式不正。両ファイルを既存の安全なJSON読取で検査する。例は架空銘柄0001の価格1件であり、実取得した原本ではない。

新規API26件・CLI4件、計30件を追加。既存の専用入力・範囲検査と合わせて172 passed（1.44秒）。日時変換の年1での範囲超過、文書IDの不正Unicode、別文書/隣接行との取り違え、日時の同一時点、改変hash、入力不変、両JSONの読取失敗を確認した。サンプルの実CLIも文書1件・行1件の一致で成功した。

完成までの全体工程は[進捗表](COMPLETION_ROADMAP.md)。最新の実測は[README](README.md)に記録する。
