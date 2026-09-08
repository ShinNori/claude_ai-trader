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

## 初回の試し方

1. `-DryRun` で、渡される一文とコマンドが意図どおりか確認
2. `-MaxRounds 1 -StartWith codex` で 1 往復だけ実行し、両 B の更新・QUESTIONS・git log を確認
3. 問題なければ `-MaxRounds 3` で放置運転。停止理由は `logs/pingpong/last_run.md`
