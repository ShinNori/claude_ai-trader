# ai-trader — Claude ビルド（フェーズ0〜1）

日本株 日次シグナル・バックテスト基盤。仕様は `../common/共通仕様_フェーズ1.md` に従う。

## セットアップ

```bash
cd build-claude
python -m venv .venv && .venv\Scripts\activate     # Windows（Linux は source .venv/bin/activate）
pip install -r requirements.txt
copy .env.example .env                              # J-Quants トークンがあれば記入（無くても合成データで動く）
set AI_TRADER_HOME=C:\Users\<you>\.ai-trader        # 実行時データの置き場（Dropbox の外）。未指定なら ~/.ai-trader
```

## 使い方

```bash
python -m aitrader init-db
python -m aitrader load-synthetic --seed 42
python -m aitrader signals --strategy margin_bucket_long --as-of 2025-06-06
python -m aitrader backtest --strategy margin_bucket_long --from 2016-01-04 --to 2026-08-31 --out results/
python -m aitrader report --results results/margin_bucket_long_v1/
# J-Quants 実データ（.env にトークンがある場合）
python -m aitrader fetch --from 2024-01-01 --to 2026-08-31
```

受入テスト: `python -m pytest ../common/tests -q`

## 構成

```
aitrader/
├─ __main__.py     CLI
├─ api.py          init_db / load_synthetic / run_signals / run_backtest（受入テスト用の公開API）
├─ db.py           DuckDB スキーマ・接続（AI_TRADER_HOME/market.duckdb）
├─ jquants.py      J-Quants クライアントと取り込み（列名の揺れは _norm_* で吸収）
├─ strategies/
│   ├─ base.py               Candidate / Strategy インターフェース
│   ├─ margin_bucket_long.py A1 信用買残減少バケット・ロング
│   └─ __init__.py           レジストリ（config/strategies.yaml のパラメータで上書き）
├─ backtest.py     バックテスト（共通仕様 §5）
└─ report.py       report.html（外部ライブラリ不要の単一HTML）
config/strategies.yaml   戦略パラメータと状態
results/<strategy>_<version>/  summary.json / report.html（trades.csv, equity.csv は .gitignore）
```

戦略を1本追加するとき触るファイル: `strategies/<new>.py`（新規）、`strategies/__init__.py`（1行）、`config/strategies.yaml`（パラメータ）。

## 共通仕様の解釈（Codex ビルドとの数値差を判定するために明記）

| 論点 | 本ビルドの解釈 |
|---|---|
| シグナル日 | 各 ISO 週の最初の営業日（通常は月曜、休場なら翌営業日）。`as_of` はその直前の営業日 |
| 「利用可能資金」 | 寄り付き時点の現金（当日終値で決済する建玉の代金は含めない）。候補 N 件に `1/N` ずつ配分。指値未約定の分は現金に残り次週に回る |
| 日中の処理順 | 寄り付きの買付 → 終値の売却 → 時価評価。**v0.1 では売却を先に処理し当日売却代金を朝の買付に使う誤りがあり、Codex ビルドの指摘で修正（2026-09-08）** |
| 同一銘柄の重複 | 保有中の銘柄が再び候補になった場合はエントリーしない（`skipped_duplicate` に集計。`skipped_by_limit` とは別） |
| イグジット | エントリー日から `holding_days` 営業日後の終値 × (1−0.001)。期間末に残った建玉は最終日終値で強制決済し、取引として集計。最終日がシグナル日なら新規建ても行い同日終値で決済（Codex と同じ扱い） |
| 営業日 | `prices_daily` に存在する日付を営業日とみなす（`calendar` は参照用） |
| 株数 | 金額ベース（端株無視）。`shares = qty_yen / 約定価格` |
| ユニバースの「直近60営業日」 | `as_of` 以前の60営業日ぶんの `turnover` が揃っている銘柄のみ（データ不足は除外） |
| chg4w | `publish_date <= as_of` の週次残高のうち最新と、その4本前（＝4週前）の比 |
| 5分位 | ユニバース内で chg4w 昇順に並べ、先頭 `len//5` 件を最下位バケットとし、最大20件 |
| ベンチマーク | `index_daily.TOPIX` を初期資金で正規化。合成データでは等金額指数 |
| シャープ | 日次リターンの平均/標準偏差 × √252、無リスク金利 0 |
| profit_factor | 損失ゼロのときは `null` |
| data_mode | `listed.name` が「合成」で始まる行があれば `synthetic`、無ければ `jquants` |

## 合成データ（seed=42, 2016-01-04〜2026-08-31）の結果

| 指標 | 値 |
|---|---|
| 最終資産 | 36,037,323.83 円（初期 10,000,000 円）— **Codex ビルドと1円単位で一致** |
| CAGR | 12.7% |
| シャープ | 1.33 |
| 最大DD | −17.4% |
| 取引回数 | 4,448（指値未約定 1,135、重複スキップ 5,317） |
| TOPIX（合成） | CAGR 7.6% / 最大DD −43.4% |
| TOPIX 相関 | 0.93 |
| 実行時間 | 約 26 秒（10年・300銘柄・Linux コンテナ）。Codex ビルドは約 8.6 秒（Windows）で、速度は Codex 側が優位 |

※ 合成データには「買残が減った銘柄がその後上がりやすい」効果を**意図的に**入れてあるので、この成績は基盤の動作確認用。実市場での有効性を示すものではない。

受入テスト: `8 passed`（2026-09-08、処理順修正後に再実行）

## 既知の制限・次フェーズ

- J-Quants の実データ取り込みは未検証（トークン未取得）。列名は v1 ドキュメントの想定。`fetch` 実行後に `signals` が動けば OK
- ウォークフォワード・感度分析・年別集計以外の検証はフェーズ1の範囲外（戦略カタログ §1.3〜1.4）
- フェーズ2: 判定パケット生成 → Claude/Codex 審査 → 合議 → LINE 通知（設計書 5〜7章、Codex の連携仕様案を反映）
