# ピンポン方式（Claude Code ⇄ Codex 自動引き渡し）

作成: 2026-09-09 06:10 JST（Claude / Cowork）

## 何をするか

いままで則光さんが手でやっていた「Claude が書いた一文を Codex に貼る → Codex の報告を Claude に見せる」を、
`tools/pingpong.ps1` が代行する。通信路は**既存の引き渡しファイルそのもの**なので、AGENTS.md・報告テンプレート・履歴表の運用は変わらない。

```
Codex引き渡しプロンプト.md の B ──▶ codex exec ──▶ build-codex/Claude引き渡しプロンプト.md の B
        ▲                                                            │
        └──────────────── claude -p ◀────────────────────────────────┘
```

各ターンは、相手が書いた B の先頭行「今回の依頼: …」から、いつもの一文

```text
ai-trader の作業フォルダで、〈引き渡しMD〉を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、〈今回の依頼〉を行ってください。
```

を組み立てて CLI に渡す。加えて「無人実行なので質問は QUESTIONS.md に書いて止まる」「相手に渡す作業がなければ B に『引き渡し不要』と書く」という追加規則を付ける。

## 使い方（則光さんの PC、ai-trader フォルダで）

```powershell
powershell -ExecutionPolicy Bypass -File tools\pingpong.ps1                    # Codex から開始、最大 3 往復
powershell -ExecutionPolicy Bypass -File tools\pingpong.ps1 -StartWith claude  # Claude から開始
powershell -ExecutionPolicy Bypass -File tools\pingpong.ps1 -MaxRounds 1       # 1 往復だけ
powershell -ExecutionPolicy Bypass -File tools\pingpong.ps1 -DryRun            # 実行せず、渡す一文とコマンドだけ表示
```

| 引数 | 既定 | 意味 |
|---|---|---|
| `-MaxRounds` | 3 | 往復数の上限（1 往復 = Codex 1 ターン + Claude 1 ターン） |
| `-StartWith` | codex | 最初に動かす側。相手向け B が最新になっている側を先にする |
| `-TurnTimeoutMin` | 45 | 1 ターンの上限時間 |
| `-CodexModel` / `-ClaudeModel` | 空 | モデル指定（空なら各 CLI の既定） |
| `-ClaudeSkipPermissions` | off | Claude を `--dangerously-skip-permissions` で起動。既定は `--permission-mode acceptEdits` ＋ 許可ツールを Read/Edit/Write/Glob/Grep と `python` `pytest` `git` 系の Bash に限定 |
| `-NoGit` | off | ターンごとの git commit を行わない |

前提: `claude`（Claude Code CLI）と `codex` の両方がインストール・ログイン済み。Cowork（クラウド側）からは OpenAI に到達できないため、**必ず PC 上で実行する**。

## 停止条件（暴走防止）

| 条件 | 意味 |
|---|---|
| `-MaxRounds` 到達 | 往復数の上限。まず 1〜2 で様子を見る |
| `build-codex/QUESTIONS.md` / `ops/QUESTIONS.md` がそのターンで更新された | 人間の判断待ち。答えを書いてから再実行 |
| 相手向け B の先頭行が「今回の依頼: 引き渡し不要（…）」 | 作業完了 |
| ターン後に相手向け B が更新されていない | 引き渡しが行われなかった＝異常。ログを確認 |
| タイムアウト / CLI の異常終了 | `logs/pingpong/*_stderr.txt` を確認 |

加えて毎ターン `git commit`（リポジトリがなければ初回に `git init`、`.gitignore` に logs/results/sqlite/.env を登録）。
「2 つの AI がテストを通すために仕様を緩め合う」事故は、A 固定プロンプトの禁止事項＋このコミット履歴で後から検出する。

## 出力

- `tools/pingpong_history.md` — 開始/終了（JST）・担当・依頼・結果の表（自動追記）
- `logs/pingpong/rN_<agent>_<stamp>_{prompt,stdout,stderr}.txt`、`_last.md`（最終応答）
- `logs/pingpong/last_run.md` — 停止理由のまとめ
- 環境変数 `PINGPONG_NOTIFY_CMD` を設定すると終了時にそのコマンドを（停止理由を引数に）呼ぶ。LINE 通知などに使える

## 運用ルール（両 AI 共通。AGENTS.md / CLAUDE.md の規則に追加）

1. B の先頭行は必ず `今回の依頼: …` の形。スクリプトはこの行しか読まない
2. 未完了のまま B を更新しない（更新＝相手にバトンを渡す、の意味）
3. 質問があるときは自分側の QUESTIONS.md に書いて止まる。B は触らない
4. 全体が完了したら相手向け B の先頭行を `今回の依頼: 引き渡し不要（理由）` にする
5. 履歴表（各引き渡しMD の「過去の依頼」）にも従来どおり行を足す。スクリプトの `pingpong_history.md` は機械側の記録

## 監視方式（tools/watch_handoff.ps1）— 1 分ごとに引き渡しファイルを見張る【本命】

ピンポンが「1 本のスクリプトが両者を順に起動する」のに対し、監視方式は**片側ずつ独立した見張り**を置く。
見張りは 1 分ごとに自分宛ての依頼ファイルを確認し、更新されていれば同じ一文プロンプトで CLI を起動する。

### チャネル（開発と自動連携を混ぜない）

| チャネル | 用途 | Codex 宛て（Claude が書く） | Claude 宛て（Codex が書く） |
|---|---|---|---|
| `dev` | 自動投資システムの開発ループ | `Codex引き渡しプロンプト.md`（## B. 今回の依頼） | `build-codex/Claude引き渡しプロンプト.md`（## B. 今回の依頼） |
| `auto` | 自動連携（このスクリプト群）の作業 | `tools/自動連携_Codex依頼.md`（## 今回の依頼） | `tools/自動連携_Claude依頼.md`（## 今回の依頼） |

見張りは既定で両チャネルを順に確認する（`-Channel dev` で片方だけ）。起動回数の上限と処理済みハッシュはチャネル別、ロックは Agent 別（同じ CLI は同時に 1 つ）。
起動された側は「自分のチャネル以外の引き渡しファイルは変更しない」という追加規則を受け取る。

### 起動方法（PC の ai-trader フォルダ）

| 方法 | 手順 |
|---|---|
| 手動で常駐 | `tools\start_watch_codex.cmd` をダブルクリック（ウィンドウを開いたままにする）。引数はそのまま渡せる: `start_watch_codex.cmd -DryRun` |
| ログオン時に自動起動 | `powershell -ExecutionPolicy Bypass -File tools\register_watch_task.ps1`（タスクスケジューラに「ai-trader watch codex」を登録し非表示で常駐。解除は `-Unregister`） |
| 1 回だけ確認 | `powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent codex -Once` |
| Claude 側も PC で | `tools\start_watch_claude.cmd`。**Cowork が Claude 側を担当している間は起動しない**（二重担当になる） |

利点は、Claude 側を **Cowork（クラウドの Claude）が担当できる**こと。Cowork が依頼ファイルを書いて保存すれば、PC の Codex 見張りが 1 分以内に拾い、
結果は Claude 宛てファイルに返る。設計相談と実装を同じセッションで続けられる。

### 起動条件（6 つすべて）

B のハッシュが前回処理分と違う／更新から `-StableSec`（既定 60 秒）以上経過（書きかけ・Dropbox 同期中を拾わない）／
「引き渡し不要」でない／自分側の QUESTIONS.md が依頼より新しくない（人間の回答待ちでない）／当日の起動回数が `-MaxRunsPerDay`（既定 10）未満／ロックなし。
起動前にハッシュを処理済みとして `logs/watch/state_<channel>_<agent>.json` に記録するので、同じ依頼で二度は動かない。
両側に見張りを置くとピンポンのようなラウンド上限がないため、**1 日の起動回数上限が唯一のブレーキ**になる。まず 5〜10 で運用する。

| 引数 | 既定 | 意味 |
|---|---|---|
| `-Agent` | 必須 | codex / claude |
| `-Channel` | dev,auto | 見張るチャネル |
| `-IntervalSec` | 60 | 確認間隔 |
| `-StableSec` | 60 | 更新からこの秒数が経つまで起動しない |
| `-MaxRunsPerDay` | 10 | 当日の起動回数上限（チャネル別、JST 日付） |
| `-Once` | off | 1 回確認して終了 |
| その他 | | `-TurnTimeoutMin` `-CodexModel` `-ClaudeModel` `-ClaudeSkipPermissions` `-NoGit` `-DryRun` はピンポンと同じ |

ログは `logs/watch/`、履歴は同じ `tools/pingpong_history.md`（チャネル欄つき）。

## 初回の試し方

1. `tools\start_watch_codex.cmd -DryRun` で、両チャネルの起動判定と一文が意図どおりか確認（DryRun でも処理済みハッシュは記録されるので、本番前に `logs\watch\state_*.json` を削除）
2. `tools\start_watch_codex.cmd` で常駐。1 分以内に更新済みの依頼を拾って Codex が動く。`tools\pingpong_history.md` と `logs\watch\` を確認
3. 安定したら `register_watch_task.ps1` でログオン時自動起動に切り替える
4. ピンポン方式を使う場合は `-DryRun` → `-MaxRounds 1` → `-MaxRounds 3` の順
