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
├─ csvfills.py 証券会社 CSV（UTF-8/CP932、日本語ヘッダ）→ CsvRow
└─ runner.py   run_daily: signals → packets → judges → gate → ledger → outbox → receipt（{home}/receipts/{as_of}.json）
tests/         packet 50 / gate 66 / ledger 26+11+23（CSV 照合と敵対試験）/ judges+notify 18 / runner 6 / CLI 8
```

### 使い方

```bash
python -m aitrader packets --strategy margin_bucket_long --as-of 2025-06-06 [--events events.json] [--lot-sizes lots.json]
python -m aitrader ledger-init --cash 3000000 [--positions positions.json]
python -m aitrader daily --strategy margin_bucket_long --as-of 2025-06-06 --events events.json            # dry-run（台帳に書かない）
python -m aitrader daily ... --execute                                                                     # 台帳に CREATED→APPROVED→SENT を記録し outbox へ
python -m aitrader daily ... --now 2025-06-09T08:30:00 --execute                                           # リハーサル用の現在時刻上書き
python -m aitrader daily ... --judge cmd --claude-cmd "claude -p" --codex-cmd "codex exec"                 # 実 CLI を審査役に（標準入力にパケット JSON）
python -m aitrader ledger-report --proposal-id A1-... --kind FILLED --qty 100 --price 2020 --broker-order-id B1  # LINE 相当の手動報告
python -m aitrader ledger-import-csv --file fills.csv                                                      # 証券会社 CSV の照合取込（曖昧な行は pending）
python -m aitrader ledger-status                                                                           # 現金・予約・保有・未確認・pending
python -m aitrader ledger-resolve --event-id E --apply --proposal-id P   |   --discard                     # pending を人間が解決
```

events.json を渡さない銘柄は `UNKNOWN` になりゲートで保留される（安全側）。

### 検証記録

| 項目 | 結果 |
|---|---|
| 自前テスト `python -m pytest tests -q` | **208 passed**（2.1 秒。CSV 照合追加後） |
| 共通契約テスト（`common/tests/phase2/` の packet / gate / ledger を build-claude に向けて実行） | **124 passed**（packet 34・gate 60・ledger 30。ハッシュは Codex 実装と同一規則） |
| 合成データ E2E（seed=42, as_of=2025-06-06, 現金 300 万円, `--now 2025-06-09T08:30`） | 候補 20 → パケット 8（2 件は予算不足で除外）→ **送信 2 件**、6 件は「本日の新規通知 2 件が上限」で遮断。台帳 reserved 434,868 円 |
| 過去日付を `--now` なしで実行 | 全件「期限切れ」で遮断（意図どおり） |

### 速度比較（Claude 単独 vs Claude/Codex 並行）

| 観点 | Claude 単独（本節） | Claude/Codex 並行（build-codex + ops） |
|---|---|---|
| フェーズ2 の着手→動く一式 | **約 9 分**（23:14→23:23 UTC。中核 3 モジュールを本体が書き、台帳・審査/通知・ランナー/CLI・境界試験をサブエージェント 4 体に並列分担） | 2026-09-08 着手、以後 R01〜R19 の往復と v031〜v041 のレビュー（約 2.5 週間、往復ごとに人間の中継） |
| コード量 | 約 2,270 行（実装 7 ファイル + テスト 5 ファイル） | build-codex/aitrader 約 60 ファイル + ops/ + 契約文書 |
| 独立性 | 実装者と試験者を別エージェントに分けた（試験側はバグ 0 件・注意点 1 件を報告し、注意点はゲートに反映） | Codex が敵対テストを先に書き Claude が実装する分担 |
| 範囲の差 | 通知ゲート + 台帳 + CSV 照合（`import_csv_fills` / `pending_rows` / `resolve_pending` / `balance_at`）。ランナー復旧、証跡バンドルは未実装 | ランナー復旧・証跡バンドル・厳格入力検証・運用状況契約まで到達 |

解釈: 速度差の大部分は「人間を介した往復の回数」と「契約文書の往復」から来ている。単独ビルドは契約を自分で決めて即実装できる代わりに、第三者の敵対的検証が弱い（今回はサブエージェントで代替）。同じ範囲まで到達させる場合の差は未測定。

### 追記: CSV 照合（2026-09-27 JST、同じやり方で約 4 分）

台帳に CSV 照合を追加した（実装: opus、敵対試験: sonnet、CLI: sonnet の 3 体並列。23:11→23:15 UTC）。

| 論点 | 本実装 |
|---|---|
| 冪等 | 同じ `event_id` は 2 回目以降 skipped。ERROR / DISCARDED になった id も再取込されない（修正後は新しい id で） |
| LINE と CSV の重複 | 同一 code+side の既存約定と「同一証券ID かつ 同数量・同単価（時刻があれば時刻も）」なら重複。到着順が逆でも 1 回だけ計上 |
| 曖昧一致 | 証券IDが無く既存約定と同数量・同単価の行は pending（自動計上しない）。`pending_rows()` に理由付きで残り、`resolve_pending(APPLY/DISCARD)` で人間が解決 |
| 紐付け | 明示 `proposal_id` は code+side が一致しなければ pending。省略時は同 code+side の未決済通知がちょうど 1 件のときだけ紐付け |
| 期限切れ通知への CSV | 適用する（EXPIRED ≠ 取消） |
| `balance_at(at)` | 各保存時点の残高要約（cash / reserved / positions）を ledger_events に記録し、`at` 以前の最新要約を返す。約定ロジックの再実行はしない |
| 敵対試験 | 23 件（REJECTED 通知への CSV、保有なし SELL、qty 0/負、naive datetime、同一呼び出し内の重複 id、残数量超過、同一証券IDの別銘柄、pending→LINE→APPLY の二重計上防止、余力負）。実装バグ 0 件 |

### 他 PC での起動（2026-09-28 追記）

前提: Python 3.11 以上と git。clone は **Dropbox の外**に置く（`../Claude_Opus引き継ぎ.md` の PC 移行メモと同じ）。ネットワークは pip だけ使う（J-Quants・LLM・LINE・証券には接続しない）。

```powershell
git clone https://github.com/ShinNori/claude_ai-trader.git D:\work\ai-trader
cd D:\work\ai-trader
git checkout claude/auto-investment-system-qtxzkv
cd build-claude
powershell -ExecutionPolicy Bypass -File setup.ps1                     # venv 作成 → pip → テスト 208 件 → smoke.py
# 実行時データの置き場を変える: -Home D:\ai-trader-home   テストを飛ばす: -SkipTests   初期現金: -Cash 3000000
```

```bash
bash setup.sh [--home ~/ai-trader-home] [--cash 3000000] [--skip-tests]   # Linux / macOS
```

`smoke.py` は合成データで init-db → load-synthetic → packets → events.json 生成 → ledger-init → daily（dry-run）→ daily --execute（リハーサル時刻 2025-06-09 08:30）→ ledger-status を通し、最後に `OK` を出す。既存の DB・台帳があれば作り直さない。Linux コンテナの新規 venv で 35 秒（テスト除く）。

| 置き場 | 内容 |
|---|---|
| `AI_TRADER_HOME`（既定 Windows: `%LOCALAPPDATA%\ai-trader-claude`、Linux: `~/.ai-trader-claude`） | `market.duckdb`、`ledger.sqlite`、`outbox/`、`receipts/`、`events.json`。Git 管理外、Dropbox の外 |
| `build-claude/.env` | J-Quants トークン（任意）。`.env.example` を setup がコピーする |

Windows で確認済みの依存: 標準ライブラリの sqlite3 / subprocess、pandas / duckdb の wheel。CP932 の CSV は `ledger-import-csv` が自動判別する。テストの外部コマンドは `sys.executable` を使うので `sleep` 等の Unix コマンドに依存しない。
