# 個人向け J-Quants 公開資料レビュー

**2026-09-12の追記:** 株価・週末信用残・ページングの本文と、9月28日提供開始予定の信用残新仕様を追加確認した。最新差分は[再確認記録](JQUANTS_PUBLIC_DOC_UPDATE_20260912.md)。以下の取得失敗・未確認は前日時点の記録であり、解消した範囲は新記録を優先する。

閲覧日: 2026-09-11  
対象: 個人向け J-Quants の公開公式資料と公式 Python SDK。J-Quants Pro の資料は根拠に使用しない。

## 結論

現行実装 `aitrader/jquants.py` は、個人向け V2 の公開仕様に対応済みとはいえない。現在の実装は `idToken`、`/listed/info`、`/markets/weekly_margin_interest`、長い列名を前提とする。一方、個人向け V2 の移行資料と公式 SDK は API key、`/v2/equities/master`、`/v2/markets/margin-interest`、短縮列名を示している。

この確認は実データ入力契約の採用を意味しない。認証付き API は実行しておらず、実データも取得していない。専用 home の `provenance_fixture` 試作も個人向け V2 adapter ではなく、実運用適格性を証明しない。

## 公開資料で確認できた差分

| 項目 | 個人向け V2 の公開資料・公式 SDK | 現行 legacy 実装 |
|---|---|---|
| 認証 | API key | `idToken` を取得して bearer token として使用 |
| 上場銘柄 | `/v2/equities/master` | `/listed/info` |
| 信用残 | `/v2/markets/margin-interest` | `/markets/weekly_margin_interest` |
| 上場銘柄の列 | `Date, Code, CoName, CoNameEn, S17, S17Nm, S33, S33Nm, ScaleCat, Mkt, MktNm, Mrgn, MrgnNm, ProdCat` | `CompanyName`, `Sector33Code`, `ListedDate` などを参照 |
| 信用残の列 | `Date, Code, ShrtVol, LongVol, ShrtNegVol, LongNegVol, ShrtStdVol, LongStdVol, IssType` | `ShortMarginTradeVolume`, `LongMarginTradeVolume`, `PublishedDate` / `PublishDate` などを参照 |
| 公表日の扱い | 今回確認した V2 固定列に公表日列はない。更新予定は週次、第2営業日16:30頃と案内される | 公表日列がなければ `Date + 4日` を推定値として保存 |

したがって、現行の `PublishedDate` / `PublishDate` fallback や `ListedDate` を、個人向け V2 の取得可能項目として転用しない。`Date + 4日` も個別行の正確な公表完了日時として扱わない。

## 未確認事項

- 個別仕様ページの本文は今回の閲覧環境では 403 となったため、各列の厳密な意味は確認できていない。列一覧は個人向け公式 SDK の V2 定数を根拠とする。
- 上場銘柄マスターの `Date` を上場日と解釈できる根拠は確認できていない。
- `ListedDate` と売買単位・単元株数を、現在の個人向け公開仕様または公式 SDKから取得できることは確認できていない。
- 実際の更新完了を行単位で証明する API、version、ETag の有無は、今回確認した資料だけでは未確認である。
- 認証、ページング、期間制約、契約プラン別の取得範囲、実レスポンスは未検証である。

## 参照した個人向け公式資料

- [J-Quants API 仕様一覧](https://jpx-jquants.com/ja/spec/)
- [V1 から V2 への移行](https://jpx-jquants.com/ja/spec/migration-v1-v2)
- [データ更新予定](https://jpx-jquants.com/ja/spec/data-update)
- [個人向け公式 Python SDK](https://github.com/J-Quants/jquants-api-client-python)
- [V2 equities 実装](https://github.com/J-Quants/jquants-api-client-python/blob/main/jquantsapi/apis/v2/equities.py)
- [V2 markets 実装](https://github.com/J-Quants/jquants-api-client-python/blob/main/jquantsapi/apis/v2/markets.py)
- [V2 固定列定義](https://github.com/J-Quants/jquants-api-client-python/blob/main/jquantsapi/constants.py)

J-Quants Pro のページや列定義は、このレビューの判断根拠に含めていない。

## 株価・指数・営業日の差分と次の限定工程

| 対象 | V2のパス・主な列 | ローカルで未確定の判断 |
|---|---|---|
| 株価 | `/v2/equities/bars/daily`、`O/H/L/C/Vo/Va/AdjFactor/AdjO/AdjH/AdjL/AdjC/AdjVo` | 調整列の部分欠損、出来高・代金の基準、訂正を取り込む契約 |
| TOPIX | `/v2/indices/bars/daily/topix`、`Date/O/H/L/C` | 取得対象期間の充足、価格日の整合 |
| 営業日 | `/v2/markets/calendar`、`Date/HolDiv` | 商品ごとの営業日区分の採用と期間の充足 |

V2の認証ヘッダーは`x-api-key`、応答は原則`data`配列と`pagination_key`。根拠は上記[移行資料](https://jpx-jquants.com/ja/spec/migration-v1-v2)、[TOPIX仕様](https://jpx-jquants.com/ja/spec/idx-bars-daily-topix)、[営業日仕様](https://jpx-jquants.com/ja/spec/mkt-cal)、公式SDKの固定列定義。公開資料で形が分かることと、実サービスの接続確認が済むことを区別する。

次は独立した純粋関数で仮レスポンスの形だけを検査する。通信・キー読取・DB作成・旧adapter置換はしない。列の型を通過しても公表時点、経済的整合、期間の充足、実シグナルの適格性を証明しない。公式SDKのmain参照は閲覧時点の確認であり、固定リビジョンの永続保証ではない。

独立V2形状検査は実装済み。保存した仮JSONから使う[V2検査CLI](V2_FIXTURE_INSPECTION.md)も追加した。取込・候補生成・DBへ自動接続せず、検査通過時もready_for_live=falseを維持する。
