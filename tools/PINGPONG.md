# Claude Code ⇄ Codex 自動引き渡し（監視方式 v2 ／ ピンポン方式）【廃止・経緯記録】

> **2026-09-09 07:40 JST 採用決定（Claude）**: 自動連携の正規実装は **`tools/automation/`（Codex 版 Python エンジン＋PS5.1 入口）** に一本化し、
> Claude が **dev チャネル**（投資本体の開発ループ）を追加した。本書で説明していた `tools/watch_handoff.ps1`・`pingpong.ps1`・起動 cmd・タスク登録は
> `tools/_deprecated/` へ移して廃止。以降の使い方は `tools/automation/README.md`、採否と H01〜H18 の反映状況は `tools/automation/CLAUDE_REVIEW.md`。
> 本書はそれまでの設計経緯の記録として残す。


作成: 2026-09-09 06:10 JST（Claude / Cowork）。v2: 07:40 JST — Codex レビュー `build-codex/PINGPONG_REVIEW.md`（H01〜H18）を監視方式に反映。
**現在の推奨は監視方式 v2（tools/watch_handoff.ps1）のみ。ピンポン方式（tools/pingpong.ps1）はレビューで A 判定が多く、無人運用には使わない（手動の 1 往復検証用に残す）。**

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

### v2 の安全策（Codex レビュー H02〜H17 への対応）

| 項目 | v2 の動作 |
|---|---|
| 実行時ファイル | state / lock / HALT / ログは **Dropbox 外** `%AI_TRADER_HOME%\handoff\`（既定 `%USERPROFILE%\.ai-trader\handoff\`）。共有フォルダには `tools/pingpong_history.md` の要約行だけ |
| 排他 | プロジェクト共通ロック `handoff.lock` を FileMode.CreateNew で原子的に取得し作業中は保持。取得後に依頼を読み直して不変を確認。クラッシュで残ったロックは人間が削除 |
| 依頼の読み取り | 「## B. 今回の依頼」または「## 今回の依頼」から次の見出し（#〜######）/`---` まで。コードフェンス内の見出しは無視。「今回の依頼:」行は **1 物理行・1 件・非空** でなければ起動しない（「保留中の依頼」のような ### 節は依頼に含めない） |
| 書きかけ対策 | 同じ内容を `-StableSec` 以上の間隔で 2 回観測し、かつ mtime も古いときだけ起動 |
| 処理済み | hash の履歴（直近 200 件）。A→B→A の再実行も抑止。失敗（timeout / 異常終了 / 引き渡しなし）は処理済みにせず **HALT**（`HALT_<agent>.txt`）。人間が確認して削除するまで起動しない |
| 質問待ち | 自分側の QUESTIONS.md が存在し「回答済」を含まない間は起動しない（時刻比較はしない） |
| 上限 | 当日 `-MaxRunsPerDay`（既定 10）に加え、チャネル累計 `-MaxRunsTotal`（既定 20）。到達後は `-ResetCounters` するまで再開しない |
| CLI 起動 | `codex -a never exec --sandbox workspace-write … -`（承認待ちで止まらず失敗として返す）。実行ファイルは絶対パス解決、モデル名は英数字 `. _ -` のみ。タイムアウトは `taskkill /T` でプロセスツリーごと停止 |
| 完了判定 | 先頭行が「引き渡し不要」で **始まる** 場合のみ |
| DryRun | 読取・表示のみ。state / lock / ログを書かず 1 回で終了 |
| 廃止 | 通知フック（`PINGPONG_NOTIFY_CMD`）と git 自動 commit。スナップショットは人間が取る |
| 終了コード（-Once） | 0 = 起動なし/成功、2 = 質問待ち、3 = HALT/異常、4 = 引き渡しなし |

`-Status` で HALT・上限・処理済みの状態を表示できる。

| 引数 | 既定 | 意味 |
|---|---|---|
| `-Agent` | 必須 | codex / claude |
| `-Channel` | dev,auto | 見張るチャネル |
| `-IntervalSec` | 60 | 確認間隔 |
| `-StableSec` | 60 | 更新からこの秒数が経つまで起動しない |
| `-MaxRunsPerDay` | 10 | 当日の起動回数上限（チャネル別、JST 日付） |
| `-MaxRunsTotal` | 20 | チャネル累計の起動上限。`-ResetCounters` で 0 に戻す |
| `-Once` | off | 1 回確認して終了（終了コードで結果を返す） |
| `-Status` / `-ResetCounters` | | 状態表示 / 累計カウンタのリセット |
| その他 | | `-TurnTimeoutMin` `-CodexModel` `-ClaudeModel` `-ClaudeSkipPermissions` `-DryRun` |

ログは `%USERPROFILE%\.ai-trader\handoff\logs\`、履歴は `tools/pingpong_history.md`（チャネル欄つき）。

## 初回の試し方

1. `tools\start_watch_codex.cmd -DryRun` で、両チャネルの起動判定と一文が意図どおりか確認（v2 の DryRun は副作用なし）
2. `tools\start_watch_codex.cmd` で常駐。新しい依頼は検知から `-StableSec` 後に起動する。`-Status`、`tools\pingpong_history.md`、`%USERPROFILE%\.ai-trader\handoff\` を確認
3. 安定したら `register_watch_task.ps1` でログオン時自動起動に切り替える
4. HALT したら `HALT_codex.txt` の理由とログを確認し、直してからファイルを削除する。ピンポン方式は無人運用に使わない
