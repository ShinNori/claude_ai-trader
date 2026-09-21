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

今回の依頼: 質問ブロック判定の反例 E01・E02・E05 と PS5.1 符号化 E08 の修正と回帰試験

Claude 独立確認（tools/automation/CLAUDE_REVIEW.md 末尾、2026-09-09 10:30 JST と 17:01 JST の 2 節）。
Windows 実機の実測: 受領版 66 件は chcp 65001 で全通過・skip 0（12.802 秒）で再現。反例を足すと 75 件中 4 失敗。
既定の日本語コンソール（chcp 932）では、さらに test_handoff_windows.py の 2 件が UnicodeDecodeError になる。

- E01（A）: 解決マーカーの下に見出しなしで追記された質問が、同じ ## ブロック内にあるため解決済みとみなされ飲み込まれる。
  10:01 の事故と同じ「マーカー付きブロックへの追記」が ## を付け忘れると再発する。
- E02（B）: 最初の ## より前の本文が全部「文書タイトル」として無視される。
- E05（C）: 複数行 HTML コメントの内側のマーカーが解決扱い。
- E08（B、新規）: register_watch_task.ps1:33-35 が engine の UTF-8 JSON を @(& $PythonExe …) で捕捉するため、
  [Console]::OutputEncoding（日本語 Windows の既定 CP932）で復号されて要約が文字化けし、記号の並びによっては
  ConvertFrom-Json が落ちる。この作業フォルダの現在の依頼で再現済み（-File … -DryRun が rc=1）。README の登録・保守
  手順が既定環境で実行できない。反例は tools/tests/test_ps_encoding_claude.py（2 件、子 PS の符号化を CP932 に固定）。
- E09（通過・番人）: watch と publish は engine 出力を捕捉せず素通しするため壊れない。E08 の修正で捕捉方式に変えない。

修正案（採否は Codex 判断）: E01/E02 はマーカーより上の本文だけを解決し、最後のマーカーより下に本文が残れば未解決。
最初の ## より前は # 見出し行と空行を除いて本文が残れば旧形式ブロック。E05 は複数行コメントを非構造行にするか、
マーカー行が同一行で開閉する単独コメントであることを要求する。ブロック終端・枠内無効・hash 診断・異なる hash の停止は維持。
E08 は捕捉前に [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) を置く（handoff_common.ps1 に集約すると
register_watch_task.ps1 の自前の python 呼び出しにも効く）か、engine 出力を一時ファイルへ書いて UTF-8 で読む。
test_handoff_windows.py の 2 件は同じ修正で既定コンソールでも通る。試験側の復号を OEM に緩める回避は採らない。

完了条件: test_question_sections_claude.py（7 件）と test_ps_encoding_claude.py（2 件）を含む全 75 件を、
chcp 932 と chcp 65001 の両方で Windows 実測して件数・秒数を記録し、QUESTION_REVIEW.md と README.md へ追記する。
そのうえで tools/automation/Claude引き渡しプロンプト.md の B を更新し publish --agent claude（次がなければ「引き渡し不要」）。
編集範囲は tools/automation/ と tools/tests/。dev チャネル（投資本体）と ACL・権限には触れない。

<!-- handoff-ready: 99fda2051cc1a010cc19ff372a25094f99162ea1cf888f9558fba52bdcdc3ccc -->

