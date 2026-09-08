# 自動連携（Claude⇄Codex 引き渡し自動化）— Codex への依頼【別枠】

このファイルは **自動投資システムの開発（Codex引き渡しプロンプト.md の B）とは別枠** の依頼。
則光さんが「自動連携の作業をして」と指示したときだけ、Codex に次の一文を渡す。通常の開発ループには混ぜない。
成果物は tools/（Claude 担当）と build-codex/PINGPONG_REVIEW.md（Codex 担当）。設計・使い方は tools/PINGPONG.md。

```text
ai-trader の作業フォルダで、tools/自動連携_Codex依頼.md を読み、Codex引き渡しプロンプト.md の「A. 固定プロンプト」の規則と、このファイルの「今回の依頼」に従い、自動引き渡し 2 方式（監視方式 tools/watch_handoff.ps1 ＋ ピンポン方式 tools/pingpong.ps1）の実装レビューと、Codex 側の運用規則への適合 を行ってください。
```

発行時刻: 2026-09-09 06:35 JST（別枠化: 06:55 JST、チャネル対応版に更新: 07:15 JST）

## 今回の依頼

```
今回の依頼: 自動引き渡し 2 方式（監視方式 tools/watch_handoff.ps1 ＋ ピンポン方式 tools/pingpong.ps1）の実装レビューと、Codex 側の運用規則への適合

背景: 引き渡しが手動（則光さんが一文を貼る）で手間がかかる。Claude が 2 本のスクリプトを作成した。
- 監視方式（本命）: 見張りが 1 分ごとに自分宛ての引き渡しMD の B を確認し、更新されていれば同じ一文プロンプトで CLI を起動する。
  片側ずつ独立して動くので、Claude 側を Cowork が担当し PC では -Agent codex だけを常駐させる運用ができる
- ピンポン方式: 1 本のスクリプトが両 CLI を交互に起動する（両方 PC で動かす場合）
先に tools/PINGPONG.md（仕組み・起動条件・停止条件・運用ルール）、tools/watch_handoff.ps1、tools/pingpong.ps1 の順に読むこと。

1. 実装レビュー（tools/ は Claude 担当のため編集しない。指摘は build-codex/PINGPONG_REVIEW.md に「指摘ID / 重大度 A,B,C / 事象 / 修正案」の表で書く）
   - 監視方式の起動条件 6 つ（B ハッシュ差分・更新後 StableSec 経過・引き渡し不要でない・QUESTIONS.md が B より古い・当日上限・ロック）で
     次の事故を防げるか: 書きかけ／Dropbox 同期途中の B を拾う、同じ B で二度起動、両側見張りの無限往復、人間の回答待ち中に起動、
     前回の作業が終わる前に次を起動。防げない経路があれば具体的に示す
   - B の先頭行「今回の依頼: …」を唯一の通信路にする設計の弱点（複数依頼・古い B の再実行・先頭行の書式ゆれ）と対策
   - PowerShell 5.1 での互換性（Start-Process＋cmd.exe /c によるリダイレクト、日本語パス、UTF-8 BOM、ConvertFrom-Json の型）。
     構文エラーがあれば行番号と修正を示す。PowerShell が実行できるなら `-Agent codex -DryRun` と `-Once -DryRun` を実行し、出力を貼る
   - `codex exec` の起動オプション（--sandbox workspace-write、`-` による標準入力プロンプト、--output-last-message、-C）が
     あなたの環境で正しく動くか。動かない場合は正しい呼び出しを示す。無人実行で承認プロンプトが出て止まる経路がないか
   - 見張りが Codex 自身を起動した際、Codex がこのファイル（自分宛て B）を編集して再起動を誘発しないか（自分宛て B は読むだけ、の規則で足りるか）

2. 運用規則への適合（build-codex/ 内のみ編集）
   - build-codex/Claude引き渡しプロンプト.md の「今後の更新ルール」に tools/PINGPONG.md §運用ルール の 5 点
     （B 先頭行の形式・未完了時は B を更新しない・質問は QUESTIONS.md に書いて止まる・完了時は「引き渡し不要」・履歴表）を追記
   - AGENTS.md への追記案があれば PINGPONG_REVIEW.md に書く（AGENTS.md 本文は Claude が反映する）

3. Codex引き渡しプロンプト.md / build-codex/Claude引き渡しプロンプト.md の B は **変更しない**（自動投資システムの開発ループとは別枠）。
   報告は build-codex/PINGPONG_REVIEW.md の末尾に「報告の末尾」テンプレートで書く。
   Claude に次の作業（スクリプト修正など）を渡す場合だけ tools/自動連携_Claude依頼.md の「## 今回の依頼」を書き換える
   （先頭行は「今回の依頼: …」。渡す作業がなければ「今回の依頼: 引き渡し不要（理由）」のまま）
4. 追加レビュー対象（06:55 以降の変更）: watch_handoff.ps1 にチャネル（dev / auto）を追加し、依頼セクションの見出しが
   「## B. 今回の依頼」と「## 今回の依頼」の両方に対応した。tools/start_watch_codex.cmd（常駐起動）、
   tools/register_watch_task.ps1（タスクスケジューラ登録）も対象。pingpong.ps1 L193 の `$agent:` は `${agent}:` に修正済み

完了条件: build-codex/PINGPONG_REVIEW.md（指摘表と採否の提案、DryRun 出力）、Claude引き渡しプロンプト.md の更新ルール追記。
報告は「報告の末尾」テンプレートに従う。
```

## 履歴

| 日時 | 内容 | 結果 |
|---|---|---|
| 2026-09-09 06:35 | 2 方式の実装レビュー依頼（当初は開発ループの B 第10回として発行。Codex が 06:24 に review_handoff.ps1 / PINGPONG_EVIDENCE.txt で検証開始） | 06:55 別枠へ移動。pingpong.ps1 L193 の `$agent:` は Claude が `${agent}:` に修正済み |
