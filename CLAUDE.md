# CLAUDE.md — Claude Code 用（ai-trader）

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

## 往復の自動化（ピンポン／監視方式）— 開発ループとは別枠

自動連携の作業（tools/ のスクリプト、Codex へのレビュー依頼）は `tools/自動連携_Codex依頼.md` で扱い、`Codex引き渡しプロンプト.md` の B（自動投資システムの開発）には混ぜない。

`tools/pingpong.ps1` は Claude Code と Codex を交互にヘッドレス起動し、`Codex引き渡しプロンプト.md` と `build-codex/Claude引き渡しプロンプト.md` の「B. 今回の依頼」を通信路にして往復する（詳細は `tools/PINGPONG.md`）。
Claude Code が pingpong から起動されたターンでは次を守る:

- B の先頭行は必ず `今回の依頼: …`。未完了のまま相手向け B を更新しない（更新＝バトンを渡す）
- 人間の判断が必要なら `ops/QUESTIONS.md` に書いて止まり、B は触らない
- 全体が完了したら相手向け B の先頭行を `今回の依頼: 引き渡し不要（理由）` にする
- 履歴表（引き渡しMD の「過去の依頼」）にも従来どおり行を足す

## 禁止

- 実口座・証券サイトへの接続、発注
- 秘密情報（.env、トークン）のコミット
- common/ の編集（不備は common/ISSUES.md に追記）
