# AGENTS.md — Codex への引き継ぎ（ai-trader）

## 現在のフェーズ: **フェーズ2 後半**（2026-09-08 更新）

毎回の依頼内容は **`Codex引き渡しプロンプト.md` の「B. 今回の依頼」** に書いてある。まずそれを読むこと。
フェーズ2 前半（受入テスト先行作成・判定パケット）は完了。Claude 側の ops/（台帳・通知ゲート・ハードリミット）も
あなたの受入テスト 124 件を全通過して完了した（`ops/Claude対応結果.md`）。

役割分担（固定）:
- Codex: build-codex/（主系）、common/tests/phase2/ の受入テスト、レビュー・反証
- Claude: ops/、build-claude/（照合用副系）
- common/ の仕様・合成データは両者とも編集しない。不備は common/ISSUES.md に追記

決定済みの前提（設計書 12章）: 現物限定 / 初期リミットは 5銘柄・25%・1日2件 / 買い・売りとも寄付指値 /
損切りは証券側の逆指値 / 16:30 照合→翌 06:50 未解決で新規停止 / 台帳は `${AI_TRADER_HOME}/ledger.sqlite`。

---

## （参考）フェーズ1 の指示（完了済み）

あなた（Codex）には、Claude と**同じ仕様で同じものを別々に実装して比較する**役割をお願いします。
実装先は `build-codex/` のみ。他のフォルダは読むだけで、編集しないでください。

## まず読む文書（この順）

1. `../AI合議型自動売買システム_設計書.md`（v0.2）— 全体像。執行は「LINE通知→楽天証券Webで手動発注」。フェーズ1では通知も審査も作らない
2. `../戦略カタログ_複数ロジック検証計画.md`（v0.1）— 戦略一覧・共通基盤・採用基準。§1.6 の「対応時間」も前提
3. `common/共通仕様_フェーズ1.md`（v1.0）— **今回の実装仕様。これに厳密に従う**
4. `common/synth_data.py` — 両者共通の合成データ（変更禁止）
5. `common/tests/test_acceptance.py` — 受入テスト（変更禁止）
6. `../Claude_Codex連携仕様案.md` — あなたが以前作成した合議仕様。フェーズ2で使うため今回は参照のみ

`../_old_v0.1_*.md` は旧版なので読まなくてよい。

## 決定済み事項（設計書の未決事項に対する回答）

- 対象: 日本株、日次（スイング）。スキャルピング・日中は対象外
- 判定: Claude Code CLI ＋ Codex CLI のダブルサイン（フェーズ2）
- 執行: LINE Messaging API で通知 → 楽天証券Web で手動発注（07:00 通知、7:40 以降に発注）
- コード置き場: `999_投資関係/ai-trader/`（Dropbox）。**実行時の DB・ログ・結果本体は Dropbox 外**（`AI_TRADER_HOME`、既定 `~/.ai-trader/`）
- J-Quants: **未登録**。合成データモードで開発・テストし、実データ取り込みはトークンがあれば動く形にしておく
- 戦略 A1 のパラメータは共通仕様 §4.1 の既定値で固定（最適化禁止）
- あなたの連携仕様案にあった「confidence を安全性の根拠にしない」「仮想約定は実際の指値条件を反映する」は採用済み（共通仕様 §5 の指値規則がそれ）

## 成果物（`build-codex/` 直下）

```
build-codex/
├─ README.md            # セットアップ、CLI の使い方、設計上の判断、テスト結果
├─ requirements.txt
├─ .env.example
├─ .gitignore
├─ config/strategies.yaml
├─ aitrader/
│   ├─ __init__.py
│   ├─ __main__.py      # CLI（共通仕様 §7）
│   ├─ api.py           # init_db / load_synthetic / run_signals / run_backtest（受入テストが使う）
│   ├─ db.py
│   ├─ jquants.py
│   ├─ strategies/      # base.py, margin_bucket_long.py
│   ├─ backtest.py
│   └─ report.py
├─ results/margin_bucket_long_v1/   # 合成データ 2016-01-04〜2026-08-31 の summary.json / report.html（trades.csv, equity.csv は省略可）
└─ tests/               # 自分のユニットテスト（任意）
```

## 完了条件

1. `build-codex/` で `pytest ../common/tests -q` が **8/8 通過**
2. `python -m aitrader load-synthetic --seed 42` → `python -m aitrader backtest --strategy margin_bucket_long --from 2016-01-04 --to 2026-08-31 --out results/` が完走し、`summary.json` と `report.html` が生成される
3. README に、共通仕様で解釈が分かれうる点（例: リバランス週の重なり、未約定時の資金の扱い、営業日の扱い）を**どう解釈したか**を明記する。Claude 側との数値差の原因を人間が判定するために必要
4. 秘密情報を含めない。実口座・証券サイトに接続しない。シグナル経路で LLM を呼ばない

## 進め方の注意

- `common/` に不備を見つけたら `common/ISSUES.md` に追記する（編集はしない）。判断は人間が行う
- 「勝てるまでパラメータを探す」ことはしない。基盤の正しさと再現性を優先
- Windows でも動くように（パスは `pathlib`、コンソール出力は UTF-8）
- 完了したら README の末尾に、実行時間（10年バックテスト）と受入テストの結果を貼る

## 人間（則光）への確認が必要なとき

`build-codex/QUESTIONS.md` に質問を書いて止める。推測で仕様を変えない。
