# strict_input_v1：人工原本付き入力の専用検査

ユーザーが選択した新しい専用モードの第一段階。既存のsynthetic・legacy研究・provenance_fixtureを変更せず、メモリ内の入力とその人工原本の対応を検査する。DB作成・取込・候補生成・審査・通知・注文・ネットワーク通信は行わない。

成功は`VERIFIED_OFFLINE_INPUT`、不足・矛盾は`DATA_INCOMPLETE`。どちらも`ready_for_live=false`、`current_signal=false`、`read_only=true`。成功は本番の適格性や原本の外部真正性の証明ではない。

## 入力形式

[完全な人工入力例](examples/strict_input_valid.json)を参照。サンプルの銘柄・価格・公表時刻は作業用の架空値である。

| 最上位キー | 内容 |
|---|---|
| mode | strict_input_v1固定 |
| source_origin | offline_fixture固定。実取得資料として受理しない |
| as_of | timezone付きISO日時。未来判定の明示的な基準 |
| expected_codes | 空でない重複なしの銘柄文字列リスト |
| source_documents | id・sha256・payloadを持つ人工原本配列 |
| sources | prices/publications/lots/eventsの4組。それぞれexpected_codesと同じ銘柄集合 |

各sourcesの値は`document_id`と`pointer`だけを持つ。pointerは原本payload内の行を指すJSON Pointer（例 `/prices/0001`）。別途コピーした値を信じるのではなく、その原本内の行自体を検査する。参照の重複、存在しない原本・行、行の銘柄違いは拒否する。

原本hashはpayloadをUTF-8、キー昇順、区切り文字`,`と`:`、ensure_ascii=false、allow_nan=falseでcanonical JSON化したSHA-256の小文字hex。元ファイルの生バイトhashではなく、外部署名・取得者証明でもない。payloadとhashを同時に整合させて変更した入力の出所を認証する仕組みではない。

## 原本内の4種類の行

| 種類 | 行のキー | 検査 |
|---|---|---|
| prices | code, price_at, selected_basis, raw, adjusted, adjustment_contract | price_atはawareでas_of以下。rawはopen/high/low/close/volumeが全有の有限数値。RAWはadjusted/contractともnull。ADJUSTEDはrawとadjusted双方が全有で、非空の明示contractを要求 |
| publications | code, publication_at, estimated | awareのpublication_atがas_of以下、estimatedが厳密にfalse |
| lots | code, lot_size | boolを除く正の整数。欠損を100にしない |
| events | code, next_earnings_at, margin_regulated | 決算日時はawareまたは明示null、規制は厳密bool。UNKNOWNや欠損は拒否 |

行の余分なキーも拒否する。部分的な調整OHLCVと、RAW指定なのに調整値を併記した混在は拒否する。ADJUSTEDのcontractは人工入力で明示した識別子であり、外部調整規則を検証済みという印ではない。調整係数の計算や価格と出来高の経済的整合を推測しない。turnover・factorは本段階の行に含めておらず、その意味・整合の検査は未実装。

決算nullは人工原本の明示的な「なし」の記録であり、実際に決算がない証明ではない。銘柄集合の網羅性も呼出側のexpected_codesとの一致であり、市場全体・全ページ・営業日の網羅性ではない。価格の鮮度、単元の有効期間、実公表時刻の出所証明、取込run/訂正履歴の永続化は次段階。

## 実行

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader\build-codex'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.strict_input_cli --input .\examples\strict_input_valid.json
```

CLIは既存の安全なJSON読取を使用する。1MiB超・link/reparse・読取中の変化・重複キー・非有限JSONを拒否し、エラー時に原本本文やpathを標準エラーへ転記しない。既存DBを開かない。終了コード0は人工入力検査成功、2は不足または入力エラー。構造を読み取れた不足はJSONのreason_codes、読取不能や引数不正は固定エラーで返す。

Python入口は`aitrader.strict_input.inspect_strict_input(bundle)`。戻り値は件数・状態・固定理由コードなどのメタデータだけで、銘柄や価格本文を返さない。入力を変更しない。

## 検証

追加レビューでJSON Pointerの境界を修正した。空文字は原本文書のルートを指す。配列添字はASCII数字の正規表現（0または先頭ゼロのない整数）だけを許し、全角/アラビア数字、符号、範囲外を拒否する。オブジェクトのUnicodeキー・空キー・エスケープしたキーは保持する。追加12件を含む専用モードAPI/CLIは52件成功（0.54秒）。この修正後に全体試験を再実行した結果ではなく、前回全体2270件の記録と区別する。

残工程の証拠と試験境界は[次段階メモ](STRICT_INPUT_NEXT.md)を参照。

新規API36件・CLI4件、計40件が主担当の統合実行で0.54秒で成功。原本hash/参照/銘柄集合、調整欠損・基準混在、未来価格/公表時刻、推定公表日、単元の型、UNKNOWN、入力不変、秘密値非表示、read_onlyと非承認flagを検証した。大きな整数はJSONで表せるがfloatに変換できないケースを使用。全体実測は[README](README.md)の最新節を参照。

次の結合モードでは[結合案 F-01](HISTORY_VALIDITY_BINDING_PLAN.md)の共通コード/ID書式を入力・履歴へ同時適用する設計。現在のstrict_input_v1の受入規則は変更していない。
