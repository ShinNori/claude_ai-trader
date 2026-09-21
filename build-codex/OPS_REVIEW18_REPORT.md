# 第18回レビュー対応（2026-09-12）

**最新確定：2026-09-12 07:56 JST、2230 passed / 9 skipped / 562 warnings、369.25秒、終了コード0。** 対象はcommon/tests全体・build-codex/tests・ops/tests。前回の指定範囲にフェーズ1受入8件を加え、新規混在時刻4件も含む。以下の2 failedは期待値更新前の履歴で、現在の失敗残件ではない。進行中の試験はない。公式使用4%・残96%、上限20%。

最終実行：プロジェクト直下、PYTHONPATH=ops;build-codex、bundled Pythonで `-m pytest common/tests build-codex/tests ops/tests -q -p no:cacheprovider --basetemp C:\Users\s\AppData\Local\Temp\ai-trader-review18-timeorder-final-20260912 --tb=short --disable-warnings`。途中で中断した実行を成功件数に合算していない。

## 後続：Codexが残件を引き継ぎ（07:47以降）

ユーザーのCodex継続指示を受け、W01a/bの未来操作の戻り値assertだけをINVALIDへ更新した。後続の実STOP/RESUMEと停止状態のassertは保持し、2行以外の既存試験は変更していない。Claude原案に対するCodexの期待値更新であり、Claudeによる再レビュー済みとは表記しない。既存Claude11件と追加14件の計25件が1.08秒で成功した。以下の「2 failed」は更新前の履歴。

後続独立レビューで、保存制御時刻のSQL MAXが文字列順であり、UTC/JST混在時に遅着STOPを現在の操作と誤ることを再現した。読取stop_statusでは同じ時刻表記を受容するため、保存形式の差だけで制御順が変わらないよう日時として比較する。既存の未来履歴の自動修復を追加するものではない。再現試験追加後、途中の全体実行は中断し、修正完了後に再実測する。

修正：_latest_control_event_atは保存された非NULLのevent_atを既存_jstで解析し、datetimeの最大値を求める。NULL履歴の従来扱い、遅着の監査、同時刻は維持する。異常な文字列やtimezone欠損は既存検証で拒否し、履歴を自動書換えしない。新規test_control_time_followup.pyの4件が0.19秒で成功。UTC表記の最新STOPに対する遅着RESUMEと、最新RESUMEに対する遅着STOPの両方、およびnaive時刻2件を検証した。

W02の主系統合も独立確認した。enqueue_prepared_notificationsは返された旧keyの行を予定カードと照合し、別key衝突ではRunErrorでqueue receiptを書かない。mock_deliveryも予定key/本文を照合する。既存統合試験と新規W02試験の7件が3.20秒で成功したため、同等の試験を重複追加していない。

対象はCodex引き渡しプロンプト.mdの最新B。Claudeの独立レビューを受け、修正と今回の検証はCodexが行う。共通仕様、既存受入、Claude試験は変更しない。実通信・見張り・Claudeの起動/公開は行わない。

## W01：直接制御の未来時刻

直接Notifier.stop/resumeで、JST正規化後のevent_atがnowを超えた場合はINVALIDを返す。controls・kv・queue・台帳を変更せず、未来の時刻で最新制御時刻を進めない。遅着の監査とIGNORED、同時刻、時刻省略の既存動作は維持する。Webhookの既存INVALIDと、読取stop_statusの未来履歴UNKNOWN判定に整合する入口検査である。既に保存された不正履歴を自動修復する変更ではない。

### 既存Claude試験との前提差

`ops/tests/test_v041_resume_review_claude.py` のw01aは未来RESUMEがRESUMEDになることを途中でassertし、w01bは未来STOPにIGNOREDまたはSTOPPEDを要求する。未来操作をINVALIDとして拒否する契約と両立しない。試験を削除・skip・xfail・書換えせず、実測でその箇所だけ失敗する場合はB（テスト前提の誤り）として記録する。w01c/dの安全性の結論と、新しい三経路回帰で実装修正を検証する。全件成功と表記しない。

## W02：採用（追加応答で識別）

同じ候補kind/proposal_idで別key、かつ内容hashが異なる再登録の場合だけ、従来の旧key/state/duplicate=Trueにchanged=Trueを追加する。完全重複と初回登録の応答形状、同keyで内容変更した場合のValueErrorは維持する。DB schema・既存本文・期限・状態・retry key・月枠は変更せず、新規行や再送も作らない。

理由：無言で訂正内容が失われることを呼出側で識別しつつ、二重通知防止と既存呼出しの互換性を維持する。changedは「保存内容を変更した」の意味ではなく、「再登録要求の内容が既存と異なった」の意味である。呼出側が確認して訂正方針を決める材料であり、自動訂正通知の新契約は採用しない。NEWとEXITはkind別の既存重複範囲を維持する。

## 実測

指定全体実測は **2216 passed / 2 failed / 9 skipped / 562 warnings、347.37秒、終了コード1**（2026-09-12 07:37 JST）。失敗はW01a/bだけで、実際の差はそれぞれINVALID対RESUMED、INVALID対(IGNORED, STOPPED)。上記のB分類と一致する。他の実装不具合Aはこの実測では検出しなかった。全件通過ではない。既存の9 skippedを新設・増加させていない。

Claude側で環境要因だったtest_r20も今回の対象に含み、失敗していない。主系・ops・共通phase2を収集できたが、common/tests直下のフェーズ1受入8件は指定コマンドの対象外である。実接続・実プロセス同時書込・電源断・不正な既存未来履歴の修復は未検証。

W01a/bの期待値更新は担当者への次回依頼事項として残す。未来操作の戻り値だけをINVALIDへ合わせ、後続の本物のSTOP/RESUMEの安全性assertは維持することを提案する。変更やClaudeへの自動公開は行っていない。

使用量：リセット後3%・残97%、上限20%。新規試験14件は全件成功。進行中の試験はない。

個別実測：新規三経路回帰8件成功、W02のNEW/EXIT契約6件成功（0.52秒）。Claudeの11件は9件成功・2件失敗（W01a/bの上記B）。W03〜W08の6件、W01c/d、W02は成功した。新規追加は計14件で、Claude試験は変更していない。

指定全体コマンドはops/から以下で開始した。結果は終了後に追記する。前回2201 passedは今回のClaude追加11件を含む結果ではなく、対象もcommon/tests全体とphase2だけで異なる。

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader\ops'
$env:PYTHONPATH="$PWD;$PWD\..\build-codex"
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest ../common/tests/phase2 ../build-codex/tests tests -q -p no:cacheprovider --basetemp C:\Users\s\AppData\Local\Temp\ai-trader-review18-full-20260912 --tb=short --disable-warnings
```
