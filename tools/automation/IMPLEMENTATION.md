# 自動連携実装報告（Codex別枠）

2026-09-09。ユーザーの「ここでは自動連携スクリプトを実装」「Claude同様に別枠で進める」に従って実装した。

## 成果物

正規の入口・本体はtools/automation/内：codex_engine.py、watch_handoff.ps1、pingpong.ps1、handoff_common.ps1、publish_handoff.ps1、handoff_status.ps1。依頼と質問も同ディレクトリ専用。試験はtools/tests/test_handoff_engine.py。

共通の全体ロック、安定2回観測、公開BのSHA256、永続処理済み記録、共通日次上限、クラッシュ/失敗ブロック、質問停止、相手の公開完了判定を実装。DryRunは副作用なし。書きかけの完了時刻だけを根拠に動かさず、明示公開を要求する。投資本体のBやops質問を監視しない。

旧方式のcmd文字列連結・自動Git処理・未確認通知コマンド実行は本版に含めない。ログと状態はDropbox外に置く。認証済みエージェントの実行と公開手順をREADMEへ記録した。

## 検証結果

- Windows上の標準unittest **22件すべて通過、skipなし、2.569秒**。
- PowerShell 5.1の構文検査 **5ファイル通過**。
- 専用watchのcodex DryRun、claude Once DryRun、pingpong DryRun **3件通過**。起動先はtools/automationの専用B、状態先はC:\Users\s\.ai-trader\automation-handoff\13e7542ead2d199e。
- ローカルPythonの子プロセスで、日本語・!・&・空白・改行の標準入力/引数が文字どおり届くことを検証。
- タイムアウトで通常の子プロセスと孫プロセスが停止するWindows試験も通過。
- 模擬エージェントで両側交互実行→終了、失敗後の公開出力を次へ進めないこと、永続running後のクラッシュ停止、同じBの再送・日次上限・質問の旧mtime無視を検証。
- Codex CLIは検出、helpで承認never・workspace-write・stdin・output-last-message・-Cを確認。Claude CLIは未検出。モデルの実呼出し・ログイン試験は行っていない。

## 並行編集の競合と副作用

最初にtools/watch_handoff.ps1等を変更した後、Claude側から同ファイルへの更新が入り、2026-09-09 06:33:49の旧方式DryRunが実行された。出力からGit初期スナップショット `e30b06b watch: initial snapshot` とlogs/watch/state_dev_codex.json、promptファイル作成を確認。実AIは起動していない。処理を中断し、残存するwatch_handoff.ps1実行プロセスがないことを確認した。

これを受けて入口までtools/automation/へ分離し、そちらで改めて全検証した。並行編集の履歴とGit記録は削除・巻戻ししていない。旧tools/直下のスクリプトや登録タスクはClaude側の管理物として今回の正規入口に含めない。初期段階のtools/handoff_engine.py等は本版の起動に使用しない。

## 制限・次工程

常駐・タスク登録・実AI起動は未実施。まずClaudeがこの別枠で独立レビューする。完全自動で両CLIを回すにはローカルClaude Code CLIとログインが必要。Cowork担当ならPCはcodex監視だけを使用できるが、Cowork自体を自動起動する機能はない。

公開hashは「完成した入力を取り込む」ための整合検査であって、発行者認証や書き手間の排他ではない。複数の人/エージェントが同じ宛先Bを同時に編集する運用は禁止。手動アプリの作業はロックに参加しない。プログラムは要求ファイルと状態を検証するが、モデルが指示を守ることまで保証するものではない。

根拠・使い方は [README](README.md)。次は [Claude専用引き渡し](Claude引き渡しプロンプト.md)。投資本体の引き渡し文書は今回の自動連携工程では更新しない。
## 完了記録

終了時刻：2026-09-09 06:40 JST。専用両MDの公開マーカーを保存し、Claude宛てDryRunでpublished=trueを確認。モデル起動・常駐監視・タスク登録は未実施。CLIなし/認証未確認を稼働済みとは扱わない。
## 2026-09-09 Codex再検証・修正（最新版）

専用Bのdev/状態表示/質問待ちを確認し、登録入口も含めD01〜D09を修正。52件全通過5.978秒、skipなし。PS5.1構文6件と実フォルダDryRun4入口も通過。実タスク登録・実AI起動は未実施。詳細と採否はCODEX_REVIEW.md、導入手順の最新版はREADME.md。

C04はClaudeの1〜6段終端を採用し、コード枠外だけのB/公開判定へ補強。C05は現方式維持（fdはyield中も保持している）。クラッシュ時の残存ロックは手動確認後の除去で、PIDだけを根拠に自動削除しない。

常駐登録はDryRun/RegisterOnly/明示Replace/所有・稼働確認/終了コード保持を追加。自動再試行3回は廃止。共有状態は処理中も周期更新、要約・runtimeパスを共有しない。過去の「root開発Bを監視しない」「共有ファイルは一切なし」という初期専用チャネル説明は、現行auto/devと観測専用状態表示の範囲に置き換える。状態・ログ本体はDropbox外を維持する。

終了時刻: 2026-09-09 09:23 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、tools/automation/Claude引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、自動連携のWindows対応修正D01〜D09と常駐登録・状態表示の最終独立確認を行ってください。
```
