# 自動連携 Codex 独立レビュー・修正記録

開始: 2026-09-09 09:11 JST。最新の専用A/B（devチャネル・状態表示・質問待ち）と、ユーザーの「連携機能の開発を優先」に対応。編集はtools/automation/とtools/tests/のみ。投資opsの実装・開発引き渡しBは変更していない。

## 結果

**Windows unittest 52件全通過、失敗/skipなし、5.978秒。** PowerShell 5.1.26100.9168の構文6ファイル通過。実プロジェクトのwatch/publish/pingpong/register各DryRunも通過。実AI起動、常駐登録、タスク起動、口座/LINE操作なし。

模擬登録でパラメータと制御分岐を検証し、隔離した日本語・空白・!・&・単一引用符を含む作業フォルダで、生成したEncodedCommandを実PS5.1で実行。ダミーwatchへの引数伝達とexit7の保持を確認した。タスクスケジューラサービスへの実登録は未検証なので「常駐稼働済み」とは扱わない。

## 修正した問題

| ID | 修正・根拠 |
|---|---|
| D01 | B検索がコード例の## Bまで数え、長さの異なるフェンスやチルダを区別せず、コード枠内マーカーを公開と扱えた。構造行を走査し、見出しと公開マーカーはコード枠外だけを採用。見出し終端#〜######はClaudeのC04変更を継承 |
| D02 | state検証がdict2項目のみで、負数/文字列の日次カウントや非有限の観測時刻を受け入れ得た。型・範囲・日付・観測値を検証し、不正時は起動せず停止 |
| D03 | Resume既定autoではdevの質問を調べず共有失敗ブロックを解除できた。ロック内で全チャネルの質問確認に変更。Resume DryRunはロック/状態も作らず確認のみ |
| D04 | 複数チャネルの先行質問が後続の待機結果に隠れ、Onceのexitが成功になった。tick内で検出した全結果を集約してexit1。実行結果doneでも後続チャネルを実行できたため、実行ログを持つ結果でtickを終了。静的doneは他方を確認 |
| D05 | DryRunのcli_foundが存在しない明示パスでもtrueだった。実ファイル・ネイティブCLI条件を検証し、cli_errorとprocessedを表示。devプロンプトにも質問の自己解決禁止を明記 |
| D06 | 状態表示はCLI実行中（最長45分）に更新されず、3周期で停止と誤判定し得た。処理中は別スレッドで周期更新し、観測不能と停止断定を区別。共有内容から依頼要約/runtimeパスを除去し、Codex/Claudeでファイルを分離。監視の制御入力には使わない |
| D07 | pingpongのPS入口にdev指定がなかった。-Channel auto/devを追加。watchの-NoStatusFile、statusの-Resume -DryRunも入口へ接続 |
| D08 | 登録スクリプトが引数・パスを無防備にPowerShell文字列へ挿入、DryRunなし、無条件Forceと即起動。型/値検証・単一引用符エスケープ・UTF16 EncodedCommandへ変更。実行ファイルとruntimeを事前解決。既存登録は明示Replaceのみ、稼働中/所有不一致は拒否。RegisterOnlyとUnregister DryRunを追加 |
| D09 | 登録処理の成功表示は稼働確認を意味せず、旧文書は終了コード・再起動条件も曖昧だった。子の終了コードを伝播し、表示はstart requestedに限定。Interactive/Limited・IgnoreNew・無期限・バッテリー継続を明示。安全停止後の3回自動再試行は廃止。電源設定は変更しない |

D08/D09の設定はMicrosoftの[タスク設定](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset?view=windowsserver2025-ps)と[実行ユーザー設定](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskprincipal?view=windowsserver2025-ps)およびPS5.1の模擬cmdletによる束縛検査を根拠とする。

## Claude変更の採否

- devの接頭辞はdev:、autoは既存キーを維持。ロック・ブロック・日次合計共有を採用。既存試験も維持。
- 複数チャネルのdone/daily_limit/question待機を採用。実行前からある質問は当該チャネルだけを止める。実行結果questionは全体失敗ブロックになる点は区別して説明。
- Coworkが相手宛てBを完成後publishする運用とnohandoff判定は整合。未更新・未公開は成功扱いしない。Cowork自体の自動起動までは担わない。
- C04採用済み。Claudeの#〜######変更に加え、枠外判定を補強。
- C05は現方式を維持。現行lockはos.openのfdをyield中も保持しており、「ハンドル非保持」という指摘はその点を訂正。排他作成で二重実行を抑止し、クラッシュで残ったロックを自動回収しない。PIDだけで所有者不在を推定して削除しない。手動アプリや人間によるファイル削除までOS排他で保証しない。
- 共有watch_statusはH12の例外を観測専用最小情報に限定。runtimeのstate/実行履歴/プロンプト/モデル応答ログは引き続きDropbox外。共有ファイルは操作判断の権威ではない。
- 日次合計上限は日跨ぎの総連鎖上限ではない。終了Bと手動停止で連鎖を閉じる既存制限をREADMEに明記。完全無制限の再試行は追加しない。

## 試験と環境

既存22件は無変更。作業中に別担当からC04/共有状態/質問待ちの変更と3試験が入り、それらと既存dev7試験を保持して計32件を確認。その後Codex回帰14件＋PS5.1統合6件＝52件。

最初の制限付きサンドボックスではtaskkillが子プロセスを止められず、タイムアウト2試験とcleanupがエラーになった。試験条件を弱めず、許可された通常ユーザー環境で同じ停止試験を含め32件通過（2.735秒）し、最終52件でも子・孫停止を確認。模擬登録試験の初回は試験用変数のスコープ誤りを検出し、隔離PowerShellプロセス内の試験変数へ修正。実装の失敗をskipしたものではない。

実行: `python -m unittest discover -s tools/tests -v`（Codex同梱Pythonの絶対パス使用）。[全試験出力](TEST_EVIDENCE.txt)と[Windows実機出力](PS_EVIDENCE.txt)。PS_EVIDENCE末尾のActual parser versionからの6件が実際のPS5.1構文結果（冒頭PARSEは呼出側PowerShellでも行った補助確認）。

Codex CLI0.153.4のhelpでsandbox/workspace-write、skip-git-repo-check、-C、output-last-messageを確認。制限付き環境のhelpではhome警告が出ており、実認証・モデル実行の確認とは区別する。Claude CLI未検出。模擬CLIに本物のモデル引数を渡してモデルを起動する試験は行っていない。

## 導入前に残ること

Claudeによる今回修正の最終独立確認。問題がなければ自動連携のCodex宛てBを引き渡し不要として公開する（手動で完了した現在依頼を初回監視で再実行させない）。ユーザーがREADMEの登録DryRun・登録/起動を行い、実行環境でのCLIログインと見張り状態を確認する。登録権限・実認証が未確認である以上、コード試験通過だけで稼働を保証しない。

今回の開発成果・検証は完了。ユーザーに先行して常駐を開始していない。必要な操作手順はREADMEへ具体化済み。
SHA256 codex_engine.py: 09A9C5C68B1CDF5D9533FC54F524A59E7948709A2119C05945D082079021EE8D

SHA256 register_watch_task.ps1: ED53D577E589695DB26EC5668C18286C0CE206F6B3F9C822446D252D6F042CC0

SHA256 test_handoff_review.py: AF8749074ADB9CFAF8A696010C7287AB623D1FA43516260BB9EDCC2F4AC2DBAA

SHA256 test_handoff_windows.py: C9E19168F20AD6A23FF4B5923AE74BA035BEE102ECE654128E760DE0AFBD90A6

終了時刻: 2026-09-09 09:23 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、tools/automation/Claude引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、自動連携のWindows対応修正D01〜D09と常駐登録・状態表示の最終独立確認を行ってください。
```
