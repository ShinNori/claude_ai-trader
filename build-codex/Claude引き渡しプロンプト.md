# Claudeへの引き渡しプロンプト

> 最新：人工束保存API/CLI実装完了。今回Bは重要差分の独立確認1点。旧の未公開/契約作成依頼より本Bを優先する。

> 最新：第13回CLIは完了。今回Bは保存契約だけの別依頼（未公開）。旧ops作業・CLIレビューは再実行しない。担当/数値上限はTEAM_WORKFLOW.mdを優先する。

> 2026-09-17 最新B：CLI重要境界だけの依頼。最新Codex宛てBの公開指示に基づき本Bを有効とする。以下の旧停止/ops担当の一般記述より本BとTEAM_WORKFLOW.mdを優先する。

> 運用変更（2026-09-11 04:25 JST、ユーザー指示）：ここからの開発はCodex単独で継続する。以下のA/Bは過去の引き渡し記録として保持し、新規依頼として実行しない。Claudeの利用制限中は回答・レビューを待たず、Codexが必要な修正・検証を担当する。ユーザーの連携再開指示まで引き渡し不要。詳細は ../AGENTS.md 冒頭。売買シグナルの二重承認契約は変更しない。

最終更新：2026-09-09。今回の対象：ops v0.4.0独立レビューのA指摘修正・V10契約判断。

## 毎回の使い方

Claude Code／Coworkで下記の作業フォルダを開き、次の一文を渡してください。

> ai-trader の作業フォルダで、build-codex/Claude引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、〈今回の依頼の一行要約〉を行ってください。

〈今回の依頼の一行要約〉は B の先頭行と同じ文。Codex は作業報告の末尾で、この一文を下の「報告の末尾」テンプレートどおりに示す。

作業フォルダ：

```text
D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader
```

ファイル参照ができない場合は、以下のAとBを続けてコピーする。今回このファイルを作成しただけで、Claudeを自動起動したり、依頼を送信したりはしていない。

## 報告の末尾（Claude・Codex 共通テンプレート。2026-09-08 22:20 統一）

作業報告の最後は、必ず次の形で終える。順番・体裁を変えない（時刻は一文の**直前に 1 回だけ**。末尾に重ねて書かない）。
一文はコード枠（```text）に入れ、則光さんがそのままコピーできるようにする。

終了時刻: YYYY-MM-DD HH:MM JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、〈Codex引き渡しプロンプト.md｜build-codex/Claude引き渡しプロンプト.md〉を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、〈一行要約〉を行ってください。
```

- Claude → Codex に渡すときは `Codex引き渡しプロンプト.md`、Codex → Claude に渡すときは `build-codex/Claude引き渡しプロンプト.md` をファイル名に入れる
- 〈一行要約〉は相手側 B の先頭行「今回の依頼: …」と同じ文にする
- 履歴表の行にも開始・終了の時刻（JST）を残す

## A. 固定プロンプト

あなたはai-traderプロジェクトのClaude担当です。作業フォルダのAGENTS.mdを読んで担当範囲を確認し、以下のBを今回の依頼として進めてください。AGENTS.mdが参照するCodex向け依頼を、そのままClaudeの実装指示として実行しないでください。

まず設計書の最新版と共通仕様を確認し、次の順番で今回の成果物を読んでください。

1. `build-codex/README.md` 末尾の最新結果・失敗分類。
2. `build-codex/PHASE2_OPS_REVIEW2.md` の5項目への回答。
3. `common/ISSUES.md` の最新追記と、それに関連する共通仕様。
4. `common/tests/phase2/test_ops_rereview2.py` および既存のフェーズ2テスト。
5. `build-codex/PHASE2_INTEGRATION.md` の主系組み込み案。

役割分担は、Claudeがops/とbuild-claude/、Codexがbuild-codex/と共通受入テストの作成・再レビューです。今回Claudeが修正するのはops/です。Codex成果物・共通受入テスト・共通仕様・合成データは変更しないでください。仕様の不足・衝突はcommon/ISSUES.mdに追記し、既存記録を消さないでください。

実口座への接続・発注・LINE送信・実審査用LLM呼び出しは行いません。実行DB・ログはDropbox外に置きます。合格させるためのテスト削除、skip、xfail、条件緩和はしないでください。

通常の実装・調査・再現はユーザーへ仲介を依頼せず進めてください。契約変更が必要な項目は、現契約の事実、変更案、影響を文書化し、変更不要の修正を先に完了してください。判断できない事項を推測で既存仕様へ書き込まないでください。

完了時は `ops/Claude対応結果.md` と `ops/README.md` を更新してください。各指摘IDの対応、変更ファイル、実際の実行コマンド、テストの通過・失敗・skip件数、残課題、Codexに再レビューしてほしい点を明記してください。Codex側への次の具体的な依頼は既存の `Codex引き渡しプロンプト.md` のBへ記載してください。

## B. 今回の依頼

今回の依頼: EXIT生成v6実装の重要差分を独立確認する

TEAM_WORKFLOW.md優先。今回Bはv6の実装完了後の独立確認1件。旧の不採用・入力元修正依頼は終了。製品修正はCodex担当、git・ダッシュボードはClaudeが代行する。

【変更ファイル一覧】
build-codex/aitrader/packet.py / 16598 bytes / 2026-09-28T09:16:05+09:00 / 追加のみ: EXIT生成・切上げ指値・合算配分
build-codex/aitrader/exit_holdings.py / 11577 bytes / 2026-09-28T09:18:02+09:00 / 新規: 明示入力・candidates読取・前提条件・ロット導出/照合
build-codex/tests/test_exit_generation_v6.py / 33000 bytes / 2026-09-28T09:19:36+09:00 / 新規33試験: 反証1〜45と時刻/FIFO/分割の回帰
build-codex/tests/fixtures/exit_v6_expected.json / 509 bytes / 2026-09-28T09:18:03+09:00 / 改訂固定例200株のhashと反証番号一覧
build-codex/EXIT_GENERATION_CONTRACT_DRAFT.md / 60696 bytes / 2026-09-28T09:21:11+09:00 / 末尾v6採用・API・検証・最終hashの記録
build-codex/README.md / 206410 bytes / 2026-09-28T09:21:32+09:00 / 末尾v6完了範囲・結果・次担当
build-codex/Claude_Opusキャッチボール.md / 10138 bytes / 2026-09-28T09:21:32+09:00 / v6開始/終了・結果・次担当の履歴
レビュー対象は上記最終ローカル作業ツリー。gitは今回指定で未実行。製品/試験4ファイルのSHA-256は契約末尾「v6実装確定・Windows実測」の表に固定。残る3ファイルは運用記録。依頼hash: f933da63379c141869f7e70f32de33b436e3a7c79616d772245da67e931eb79f。
依存参照: runner.pyのjournal schema/INTENT→通知→APPROVED保存（347〜350、462〜474）、Ledger.proposal/notice/view/unconfirmed/seq、SPLIT/CORRECTIONの公開契約。ops/common/runner/既存試験/examples/自分宛てMDは無変更（開始時231ファイルhash一致）。

【Codex 実測】
2026-09-28 / D:/work/ai-trader / Windows 11 10.0.26200、Python 3.12.14、PowerShell。
環境変数 PYTHONDONTWRITEBYTECODE=1、PYTHONIOENCODING=utf-8、PYTHONPATH=ops;build-codex。DBはTEMP（Dropbox外）。
初回関連一括: build-codex/tests・common/tests/phase2・ops/tests内test_*.pyで名前にpacket/gate/ledger/runner/exitを含む既存27ファイル＋新規EXIT（当時26試験）。python -m pytest <選択28ファイル> -q -ra -p no:cacheprovider --basetemp <TEMP専用ディレクトリ>。409 passed / 0 skipped / 0 failed / 44.27秒。補強前の結果であり最終版全体とは扱わない。
最終版: python -m pytest build-codex/tests/test_exit_generation_v6.py build-codex/tests/test_exit_pipeline.py build-codex/tests/test_exit_v4_external_contract.py -q -ra -p no:cacheprovider --basetemp "$env:TEMP\exit_v6_final_0920"。41 passed / 0 skipped / 0 failed / 3.57秒（新規33＋既存8、skip理由なし）。局所の中間実測は契約末尾に別記、合算しない。全体試験は未実施（独立API追加のため関連検証に限定）。
改訂固定例qty200のpacket_hash: 3997d6f7c54aae594c7caf1036c93904633fbeaf662df77fa5f773362f18bbe7。

【今回 Claude に求める判断】
1. EXIT生成v6の重要差分を独立確認する。特に明示snapshot/adjustments＋読取専用candidatesという入力境界から、INTENT/EXTERNAL/未決SELL・残高照合・seq検査・ロット配分/分割を経て候補数量を作る経路が、採用契約を保っているかを確認する。改訂固定例と反証1〜45の試験も参照する。指摘は根拠・最小反例・影響を示し、製品や既存試験期待値は変更しない。再実測は指摘に直結する1点のみ（全体の繰返し不要）、必要な新規反証は独立ファイルtest_*_claude_contract.pyに限定する。

初期保有の取得日推定、runnerへの保存/生成接続、実データ/実審査/LINE/証券/発注、T1違反の相殺欠落検出は今回の検証範囲外として返す。上位設計書はCodex環境の指定相対パス/本作業ツリーに無く未読であり、既存コード・共通仕様・採用契約に基づく実装である。実接続の承認や案件全体完了ではない。
編集範囲: 独立確認記録・新規専用反証・Claude担当の結果/引継ぎ文書。製品/ops/common/runner/既存試験期待値/examplesは変更しない。人間の追加判断不要、QUESTIONS無変更。完了後は具体的な指摘の修正依頼、または独立確認結果と次の未接続工程をCodex宛てBへまとめる。
履歴: 開始時刻未取得（最初の時計確認2026-09-28 09:09:32 JST、依頼発行09:06） / 終了 2026-09-28T09:22:14+09:00 / v6採用・実装・Windows検証完了、独立確認は未実施。
更新: 2026-09-28T09:22:14+09:00

<!-- handoff-ready: f6b7de5f72e950cfd8b05912374fc87d291666782dd379aef974ef007ec74347 -->

## 過去のB（実行対象外）

今回の依頼: ops v0.4.0独立レビューのA指摘修正とV10契約判断、追加49試験を含む再検証

先にbuild-codex/OPS_V040_REVIEW.md、build-codex/共通仕様_フェーズ2_順序4_修正提案_v0.2.md、build-codex/tests/test_ops_v040_review.py、README末尾、common/ISSUES.mdの第16回追記を読む。

今回のCodexデスクトップ許可済みユーザー環境では基準633 passed（18.95秒）。追加49件後は682件＝667 passed / 15 failed（17.13秒）、既存633維持、skip/xfailなし。追加失敗はA13ケースとB2ケース。別Codex子CLIのpytest一時フォルダエラーはQUESTIONS.mdに残り、今回解決していない。環境停止と実装指摘を混同せず、実行可能なClaude環境で次の修正・検証を行う。

1. A指摘を修正する。V01（3件）全ログ経路の秘密値マスク、V02 SENDINGの回復・警告・予算保持、V03予算枠とclaimの原子性および古い行情報による状態巻戻し防止、V04期限切れUNKNOWNの保持、V05retry失敗時も既存UNKNOWNを未送信扱いしない、V06旧月の日次通知の失効、V07遅着STOP/RESUMEの順序、V08（2件）webhookの型崩れをINVALIDへ畳み込む、V09非ASCII署名を401、V13IDNによる会員ホスト禁止回避。詳細な再現・修正方向はレビュー表を優先する。実秘密・実ネットワークは使わない。
2. V10（2件、B）の未来時刻STOP/RESUME拒否案を判断し、採否と理由・影響を明記する。STOPだけ受信即時優先等の例外を採るならその契約を明示し、Codex試験を無断で変更しない。
3. ops/testsへ独立回帰試験を追加する。並行予算は集計値だけでなく実際の合成送信呼出回数を確認。SENDING/UNKNOWNの各クラッシュ点、月境界・再起動・遅着制御を検証。共通受入テストやCodex試験の削除・skip/xfail・条件緩和は禁止。
4. ops/でpython -m pytest ../common/tests/phase2 ../build-codex/tests tests -qを実測。ops/README.mdとClaude対応結果.mdに指摘別結果、件数、残課題を記録。common/ISSUES.mdは追記のみ。完了後にCodex宛てBへ具体的な独立レビュー依頼を一件記載し、publish --channel dev --agent codexで公開する。

編集範囲はops/、common/ISSUES.md追記、Codex宛て依頼と履歴。主系・共通仕様本文・既存受入テストは変更しない。仕様案v0.2のLedger.proposal/Notifier.close/reconcile_sentとcli/line拒否の説明も照合。自動連携autoのE系列、子CLIの権限修復・監視解除は別枠。QUESTIONS.mdの未回答項目を自己解決しない。実口座・LINE・審査LLMは呼ばない。

完了条件: A指摘の修正と独立試験、V10の採否、指定全体の実測、文書更新、Codexへの次依頼公開。全件通過は実測が通った場合だけ記載する。

<!-- handoff-ready: 5b000242c5688a3d1426d6665b8be0783591a1d434d7b59b221bbd4f72a15b57 -->

## 今後の更新ルール（Codex向け）

ユーザーの依頼により、このファイルを毎回の引き渡し先として使う。今後Codexが作業を完了する際は、Aを共通の制約として維持し、Bをその時点の最新依頼に更新する。今回の数字やR番号を次のフェーズへ無条件に引き継がない。

更新項目は、現在フェーズ、実際に完了した作業、正確なテスト件数と結果、未完了事項、Claudeの編集範囲、次の具体的な作業、完了条件、参照文書。古い結果はREADME等へ履歴として残し、Bには最新の依頼を一つだけ置く。

README末尾にこのファイルへのリンクを保ち、最終回答でもリンクを案内する。ファイル更新とClaudeへの送信・起動は別の操作なので、送信していない場合に「引き渡し済み」と書かない。

### 自動引き渡し時の更新ルール（2026-09-09追記）

tools/PINGPONG.md「運用ルール」の5点を、Codex側にも適用する。

1. Bの先頭の依頼行は必ず `今回の依頼: …` の形で、1物理行・1件だけ置く。複数工程はその後の本文へ記載する。
2. 未完了のまま相手向けBを更新しない。更新は相手への作業公開を意味するため、必要な検証・成果物・報告の保存を先に完了する。
3. 人間の判断が必要な質問は自分側の `build-codex/QUESTIONS.md` に書いて止まる。相手向けBを変更しない。ファイル時刻から回答済みと推定しない。
4. 全体が完了した場合は、相手向けBの先頭行を `今回の依頼: 引き渡し不要（理由）` にする。
5. 引き渡しMDの「過去の依頼」にも開始・終了JSTと結果を記録する。`tools/pingpong_history.md` は機械側の別記録であり、手動履歴の代わりにはしない。取得していない開始時刻は未取得と書く。

自分宛ての `Codex引き渡しプロンプト.md` のBは原則読取専用。自動連携は `tools/自動連携_Codex依頼.md` の別枠となったため、自動連携作業では開発ループの両Bを変更しない。[PINGPONG_REVIEW.md](PINGPONG_REVIEW.md) の指摘は [自動連携専用のClaude依頼](AUTOMATION_CLAUDE_HANDOFF.md) で管理し、opsの次回依頼に混ぜない。規則の追記だけでランチャーの適合を保証せず、対象版を特定した再検証を必要とする。

### 完了報告の必須表示（2026-09-09 指定の統一テンプレートを優先）

時刻は実際の現在時刻からJSTへ変換する。報告の最後は次の形。時刻はコピー用一文の直前に1回のみ。ファイル保存は相手への送信を意味しない。

終了時刻: YYYY-MM-DD HH:MM JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、〈相手向け引き渡しMD〉を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、〈Bの今回の依頼と同じ一行要約〉を行ってください。
```

次の作業がない場合は引き渡し不要と明記する。履歴には開始・終了を記録し、未取得の開始時刻を推測しない。

## 過去の依頼（開始・終了JST）

| 開始 | 終了 | 依頼 | 結果 |
|---|---|---|---|
| 2026-09-09 06:29（確認開始） | 2026-09-09 06:40 | 第10回 ops v0.3.6指定修正確認・独立レビュー追補 | 402件中401通過・O01の1失敗。次はClaudeのO01修正・文書明確化・再検証 |
| 2026-09-09 06:58 | 2026-09-09 07:07 | 第11回 ops v0.3.7独立レビュー・時計依存フィクスチャ修正 | 430件中428通過・Q09/Q10の2失敗。次はClaudeの契約決定・対応 |

| 2026-09-09 08:14（最初の時計取得） | 2026-09-09 08:21 | 第12回 ops v0.3.8独立レビュー・Q09更新・改訂案(i)(j) | 457件中453通過・T06/T07の4失敗。次はClaude対応 |

| 2026-09-09 08:52 | 2026-09-09 08:56 | 第13回 ops v0.3.9独立レビュー・T07更新・改訂案(k) | 487件全件通過。次はClaudeの文書明確化・最終照合 |

| 開始(JST) | 終了(JST) | 作業 | 結果 |
|---|---|---|---|
| 未取得 | 2026-09-09 11:18 | 第16回ops v0.4.0独立レビュー（デスクトップ） | 682件中667通過・15失敗、追加49。次はClaudeのA修正・V10判断。子CLI環境質問は未解除 |



## 退避したB（CLI差分確認の前）

### 退避した旧依頼

今回の依頼: 引き渡し不要（第12回受領・Windows関連79件全通過・記録完了、追加判断なし）

今回のdev終了通知。Aの過去ops修正・全体再試験は実行しない。

【変更ファイル一覧】
build-codex/README.md / 200230 bytes / 2026-09-28T08:47:48+09:00 / 復旧・引継ぎ記録
build-codex/Claude_Opusキャッチボール.md / 5950 bytes / 2026-09-17T14:31:50+09:00 / 第12回完了・開始終了履歴
運用文書のみ変更。製品・既存試験・Claude新規試験・契約§3-3は変更なし。一次資料はbuild-codex/Claude_Opusレビュー_証拠履歴_第12回.mdの結論・R12-01・対象hash。製品2ファイルと指定試験3ファイルのSHA256は同報告と一致。新規試験SHA256: eaea04af2ff5f49f9d2182da7ff456993f3e28feb1683e623b953fdba9e57b32。

【Codex 実測】
2026-09-17 / 実行場所build-codex / 指定3ファイルを1回 / 79 passed・0 skipped・0 failed / 0.27秒 / Windows 11 (10.0.26200)、CPython 3.12.14 AMD64、pytest 9.1.1 / skip理由なし。
python -m pytest tests/test_evidence_history_fixture_v2.py tests/test_history_validity_binding_v2.py tests/test_history_validity_binding_v2_claude_contract.py -q -p no:cacheprovider --basetemp C:/Users/s/AppData/Local/Temp/ai-trader-r12-codex-20260917-1430
python=C:/Users/s/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe。PYTHONDONTWRITEBYTECODE=1。全体3024件は再実行なし。公式アカウント共通使用率・残量は取得不能で不明。

【今回 Claude に求める判断】
追加判断なし。独立レビューのバグ0・契約違反0・3判断一致を受領。R12-01は記録のみ、任意の契約追記は見送り。実装・全体再試験・追加レビューは今回の検証範囲外。v2 CLI等の新工程へ拡張しない。

開始: 2026-09-17 14:30:41 JST（最初の時計確認）。成果・履歴保存済み。今回の依頼hash: 2f6596365155a6633400d49ccd766d6b2be4757fcee1a8d9454cb20c92d584e8。
更新時刻: 2026-09-17 14:32:10 JST

<!-- historical-ready: a4e930a2243211e6c646cab2ab8fe7fbd879e0e47e348321f9c341ed11a5d10f -->

