# 自動連携（監視・交互実行・Windows常駐登録）

正規の入口はtools/automation/です。旧tools/_deprecated/は使用しません。初期52件の双方確認後、ユーザーの依頼によりCodexがWindowsの常駐タスクを登録・起動し、Claudeからの新規依頼による実Codex応答も確認しました。導入時の状態表示競合対策を含む最終Windows試験は57件全通過です。現在は開発依頼の保存先確認で停止しています。現在の導入記録は[DEPLOYMENT_STATUS.md](DEPLOYMENT_STATUS.md)を参照してください。

## 何を自動化するか

| チャネル | Codex宛て | Claude宛て |
|---|---|---|
| auto | tools/automation/Codex引き渡しプロンプト.md | tools/automation/Claude引き渡しプロンプト.md |
| dev | Codex引き渡しプロンプト.md | build-codex/Claude引き渡しプロンプト.md |

watchの既定はauto,dev。pingpongの既定はauto（-Channel devで切替）。質問はautoが専用QUESTIONS_codex.md/QUESTIONS_claude.md、devがbuild-codex/QUESTIONS.md/ops/QUESTIONS.md。処理済みhashと観測はチャネル別、ロック・失敗ブロック・1日合計上限はプロジェクト共通です。

現在PCにはCodex CLIを検出、Claude CLIは未検出です。Claude側をCoworkで担当し、PCのCodexだけを監視起動できます。Coworkそのものをこのスクリプトから自動起動する機能はありません。両CLIを使う交互実行にはClaude Codeの導入・ログインが別途必要です。

## 事前確認（状態を変更しない）

以下はai-traderフォルダを開いたPowerShellで実行します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\watch_handoff.ps1 -Agent codex -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\publish_handoff.ps1 -Channel dev -Agent codex -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\register_watch_task.ps1 -DryRun
```

DryRunは1回表示して終了します。AI起動、公開マーカー更新、タスク登録、処理済み記録、ログ、共有状態表示ファイル、Git操作を行いません。published/summary/processed/cli_found/cli_error/question/blockedを確認してください。登録DryRunは起動予定の依頼も表示します。手動作業で完了した依頼でも、監視側に処理済み記録がなければ自動起動対象です。登録前に担当者が相手宛てBを正しい次依頼または引き渡し不要へ更新・公開します。自分宛てBを書き換えたり、stateを消して調整したりしません。

Python3.10以上（標準ライブラリのみ）が必要です。-PythonExe → AI_TRADER_PYTHON → PATHのpython.exe（WindowsApps除外）→ Codex同梱Pythonで選択。必要なら-CodexExe/-ClaudeExeにネイティブ実行ファイルの絶対パスを指定します。cmd/bat/ps1のCLIラッパーは拒否します。登録時にPythonと担当CLIの絶対パスを保存するため、ログイン時のPATHに依存しません。Codex更新で実行ファイルの場所が変わったら停止確認後に登録を更新してください。

## 依頼の公開

担当する成果物・検証・報告をすべて保存し、最後に相手宛てBを完成させて公開します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\publish_handoff.ps1 -Channel dev -Agent claude
```

Pythonでは `python tools/automation/codex_engine.py publish --channel dev --agent claude`。autoの場合は--channel auto、宛先Codexなら--agent codexです。

Bには「今回の依頼: …」を1物理行・1件だけ記載します。公開後にBを編集するとSHA256が一致しなくなり、再公開まで起動しません。コード例内の見出し・マーカーは制御命令になりません。B終端はコード枠外の#〜######見出しまたは---。保留・履歴は対象外です。バッククォートとチルダのコード枠、長さの違いを区別します。

同一Bのhashは全履歴で再実行を防ぎます。再試行する場合は理由・変更を含む新しいBを公開します。公開はファイルの完成通知で、相手への送信・起動完了を意味しません。

## 一時的な監視と交互実行

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\watch_handoff.ps1 -Agent codex
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\pingpong.ps1 -Channel dev -StartWith codex -MaxRounds 1
```

監視は既定60秒周期。同じ完全ファイルを2回観測し、StableSec（既定60秒）を経て実行します。-Onceは1回観測するだけなので、初回settlingは正常です。1tickにつき実行は最大1件。MaxRunsPerDay（既定10）は両担当・全チャネル合計、JST日付で数えます。無限の再試行や上限引上げは行いません。日付が変わると日次枠は戻るため、何日も続く依頼連鎖を止める累計上限の代用ではありません。

複数チャネルではdone/daily_limit/questionを待機扱いにして他方を確認します。単一チャネルはdoneで正常終了、日次上限・質問で停止。実行中にエージェントが新しい質問を書いて止まった場合は、実行結果のブロックが全チャネルに残ります。既存の質問待ちだけとは区別します。-Onceは確認したどのチャネルに質問・失敗があってもexit1です。

交互実行は両CLIが必要で、相手の新しい公開済みBがなければ停止します。監視・交互実行は同じロックと日次枠を使います。手動のCodexアプリ作業はこのロックに参加しないため、同担当の手動作業と監視を同時に走らせません。

## 常駐登録（登録・起動確認済み。現在は確認待ちで停止）

2026-09-09、ユーザーがPowerShell操作をCodexへ委任し、以下の登録・起動を実施済みです。現在のPCで初回登録を繰り返す必要はありません。以下は再導入・保守時の手順です。

```powershell
# 登録だけ（その場では起動しない。次回ログイン時の起動は有効）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\register_watch_task.ps1 -RegisterOnly
# 登録と同時に起動する場合はこちら
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\register_watch_task.ps1
# 登録済みを起動／停止／確認
Start-ScheduledTask -TaskName 'ai-trader handoff watch codex'
Stop-ScheduledTask -TaskName 'ai-trader handoff watch codex'
Get-ScheduledTask -TaskName 'ai-trader handoff watch codex' | Get-ScheduledTaskInfo
```

タスクは現在ユーザーのログイン時、非表示・Interactive/Limitedで動きます。管理者権限への引上げ・パスワード保存はしません。バッテリー切替で強制停止しない設定ですが、スリープ/電源オフ/ログアウト中は監視できません。電源設定はスクリプトで変更しません。

既存タスクは勝手に上書きしません。自プロジェクトの停止済みタスクを意図して更新する場合だけ-Replaceを指定します。実行中または別/旧方式の登録は拒否するため、まずタスクスケジューラで確認します。停止済み自プロジェクト登録の解除はregister_watch_task.ps1 -Unregister（-DryRunで事前表示）。失敗後の自動再試行は登録しません。従来説明にあった「10分ごと3回再起動」は廃止しました。

起動要求が成功しただけでは見張り稼働・CLIログイン成功を意味しません。タスク状態、ログ、共有状態表示を確認します。標準出力ログはDryRunに表示されるプロジェクトruntime内のwatch_codex.logです。

## 状態・停止・復旧

状態・ログ本体はDropbox外のAI_TRADER_HOME/automation-handoff/<project-key>/（既定~/.ai-trader/）です。共有フォルダには表示用のwatch_status.json（Codex）/watch_status_claude.jsonだけを原子的に書きます。内容は担当・各チャネルの状態/観測時刻・ブロック有無・PID・更新時刻。依頼本文/要約/ログ/ローカルruntimeパスは含めません。実行中も別スレッドで周期更新します。これは観測専用で、次の作業を起動する根拠には使いません。

3周期以上更新されなければ「生存確認できない」と扱い、Dropbox同期遅れ・PCスリープ・プロセス状態を確認してください。停止の断定や自動再起動はしません。-NoStatusFile（Python --no-status-file）で共有表示を抑止できます。DryRunは書きません。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\handoff_status.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\handoff_status.ps1 -Resume -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\handoff_status.ps1 -Resume
```

Resumeは全チャネルの質問を確認してから失敗ブロックを解除します。処理済みhash・日次回数は消しません。質問解決は人間が該当ファイルに `<!-- handoff-questions: resolved -->` を記入します。エージェントの自己解決は禁止。クラッシュで残ったproject.lockは自動削除しません。元プロセスと子孫が終了したことをPC上で確認してから、そのファイルだけを人間が除去します。状態ファイル全体を削除しないでください。

Windowsのタイムアウトは対象PIDのプロセスツリーを終了します。権限不一致等で失敗すれば永続ブロックを保ちます。完全なOS隔離や特殊な子プロセスの離脱まで保証するものではありません。登録タスクを停止した場合も、処理中なら子プロセスの終了とロックを確認してから再開します。

## 検証範囲

Windows標準unittest **52/52、skipなし、5.978秒**。既存32＋Codex追加20（うちPS5.1統合6）。PS5.1の構文6/6、実フォルダのwatch/publish/pingpong/register各DryRun通過。模擬登録でInteractive/Limited・既存稼働タスク拒否・日本語/空白/記号/引用符パス・終了コード伝播を確認。

追加の状態表示競合試験5件を含め、最終Windows試験は **57/57、skipなし、7.149秒**。Windowsの読取りハンドル保持による置換失敗と、解放後の成功も実ファイルで確認しました。表示用の更新だけに上限5回・待機合計1.55秒の再試行を追加し、プロトコルファイルの更新条件や失敗ブロックは維持しています。

タスクスケジューラへの実登録・起動、監視プロセス、共有状態表示、公開済み開発依頼の検知、実Codex応答まで確認済みです。次の開発依頼は保存先が子CLIの書込み範囲外のためbuild-codex/QUESTIONS.mdに確認事項を残し、質問ブロックで停止しました。現在の監視プロセスはありません。質問解決・改訂依頼の公開・Resume後に登録済みタスクを再起動する必要があります。投資審査LLM・実LINE・実口座の接続は行っていません。

[Codexレビュー](CODEX_REVIEW.md) / [Windows証跡](PS_EVIDENCE.txt) / [試験証跡](TEST_EVIDENCE.txt) / [Claudeへの専用依頼](Claude引き渡しプロンプト.md)。初期実装記録はIMPLEMENTATION.md、旧版の採否はCLAUDE_REVIEW.md。

設定の根拠：[Microsoftのタスク設定](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset?view=windowsserver2025-ps)、[実行ユーザー設定](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskprincipal?view=windowsserver2025-ps)。

## 質問判定の更新（2026-09-09）

QUESTIONS.mdは##見出しごとの質問ブロックとして判定します。各ブロック内に依頼hashを記載し、本人回答の解決マーカーは該当ブロックだけに置きます。過去のresolvedは新質問には効きません。hashが違う未解決質問も停止を維持します。DryRun/Onceではquestionsにファイル・見出し・hashを表示します。見出しなし旧形式は一ブロックとして互換、コード枠内マーカーは無効です。

今回追加9件を含むWindows66件全通過（6.465秒）。[修正・採否・書込拒否の診断](QUESTION_REVIEW.md)。現在はdevの書込権限質問が残り、両監視停止中。具体的な権限修復の本人承認待ちで、監視復旧済みとは扱いません。

## Codex単独開発と生成物の保存先（2026-09-11）

ユーザー指示により開発はCodex単独で継続する。ユーザーが連携再開を指示するまでClaudeへの新規公開・起動は行わない。この記載は既存タスクの停止・解除・再登録を実施した意味ではない。最新方針は../../AGENTS.md冒頭を参照。

手動Codexの並列エージェントは編集担当ファイルを分け、pytestのbasetempもエージェントごとにDropbox外の別ディレクトリにする。pytestは指定basetempの既存内容を消して準備するため、同じ保存先で同時実行しない。全体試験は担当試験が終了してから統合担当が実施する。

pytestには `-p no:cacheprovider` とDropbox外の `--basetemp` を必ず付け、`PYTHONDONTWRITEBYTECODE=1` で同期対象への__pycache__生成を抑制する。例：`C:\Users\s\AppData\Local\Temp\ai-trader-budget-review` と `C:\Users\s\AppData\Local\Temp\ai-trader-mask-review`。別PCでは実在する許可済みの一時フォルダに読み替える。

コード・テスト・仕様案は共有作業フォルダに保存するが、DB・実行ログ・キャッシュはDropbox外。保存失敗を迂回するために成果物を別フォルダへ隠したり、受入条件を緩和したりしない。権限修復後は実際の保存と実測で確認する。既存の質問マーカーや見張りのstateは開発テストの成功だけで自動解除しない。

## 最新状態（2026-09-11、ユーザー訂正）

Claude制限解除まで両見張りを停止・無効化（Disabled）した。ログイン時も起動しない。Codex単独開発を継続。エンジンE01/E02/E05/E08は修正済み、Windows CP932で75件7.760秒、CP65001で75件7.782秒、全通過・skipなし。詳細は[QUESTION_REVIEW.md](QUESTION_REVIEW.md)末尾。Claudeへの公開、resume、登録削除、処理履歴の削除はしていない。使用量確認の自動化は30分ごとで別に継続する。
