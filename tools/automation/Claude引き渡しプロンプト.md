# 自動連携専用の引き渡し

## A. 固定プロンプト

これは自動連携の別枠作業です。投資台帳opsの実装・レビューと混ぜないでください。
編集範囲はtoolsの自動連携コード・試験・この専用ディレクトリと文書です。
rootのCodex引き渡しプロンプト.mdやbuild-codex/Claude引き渡しプロンプト.md、投資用QUESTIONS.mdは変更しません。
最初にtools/PINGPONG.mdとtools/automation/IMPLEMENTATION.mdを読みます。
自分宛てMDは読むだけ。質問はtools/automation/QUESTIONS_<agent>.mdへ書き、相手のBは未更新で停止します。
完了時だけ相手の専用Bを更新し、「今回の依頼: …」を1行にします。次の作業がなければ「今回の依頼: 引き渡し不要（理由）」とします。
相手のB全体が完成してから python tools/handoff_engine.py publish --agent <相手> で公開します。
モデルの実審査・実口座・LINE操作は禁止。自動連携の試験は模擬CLIと合成作業フォルダを使います。
CLI連携が起動する開発エージェントと、投資シグナルの実審査LLM呼出しは別です。
終了時刻(JST)を次に渡すコピー用一文の直前に1回表示します。
## B. 今回の依頼

今回の依頼: 自動連携専用スクリプトの独立レビューと模擬CLIによる停止条件の検証

Codexがwatch_handoff.ps1/pingpong.ps1を共通PythonエンジンへのPowerShell 5.1対応入口に置き換えました。
自動連携の実装と試験はtools/、引き渡しはtools/automation/の専用MD、実行状態とログはDropbox外です。
投資本体のops v0.3.6作業は別枠で、今回の自動連携レビューでは変更しないでください。

1. tools/handoff_engine.py、各ps1、tools/tests/test_handoff_engine.pyを独立レビューしてください。
   公開マーカー、Bの構文、2回観測と安定時間、全体ロック、処理済みhash、日次上限、質問/失敗ブロック、DryRun非変更を重点にしてください。
2. python -m unittest discover -s tools/tests -v を実行し、模擬CLIで追加反証をtools/testsへ作成してください。
   実AIを連鎖起動しないこと。実CLIの認証・実行確認は別の本番導入工程です。
3. コードに欠陥があれば再現・重大度・修正案をtools/automation/CLAUDE_REVIEW.mdへ記録してください。
   Codex側試験の保護条件を弱めないでください。Claude CLIが手元で利用できる場合は--helpまでの読取検証で引数を確認してください。
4. 完了時はtools/automation/Codex引き渡しプロンプト.mdのBだけに次の具体的な修正依頼を記入し、codex宛てとして公開してください。
   修正不要なら「今回の依頼: 引き渡し不要（独立レビュー完了）」にしてください。

完了条件：独立レビュー記録、模擬試験実測件数、未確認事項、次の自動連携専用Bの公開。