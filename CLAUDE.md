# CLAUDE.md — Claude Code 用（ai-trader）

## 最新方針：担当分離・トークン節約（2026-09-16）

ユーザーの新指示により、[担当分担とモデル選択](build-codex/TEAM_WORKFLOW.md)を優先する。Codexは製品実装・修正・統合試験、Claudeは未決契約と独立境界試験・重要差分確認を担当。同一作業の二重実施・全件レビュー往復を止める。通常モデルを基本に難所だけ上位へ切替。サブエージェントは原則0、必要な独立作業だけ起動し、以前の「毎回最大3体」「Astra/Opus固定」より優先する。自動送信・起動・見張り再開は許可されていない。詳細は上記文書を参照し、以下の旧担当/モデル/並列方針との衝突は本節を優先。


このフォルダは Claude と Codex の並行ビルド。Claude Code はここでは **build-claude/ と tools/ のみ**編集する。
common/ は両者共通の土俵（編集禁止）、build-codex/ は Codex の担当。

## Codex への自動引き継ぎ

Codex に作業を渡すときは、まず `Codex引き渡しプロンプト.md` の「B. 今回の依頼」を今回の内容に書き換え（履歴表にも1行追加）、そのうえで次を実行する（Bash ツールで可）:

```
powershell -ExecutionPolicy Bypass -File tools\run_codex_build.ps1            # Windows
bash tools/run_codex_build.sh                                                 # Linux/macOS
```

- 追加指示は `-Task "..."`（PowerShell）/ 第1引数（bash）で渡す
- 終了後 `logs/codex_*_last.md` を読んで結果を要約し、`build-codex/QUESTIONS.md` があれば人間に見せる
- 両ビルドの比較: `build-claude/results/*/summary.json` と `build-codex/results/*/summary.json` を突き合わせ、README の解釈表で差分の原因を説明する

## 往復の自動化（tools/automation）— 開発ループとは別枠

正規実装は `tools/automation/codex_engine.py`（Python 標準ライブラリ）と PS5.1 入口。チャネルは `dev`（投資本体: `Codex引き渡しプロンプト.md` ⇄ `build-codex/Claude引き渡しプロンプト.md`）と `auto`（自動連携自身: `tools/automation/*引き渡しプロンプト.md`）。使い方は `tools/automation/README.md`、採否は `tools/automation/CLAUDE_REVIEW.md`。`tools/_deprecated/` は起動しない。

Claude Code / Cowork が相手宛て B を書いたら、最後の操作として `python tools/automation/codex_engine.py publish --channel dev --agent codex` で公開する（公開マーカーがないと Codex 側の見張りは起動しない）。B の先頭行は `今回の依頼: …` を 1 物理行だけ。自分宛て B は読むだけ。人間の判断が必要なら `ops/QUESTIONS.md` に書いて止まり、B は触らない。全体が完了したら `今回の依頼: 引き渡し不要（理由）`。自動連携の作業は auto チャネルで扱い、dev の B に混ぜない。

## 禁止

- 実口座・証券サイトへの接続、発注
- 秘密情報（.env、トークン）のコミット
- common/ の編集（不備は common/ISSUES.md に追記）
