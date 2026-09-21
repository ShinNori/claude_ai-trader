# 自動連携 — Codex への依頼【別枠・旧】

> **2026-09-09 07:40 JST**: このファイルは役目を終えた。自動連携チャネルの引き渡しは `tools/automation/Codex引き渡しプロンプト.md` / `tools/automation/Claude引き渡しプロンプト.md`（公開は `codex_engine.py publish`）で行う。以下は経緯記録。


このファイルは **自動投資システムの開発（Codex引き渡しプロンプト.md の B）とは別枠** の依頼。
則光さんが「自動連携の作業をして」と指示したときだけ、Codex に次の一文を渡す。通常の開発ループには混ぜない。
成果物は tools/（Claude 担当）と build-codex/PINGPONG_REVIEW.md（Codex 担当）。設計・使い方は tools/PINGPONG.md。

```text
ai-trader の作業フォルダで、tools/自動連携_Codex依頼.md を読み、Codex引き渡しプロンプト.md の「A. 固定プロンプト」の規則と、このファイルの「今回の依頼」に従い、自動引き渡し 2 方式（監視方式 tools/watch_handoff.ps1 ＋ ピンポン方式 tools/pingpong.ps1）の実装レビューと、Codex 側の運用規則への適合 を行ってください。
```

発行時刻: 2026-09-09 07:45 JST（第2回。第1回 06:35 は 06:29〜07:30 に Codex 完了: PINGPONG_REVIEW.md H01〜H18）

## 今回の依頼

```
今回の依頼: 監視方式 v2（tools/watch_handoff.ps1、H02〜H17 反映版）の再レビューと、tools/handoff_engine.py（tools/automation 方式）との統合方針の提案

背景: 第1回レビュー（build-codex/PINGPONG_REVIEW.md、H01〜H18）を受け、Claude が watch_handoff.ps1 を v2 に書き直した（07:40 JST）。
変更点は tools/PINGPONG.md「v2 の安全策」表と watch_handoff.ps1 冒頭コメントを参照。pingpong.ps1 は無人運用から外した（手動検証用に残置、H01 は修正済み）。
また tools/ に handoff_engine.py / handoff_common.ps1 / publish_handoff.ps1 / handoff_status.ps1（tools/automation/ と
<!-- handoff-ready: hash --> マーカーを使う別方式）が 06:29 JST に置かれている。作成者・意図が Claude（Cowork）側に伝わっていないので、
あなたが作成したなら設計意図と v2 との関係を説明し、そうでなければ「作成者不明」と記す。

1. v2 の再レビュー: H02〜H18 の各項目について「解消 / 一部解消 / 未解消」を判定し、未解消・新規の問題は同じ表形式（指摘ID J01〜、重大度 A/B/C）で
   build-codex/PINGPONG_REVIEW.md の末尾に追記。特に確認したい点:
   - 共通ロック（FileMode.CreateNew＋保持）と、取得後の依頼再読取で H03 の競合が閉じているか
   - 安定観測（同一 hash を StableSec 間隔で 2 回＋mtime）で H05 の経路がどこまで閉じるか
   - Get-Request の見出し・コードフェンス処理（H07）。「保留中の依頼」節や複数「今回の依頼:」行の扱い
   - HALT / processed 履歴 / 累計上限（H08〜H10）の状態遷移に抜けがないか。HALT 中に相手側 watcher が動く経路
   - cmd.exe /d /s /c "…" の引用と、絶対パス解決した codex.cmd / claude.cmd の起動（H15）。PS 5.1 で ParseFile と -DryRun を実行し結果を貼る
   - QUESTIONS.md の「回答済」文字列判定（H06 の代替）の妥当性
2. 統合方針: v2（PowerShell 単体）と handoff_engine.py（Python＋ready マーカー）のどちらを採用するか、または統合案を提案。
   採否の観点: 排他の確実性、依頼 ID/ready の明示性、依存（Python の有無）、Cowork が Claude 側を担当する運用との相性、保守性
3. build-codex/Claude引き渡しプロンプト.md の「今後の更新ルール」を v2 の規則（自分宛て依頼は読むだけ、「今回の依頼:」は 1 物理行・1 件、
   公開＝相手ファイル更新は最後の操作、質問は QUESTIONS.md、完了は「引き渡し不要」で始める）に合わせて更新
4. Claude に依頼したい修正があれば tools/自動連携_Claude依頼.md の「## 今回の依頼」に 1 件だけ書く（なければ「引き渡し不要」のまま）。
   Codex引き渡しプロンプト.md / build-codex/Claude引き渡しプロンプト.md の B は変更しない

完了条件: PINGPONG_REVIEW.md 末尾に第2回の判定表と統合方針、DryRun/ParseFile の実測、更新ルールの反映。報告は「報告の末尾」テンプレートに従う。
```

## 履歴

| 日時 | 内容 | 結果 |
|---|---|---|
| 2026-09-09 06:35 | 第1回: 2 方式の実装レビュー（Codex） | 07:30 頃完了。PINGPONG_REVIEW.md H01〜H18（A 10 件）。無人常駐は修正まで不採用 |
| 2026-09-09 07:40 | （Claude）watch_handoff.ps1 v2: H02〜H17 反映、pingpong.ps1 を無人運用から除外 | tools/PINGPONG.md「v2 の安全策」 |
| 2026-09-09 07:45 | 第2回: v2 再レビュー＋handoff_engine.py との統合方針（Codex） | （実行待ち） |
