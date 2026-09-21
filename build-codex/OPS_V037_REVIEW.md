# ops v0.3.7 独立レビュー（第11回）

確認開始: 2026-09-09 06:58 JST（実機時計）。依頼文の発行時刻07:10は実機時計より未来だったため、作業開始時刻として転記しない。

## 結果

**430件中428通過・2失敗、11.43秒。skip/xfailなし。全件通過ではない。** O01修正は既存O系列・P系列および追加の通常境界で確認できた。残るQ09/Q10は、公開APIが受理する逆順の業務時刻と時点再生との契約の隙間（B）で、次のClaude依頼にする。

| 段階 | 実測 |
|---|---|
| 修正前の時計依存試験単独 | 1 failed in 0.09s |
| フィクスチャ1行修正後の既存全体 | 411 passed in 10.27s |
| 新規Q系列19ケース | 2 failed, 17 passed in 0.56s |
| 既存411＋新規19の最終全体 | 2 failed, 428 passed in 11.43s |

実行場所ops/、指定コマンド本体は `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`。実際は同梱Pythonの絶対パスとOS一時フォルダの試験ライブラリを使い、最終時には `--tb=short -p no:cacheprovider --basetemp <OS一時フォルダ>` を追加した。PYTHONDONTWRITEBYTECODE=1、PYTHONUTF8=1。実行台帳は合成一時台帳だけで、実口座・実LLM・LINE・常駐処理なし。

## 時計依存フィクスチャ

`common/tests/phase2/test_ledger.py` のledgerフィクスチャだけを変更した。

```diff
-    p=proposal(); l.create_notice(p)
+    p=proposal(); l.create_notice(p, at=AT)
```

ATは2026-09-08 08:00 JST。通知状態をAPPROVED/SENTへ進める時刻とも一致し、固定の翌06:50 JSTより前になる。実行時刻でcreated_atが変わる経路を除去した。変更前後の全文比較で、この1箇所以外に差がないことを確認した（すべてのassert・既存skip指定等は未変更）。翌朝判定だけでなく、既存411件すべてが通過し、期限切れ・部分約定・重複・訂正等の意味を維持している。

## 指摘表

| ID | 分類 | 再現と影響 | 対応案 |
|---|---|---|---|
| Q09 | B：日時契約の隙間 | 09:00保留40株→14:00作成時刻の通知targetへ10:00 APPLY→記録順では後から09:30のCORRECTIONを報告。訂正は受理され現在現金964000になるが、replay(09:30)は「訂正対象c1が見つからない」で停止。TRADEの参照で通知は補完されるが、10:00のAPPLYは時点外のため訂正元約定がない。ledger.py:560–565、849–881。 | 訂正の業務時刻と、対象が残高へ反映されるAPPLY時刻との制約を決める。前倒し訂正を拒否する案、または金銭効果を先取りしない再生契約を明示する案を比較し、旧履歴と再起動も整合させる。未来APPLY全体をそのまま適用してはいけない。 |
| Q10 | B：通知状態の日時契約の隙間 | 同じ遅着通知へ10:00 APPLY後、記録順では後から09:30 APPROVED・09:40 SENT・09:50 EXPIREを受理。replay(09:45)ではAPPLYが時点外でneededにtargetが入らず、NOTICE_STATEで「通知targetがありません」。ledger.py:357–361、849–881。 | 通知状態の業務時刻と通知作成/識別補完の関係を明文化する。状態参照も識別補完するなら予約0と金銭非先取りを維持し、EXPIREの複数IDも対象を限定する。状態を作成前へ遡らせる入力を禁止する案なら、その契約・互換と試験前提の変更を明示する。 |

どちらも現在残高への適用は成功しているが特定時点が再生不能。既知O01（確定APPLYそのものが有効な時点で通知が欠ける）は解消しており、その再発とは区別する。今回の変更で新しく混入した退行と断定しない。Q09は訂正時点にいくら表示すべきかという新契約を試験で勝手に決めず、受理した履歴の再生が例外で落ちる反証として残した。入力拒否を採用する場合は、Codex側へ新契約に基づく期待値更新を依頼すること。

該当試験は [test_ops_v037_review.py](tests/test_ops_v037_review.py)。失敗をskip/xfailにしていない。ops本体は読取のみ。

## 追加試験の網羅範囲

| ID | 件数 | 検証 | 結果 |
|---|---|---|---|
| Q01 | 6 | 通知時刻が解決前/同時/後×保留時刻あり/なし。40株部分APPLY、UTC/−05:00表記、解決前非先取り、残60株予約、再起動/seq別再生 | 通過 |
| Q02 | 2 | 同一通知に40＋60株の2保留APPLY、40＋70株の残数量超過拒否・seq/残高/保留不変 | 通過 |
| Q03 | 2 | APPLY後のCORRECTION→CANCELLED、およびCANCELLED→CORRECTION。取得単価900、40株保持、予約復活なし | 通過 |
| Q04 | 1 | APPLYが既に有効なcontext_only通知へAPPROVED/SENT/EXPIRE。通知作成前は予約0、作成時点から残60株予約60120 | 通過 |
| Q05 | 1 | 合成DBから通知だけを削除する故障注入。不明参照をLedgerError、再起動はMigrationErrorとして拒否 | 通過 |
| Q06 | 2 | cutover前/時刻なしTRADE保留のAPPLY迂回拒否、seq/保留/残高維持 | 通過 |
| Q07 | 2 | 正規migrate.applyで同じ部分APPLY履歴をマーカー前のrule_version2区間/後のrule_version3区間へ置き、時点残高とreplay_knownを比較 | 通過 |
| Q08 | 1 | DISCARD済み同IDを別通知へCSV再送。重複扱い、残高/seq不変、未来予約や新しい確定参照を作らない | 通過 |
| Q09 | 1 | 解決より前へさかのぼるCORRECTION | 失敗・B |
| Q10 | 1 | 解決前へさかのぼる通知状態変更 | 失敗・B |

Q04は通知作成前の予約非漏出を確認したものであり、通知状態そのものはLedgerViewの公開欄にないため、その全内部状態を観測したとは主張しない。Q05の直接SQLは欠損履歴の合成故障注入だけで、本番移行・本番履歴の編集ではない。

## 文書の照合

ops/README.md第11回の指定3点はいずれも実測と一致する。

1. 早い解決の拒否はledger_events/現在残高/保留を維持する一方、ingest_attemptsへREJECTED/VALIDATIONが残る。既存N02およびP07が最終全体で通過。
2. replayの戻り値LedgerViewにpending欄はなく、現在pending_rowsとは別。models.pyの公開型と一致する。
3. migrate --checkの逆順解決位置は、マーカーなしでexit0のviolation欄、マーカー後でexit1のMigrationError欄。既存O04の実CLI2ケースが最終全体で通過。

第10回の過去記録には「拒否は監査に残らない」等の古い表現が残っているが、第11回は明示して訂正している。最新契約は第11回を読むこと。今回、ops文書を直接編集していない。

親フォルダの改訂案v1.1へ(h)を追記し、確定APPLYの時点内参照を識別補完する採用契約を反映した。Q09/Q10の解決方法は未採用案として分け、共通仕様本文へ新契約を推測で書き込んでいない。

## 変更範囲・版

- 変更: common/tests/phase2/test_ledger.pyの1行、build-codex/tests/test_ops_v037_review.py、今回報告、README、Claude向けB/履歴、common/ISSUES.md追記、親の改訂案v1.1追記。
- 未変更: ops本体、ops/tests、共通仕様本文、AGENTS.md、自分宛てB、自動連携tools。
- ops/aitrader_ops/ledger.py SHA256（確認前後一致）: `C3CF13F3D1A3A22D79994DB308EECFF2E4D02DFC79519A79EF171E06B062E11F`。
- 次の担当: ClaudeによるQ09/Q10の日時契約と対応・独立再検証。依頼文書保存とCLI送信は別で、今回は送信していない。

終了時刻: 2026-09-09 07:07 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、build-codex/Claude引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、ops v0.3.7レビューのQ09・Q10（逆順業務時刻の訂正と通知状態変更）の契約決定・対応と独立再検証を行ってください。
```