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

---

## Claude 単独ビルド 進捗（フェーズ2、2026-09-26 JST）

Codex との並行開発と速度を比べるため、フェーズ2（判定パケット → 二重審査 → 通知ゲート → 台帳 → 通知キュー → 日次ランナー）を **Claude Code だけ**で `build-claude/` に実装した。common/・ops/・build-codex/ は触っていない。LLM 呼び出し・LINE 送信・証券接続・発注は含まない（通知はローカル outbox の JSON、発注は人間）。

### 追加モジュール

```
aitrader/
├─ models.py   Proposal / Verdict / Limits / GateResult / LedgerView、packet_hash（共通仕様 §5 の正規化）、reserve_amount
├─ packet.py   build_proposals / render_packet（呼値丸め、翌営業日 08:59 JST 期限、UNKNOWN の明示、reference 隔離）
├─ judges.py   AI 応答の正規化（数量・価格を変えた応答や形式不正は INVALID）、MockJudge / CommandJudge / run_judges
├─ gate.py     evaluate()（合議 2 件一致・期限・契約・現物・余力・イベント・ハードリミット、不許可理由を全列挙）
├─ ledger.py   SQLite イベントソーシング台帳（通知状態・実取引状態・予約額・冪等報告・訂正・期限切れ）
├─ notify.py   通知文面と Outbox（{home}/outbox/{as_of}/{proposal_id}.json）
└─ runner.py   run_daily: signals → packets → judges → gate → ledger → outbox → receipt（{home}/receipts/{as_of}.json）
tests/         packet 50 / gate 66 / ledger 26 / judges+notify 18 / runner 6
```

### 使い方

```bash
python -m aitrader packets --strategy margin_bucket_long --as-of 2025-06-06 [--events events.json] [--lot-sizes lots.json]
python -m aitrader ledger-init --cash 3000000 [--positions positions.json]
python -m aitrader daily --strategy margin_bucket_long --as-of 2025-06-06 --events events.json            # dry-run（台帳に書かない）
python -m aitrader daily ... --execute                                                                     # 台帳に CREATED→APPROVED→SENT を記録し outbox へ
python -m aitrader daily ... --now 2025-06-09T08:30:00 --execute                                           # リハーサル用の現在時刻上書き
python -m aitrader daily ... --judge cmd --claude-cmd "claude -p" --codex-cmd "codex exec"                 # 実 CLI を審査役に（標準入力にパケット JSON）
```

events.json を渡さない銘柄は `UNKNOWN` になりゲートで保留される（安全側）。

### 検証記録

| 項目 | 結果 |
|---|---|
| 自前テスト `python -m pytest tests -q` | **166 passed**（1.5 秒） |
| 共通契約テスト（`common/tests/phase2/test_packet.py`, `test_gate.py` を build-claude に向けて実行） | **94 passed**（ハッシュは Codex 実装と同一規則） |
| 合成データ E2E（seed=42, as_of=2025-06-06, 現金 300 万円, `--now 2025-06-09T08:30`） | 候補 20 → パケット 8（2 件は予算不足で除外）→ **送信 2 件**、6 件は「本日の新規通知 2 件が上限」で遮断。台帳 reserved 434,868 円 |
| 過去日付を `--now` なしで実行 | 全件「期限切れ」で遮断（意図どおり） |

### 速度比較（Claude 単独 vs Claude/Codex 並行）

| 観点 | Claude 単独（本節） | Claude/Codex 並行（build-codex + ops） |
|---|---|---|
| フェーズ2 の着手→動く一式 | **約 9 分**（23:14→23:23 UTC。中核 3 モジュールを本体が書き、台帳・審査/通知・ランナー/CLI・境界試験をサブエージェント 4 体に並列分担） | 2026-09-08 着手、以後 R01〜R19 の往復と v031〜v041 のレビュー（約 2.5 週間、往復ごとに人間の中継） |
| コード量 | 約 2,270 行（実装 7 ファイル + テスト 5 ファイル） | build-codex/aitrader 約 60 ファイル + ops/ + 契約文書 |
| 独立性 | 実装者と試験者を別エージェントに分けた（試験側はバグ 0 件・注意点 1 件を報告し、注意点はゲートに反映） | Codex が敵対テストを先に書き Claude が実装する分担 |
| 範囲の差 | 通知ゲートまでの最小構成。CSV 照合（`import_csv_fills`）、任意時点の残高再計算、ランナー復旧、証跡バンドルは未実装 | ランナー復旧・証跡バンドル・厳格入力検証・運用状況契約まで到達 |

解釈: 速度差の大部分は「人間を介した往復の回数」と「契約文書の往復」から来ている。単独ビルドは契約を自分で決めて即実装できる代わりに、第三者の敵対的検証が弱い（今回はサブエージェントで代替）。同じ範囲まで到達させる場合の差は未測定。
