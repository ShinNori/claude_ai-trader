# 自動連携専用の引き渡し

## A. 固定プロンプト

これは自動連携の別枠作業です。投資台帳opsの実装・レビューと混ぜないでください。
編集範囲はtools/automation/とtools/tests/だけです。tools/直下のClaude側スクリプトと競合させません。
rootのCodex引き渡しプロンプト.mdやbuild-codex/Claude引き渡しプロンプト.md、投資用QUESTIONS.mdは変更しません。
最初にtools/automation/README.mdとtools/automation/IMPLEMENTATION.mdを読みます。
自分宛てMDは読むだけ。質問はtools/automation/QUESTIONS_<agent>.mdへ書き、相手のBは未更新で停止します。
完了時だけ相手の専用Bを更新し、「今回の依頼: …」を1行にします。次の作業がなければ「今回の依頼: 引き渡し不要（理由）」とします。
相手のB全体が完成してから python tools/automation/codex_engine.py publish --agent <相手> で公開します。
モデルの実審査・実口座・LINE操作は禁止。自動連携の試験は模擬CLIと合成作業フォルダを使います。
CLI連携が起動する開発エージェントと、投資シグナルの実審査LLM呼出しは別です。
終了時刻(JST)を次に渡すコピー用一文の直前に1回表示します。
## B. 今回の依頼

今回の依頼: 引き渡し保留（Claude制限解除まで両見張り停止。以下は再開後の依頼案）

最新のユーザー訂正により自動連携は保留。未公開の依頼案として保存する。QUESTION_REVIEW.md末尾を読む。Codexが質問マーカーより後の本文を未解決として保持、タイトル前置きの質問を保持、複数行コメント内のマーカーを無効化した。handoff_common.ps1のUTF-8設定でCP932捕捉を修正。全75件の両コードページ実測は同報告参照。

修正を独立確認し、全75件を模擬CLIで検証してCLAUDE_REVIEW.mdへ記録。旧auto/Codex宛て99fda205の依頼はCodexが実装済みなので重複実装を依頼しない。不具合がなければauto/CodexのBを「引き渡し不要（E01/E02/E05/E08修正を確認）」に更新して公開。追加不具合のみ具体的な新依頼にする。devやタスク登録には触れない。実投資審査・LINE・発注禁止。

## 過去の依頼

| 開始JST | 終了JST | 結果 |
|---|---|---|
| 2026-09-09 09:11 | 2026-09-09 09:23 | Codex: D01〜D09修正、Windows52件全通過、実タスク/実AI未起動。次はClaude最終独立確認 |
