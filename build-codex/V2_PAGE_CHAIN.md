# 人工V2応答のページ連鎖検査

2026-09-12。公開[ページング仕様](https://jpx-jquants.com/ja/spec/pagination)を確認し、保存した人工応答を読取検査する `v2_page_chain_fixture_v1` を実装した。公式仕様は、検索条件を変えず後続キーを渡すこと、キー不在で終了することを示す。一方、途中更新時の全体一貫性は保証していない。詳しい再確認記録は[JQUANTS_PUBLIC_DOC_UPDATE_20260912.md](JQUANTS_PUBLIC_DOC_UPDATE_20260912.md)。

## 入力

最上位のキーはmode、dataset、pagesだけ。modeは`v2_page_chain_fixture_v1`。datasetは既存V2形状検査のprices/margin/master/topix/calendar。pagesは空でない配列で、記録された要求順に並べる。

各ページのキーはquery、request_token、responseだけ。

- query：JSON object。pagination_keyを含めず、全ページでcanonical JSONが一致する。キー順の違いは許す。要求の業務上の妥当性やendpointは検査しない。
- request_token：初回はnull、後続は直前応答のpagination_keyと完全一致する文字列。
- response：既存inspect_v2_pageが受け付ける形状。後続がある場合のpagination_keyは非空文字列で、返却キーの再使用を許さない。最後はpagination_key自体が存在しないこと。

中間で終了した後にページが続く、最終に後続キーが残る、要求キーが飛ぶ、同じ後続キーへ戻る、検索条件が変わる、応答形状が不正な場合は拒否する。明示null/空文字のpagination_keyを終了扱いしない。これは新しい連鎖検査の限定規則であり、既存単ページ検査のnull/空文字互換を変えない。

## 結果

成功は`VERIFIED_OFFLINE_PAGE_CHAIN`、不足・矛盾は`DATA_INCOMPLETE`。page_countは入力配列の長さ、row_countは単ページ形状検査に成功した応答の行数合計。失敗時の部分件数は確認済み全件数ではない。

`fixture_only=true`、`consistent_snapshot_verified=false`、`ready_for_live=false`、`current_signal=false`、`read_only=true`を固定する。結果はdataset、件数、固定理由だけで、query/継続キー/行本文を返さない。

これは記録されたJSON内の繋がりの検査。実HTTP要求・提供元原本との一致、全市場の網羅性、重複業務行、価格鮮度、訂正履歴、全ページの同一時点性は証明しない。ネットワーク・DB・候補・通知への接続は行わない。

## 操作と検証

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader\build-codex'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.jquants_v2_page_chain_cli --input .\examples\v2_page_chain_valid.json
```

例は仮のcalendar応答2ページ。Python入口は`inspect_v2_page_chain(bundle)`。CLIは通常の限定JSON読取を使い、成功0、不足/エラー2。読取不能時には固定日本語エラーを返す。

新規API31件・CLI5件、計36件。既存V2形状/CLI162件と合わせて198 passed（0.92秒）。サンプル実CLIも2ページ/2行で成功した。実際の暦やAPI取得の検証ではない。全体実測は[README](README.md)。
