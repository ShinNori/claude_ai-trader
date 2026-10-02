# ai-trader 引き継ぎ（別 PC・別 AI ツールで開発を続けるための手順）

作成 2026-10-02（N2601 の Claude Code）。N2602 の Orca AI など、この PC 以外・Claude Code 以外で開発を続けるときは、**まずこのファイル → [共通指示.md](共通指示.md) → [build-codex/TEAM_WORKFLOW.md](build-codex/TEAM_WORKFLOW.md)** の順に読む。開発ダッシュボード側の同名文書（`D:\Desktop\ClaudeCode\00_dashboard\引き継ぎ_別PC移行.md`）と同じ形式。

```
N2601（これまで）                         N2602（これから）
D:\work\ai-trader ──git push──▶ GitHub ShinNori/claude_ai-trader ──git clone──▶ D:\work\ai-trader
  Claude Code の記憶(memory)は移らない ──▶ 本ファイル §4「引き継ぐルール」に全文を書いてある
  見張りタスク・実行状態(~/.ai-trader)・ダッシュボード用トークンは PC ごと ──▶ §1 で作り直す
```

## 1. 新しい PC の初期設定（1 回だけ）

1. **clone**: `git clone https://github.com/ShinNori/claude_ai-trader.git D:\work\ai-trader`（Dropbox の外に置く。製品コード・自動連携に絶対パス依存は無いので場所は変えてよいが、文書は `D:\work\ai-trader` と書いている）。改行は `.gitattributes` の `* -text` で一切変換しない（生バイト hash に依存する試験があるため。`core.autocrlf` を有効にしても影響しない）。
2. **Python 3.12** と `pytest`・`duckdb`。N2601 では Codex 同梱の `%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`（CPython 3.12.14）を使っていた。`python` コマンドは Windows Store のスタブで動かないことがある。
3. **動作確認**（リポジトリ直下で。DB と一時ファイルは必ず Dropbox 外）:
   ```powershell
   $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONUTF8='1'; $env:PYTHONPATH='ops;build-codex'
   python -m pytest build-codex/tests/test_exit_generation_v6.py build-codex/tests/test_runner_resume.py build-codex/tests/test_evidence_bundle_store.py -q -p no:cacheprovider --basetemp $env:TEMP\ai-trader-check
   ```
   N2601 の実測（2026-10-02）: `105 passed, 1 skipped / 11.14 秒`（skip は symlink 作成権限が無い環境のもの）。同じ結果になれば環境は整っている。
4. **ダッシュボード**（任意だが推奨）: `D:\Desktop\ClaudeCode\00_dashboard` を同じ場所に clone し、`%USERPROFILE%\.dashboard_token` を置き、Node.js を入れる（同フォルダの引き継ぎ文書 §1）。無い PC ではダッシュボード更新を省略し、報告に「未更新」と書く。
5. **AI への指示の参照**（PC ごとの設定。git には入っていない）: Claude Code は `C:\Users\<ユーザー>\.claude\CLAUDE.md`、Codex は `C:\Users\<ユーザー>\.codex\AGENTS.md`。リポジトリ内の [CLAUDE.md](CLAUDE.md)・[AGENTS.md](AGENTS.md) は clone で付いてくる。**Orca AI は「常に読む指示」に本ファイルと 共通指示.md を読むよう設定する（設定場所は未確認）**。
6. **往復の自動化（見張り）は N2602 では既定で使わない**。N2601 では `tools/automation`（Codex CLI を起動するタスクスケジューラ登録）で Claude⇄Codex の往復を回していたが、Orca AI がオーケストレーションを担うなら二重になる。使う場合だけ 共通指示.md §6 のコマンドで登録する（Codex の自動更新のたびに再登録が必要。auto チャネルは古い依頼が残っているので dev のみ）。N2601 の登録は解除済み。

## 2. 何がどこにあるか

| もの | 場所 | git |
|---|---|---|
| 次に何をするか（手番つきキュー）・環境の約束 | [共通指示.md](共通指示.md) | ○ |
| 役割分担・省トークン運用・Claude 宛 B の必須 3 項目 | [build-codex/TEAM_WORKFLOW.md](build-codex/TEAM_WORKFLOW.md) | ○ |
| Codex 宛ての依頼（A 固定プロンプト＋B 今回の依頼＋履歴表） | [Codex引き渡しプロンプト.md](Codex引き渡しプロンプト.md) | ○ |
| Claude 宛ての依頼 | [build-codex/Claude引き渡しプロンプト.md](build-codex/Claude引き渡しプロンプト.md) | ○ |
| レビュー側（契約整理・独立確認）の手順と回数 | [Claude_Opus引き継ぎ.md](Claude_Opus引き継ぎ.md) | ○ |
| 完成までの工程と現在地 | [build-codex/COMPLETION_ROADMAP.md](build-codex/COMPLETION_ROADMAP.md) | ○ |
| 製品コード（Codex 所有） | `build-codex/aitrader/`、台帳・ゲート・通知は `ops/aitrader_ops/` | ○ |
| 共通仕様・受入試験（編集禁止。不備は ISSUES.md に追記） | `common/` | ○ |
| 人が答える質問と解決マーカー | [build-codex/QUESTIONS.md](build-codex/QUESTIONS.md) | ○ |
| 実行時の DB・ログ・自動連携の状態 | `%AI_TRADER_HOME%` または `~/.ai-trader`（Dropbox 外） | ×（PC ごと） |
| 上位の設計書・戦略カタログ | N2601 の Dropbox `999_投資関係\`（リポジトリ外） | ×（必要なら則光から受け取る） |

## 3. 仕事の回し方（ツールが変わっても守ること）

```
契約整理・独立確認（レビュー側）──依頼 B──▶ 実装・統合試験（実装側）──依頼 B──▶ レビュー側 …
  成果: 契約文書 / 新規 test_*_claude_contract.py        成果: 製品コード / 試験 / README 記録
```

- **役割**: 実装側（これまで Codex）= 製品実装・修正・Windows 実測・README 記録。レビュー側（これまで Claude）= 未決契約の整理、既存試験と重複しない独立反証、重要差分の確認。同じ実装を二重に作らない。Orca AI で担当エージェントを割り当てるときもこの 2 役を分ける。
- **1 往復 = 依頼 1 件**。依頼は相手宛て文書の B 節に書く（先頭行は `今回の依頼: …` を 1 行）。次の作業が無ければ `引き渡し不要（理由）`。
- **同じ依頼を 2 台・2 経路で同時に走らせない**（2026-09-17 に同一ファイルの同時編集で衝突した）。
- **「次」とだけ言われたら** 共通指示.md の「次の作業キュー」で自分の手番の先頭 1 件を既定値で進め、1 件終えたら報告して止まる。確認を取るのは削除・上書き・force push・実接続・発注・使用率上限の引き上げだけ。
- **人の判断が要るとき** は QUESTIONS.md に質問を書いて止まる。解決マーカー `<!-- handoff-questions: resolved -->` は人の回答を転記したときだけ付ける。
- **作業終了時**: 相手宛て B 更新 → `git add`（対象を列挙して確認）→ commit → push → ダッシュボード更新 → 報告（変更ファイル・実測の件数/秒/環境・未確認事項・次の担当）→ キュー更新。
- **禁止**: 実口座・証券サイト・実 LINE・実審査 LLM への接続、発注、秘密情報のコミット、`common/` の編集、既存試験の削除・skip・xfail・条件緩和、推測での補完（確かめられないことは「未確認」と書く）。

## 4. 引き継ぐルール（N2601 の Claude の記憶から転記）

- **報告の末尾に必ず 1 行**: `時刻: YYYY-MM-DD HH:MM JST ／ トークン残: …`。時刻は実測し推測しない。最後に「次に行いたいこと」を書く。
- **git**: 往復 1 回ごとに 1 コミット。clone 運用では毎回 push。remote は GitHub `ShinNori/claude_ai-trader`、ブランチ `master`。コミット前に対象を列挙して確認し、ログ・DB・`.env`・トークンを含めない。
- **pytest** は必ず `-p no:cacheprovider --basetemp <Dropbox 外>`（Dropbox 内にキャッシュを作ると同期できないフォルダが残る）。
- **N2601 の Codex 見張り実行の制約**（ツールが変われば当てはまらない可能性あり）: サンドボックスが `.git` を書けず外部送信もできないため、Codex は作業ツリーへの保存まで、commit/push とダッシュボード更新はレビュー側が代行していた。
- 応答は日本語・結論先・図（構造や流れは枠線と矢印）・完了報告の形式は `C:\Users\<ユーザー>\.claude\CLAUDE.md` に従う。
- 契約案を書く前に、関係する既存文書とコード（保存先・状態遷移・既存 API）を先に読み切る（読み落としで往復が 6 回に増えた反省）。

## 5. 現在地（2026-10-02、最新コミットは本ファイルを追加したもの）

| 工程 | 状態 |
|---|---|
| 1 模擬日次基盤 | 完了 |
| 2 実データの根拠 | 人工検査（厳密入力・証拠履歴 v1/v2・期間履歴・結合 v1/v2・CLI・人工束の保存 put/verify）は実装・独立確認済み。**実原本・公表時刻の根拠は未**（則光の実データ契約待ち） |
| 3 保存と日次組込 | **実保有からの SELL（EXIT）候補生成は完了**（`packet.build_exit_proposals`・`exit_holdings.derive_holdings`、契約 v6＋D4、独立確認済み）。日次 runner への接続とスナップショット・SPLIT 記録の永続化は未 |
| 4 実接続 | 模擬まで。Claude/Codex/LINE の実接続は未（則光の認証許可待ち） |
| 5 異常終了からの復旧 | **Tier A 完了**（managed-v1 home で INTENT×CREATED/APPROVED の明示的再開。`runner_resume.py`、独立確認済み）。台帳 receipt（Tier B）と翌日予約の扱いは未 |
| 6 日次連続運転 | 未 |

- 依頼の状態: Codex 宛・Claude 宛の B はどちらも「引き渡し不要」。QUESTIONS.md は全件解決済み。
- レビュー側の受領回数は 25（次は第 26 回）。指摘番号は R26-xx から。
- 次にレビュー側が起こす工程（キュー未登録。着手時に 共通指示.md のキューへ追加）: **日次 runner への EXIT 生成の接続と、スナップショット・SPLIT 記録の永続化の契約整理**。
- 則光の操作待ち（キュー #7）: 実データ契約・アカウント・原本の出所、実接続の認証許可、実運転 PC と稼働時間。

## 6. Orca AI に最初に渡す一文（コピー用）

```text
D:\work\ai-trader の 引き継ぎ_別PC移行.md と 共通指示.md を読み、役割分担（実装側・レビュー側）を割り当てたうえで、「次の作業キュー」に次工程（日次 runner への EXIT 生成の接続とスナップショット・SPLIT 記録の永続化の契約整理）を追加し、レビュー側の手番として既定値で進めてください。
```
