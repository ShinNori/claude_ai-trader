# Codex 引き渡しプロンプト（毎回これをコピーして使う）

## 最新方針：担当分離・トークン節約（2026-09-16）

ユーザーの新指示により、[担当分担とモデル選択](build-codex/TEAM_WORKFLOW.md)を優先する。Codexは製品実装・修正・統合試験、Claudeは未決契約と独立境界試験・重要差分確認を担当。同一作業の二重実施・全件レビュー往復を止める。通常モデルを基本に難所だけ上位へ切替。サブエージェントは原則0、必要な独立作業だけ起動し、以前の「毎回最大3体」「Astra/Opus固定」より優先する。自動送信・起動・見張り再開は許可されていない。詳細は上記文書を参照し、以下の旧担当/モデル/並列方針との衝突は本節を優先。


> 2026-09-11 04:25 JST ユーザー指示：ここからはCodex単独で開発を継続する。AGENTS.md冒頭の最新方針が旧A/Bの担当分担・Claudeへの公開要件に優先する。Claude待ちにせず、必要なops修正もCodexが行い、自己検証と独立レビューを区別する。売買の二重承認契約は変更しない。残作業は build-codex/OPS_V041_RESUME.md。Claudeへの引き渡しはユーザーの連携再開指示まで不要。

作業フォルダ（Codex をここで開く。AGENTS.md を自動で読むため必須）:
`D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader`

## 使い方

Codex を上のフォルダで開き、**次の一文だけ**を貼る（A・B の本文をコピーする必要はない。Codex がこのファイルを読む）:

> ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、〈今回の依頼の一行要約〉を行ってください。

〈今回の依頼の一行要約〉は B の先頭行と同じ文。

1. Codex が終わったら、`build-codex/README.md` 末尾と `build-codex/QUESTIONS.md`（あれば）を Claude に見せる
2. Claude・Codex とも、作業報告の末尾は下の「報告の末尾」テンプレートに従う
3. Claude Code 経由で自動実行する場合は、ai-trader フォルダで Claude Code を開いて「Codex に今回の依頼を渡して、結果を要約して」と言えば `tools/run_codex_build.ps1` がこのファイルを読ませる
4. 自動化する場合（`tools/automation/README.md`）: PC で `powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\watch_handoff.ps1 -Agent codex` を常駐させると、この B が **公開**（`codex_engine.py publish --channel dev --agent codex`）されるたびに Codex が起動する。Claude 側は Cowork が担当（PC に Claude CLI なし）

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

---

## A. 固定プロンプト（毎回同じ）

```
あなたは ai-trader プロジェクトの Codex 担当です。AGENTS.md を読み、「現在のフェーズ」の指示と、
この後に続く「今回の依頼」に従って作業してください。

前提文書（この順で読む。既読なら差分だけ確認）:
1. ../AI合議型自動売買システム_設計書.md（最新版。12章「決定済み」が前提条件）
2. common/共通仕様_フェーズ1.md, common/共通仕様_フェーズ2.md（実装仕様。厳密に従う）
3. common/ISSUES.md（仕様の課題と、Claude/Codex の解決記録）
4. 相手側の最新成果: ops/Claude対応結果.md, ops/README.md（Claude 担当分）

役割分担:
- Codex: build-codex/（主系の市場データ・バックテスト・判定パケット）、common/tests/phase2/ の受入テスト、レビュー・反証
- Claude: ops/（台帳・通知ゲート・ハードリミット）、build-claude/（照合用副系）
- 共通: common/ の仕様・合成データは編集しない。不備は common/ISSUES.md に追記する

編集してよい場所: build-codex/、common/tests/phase2/、common/ISSUES.md（追記のみ）、
親フォルダの「*_修正提案_*.md」。それ以外（common/ の他ファイル、build-claude/、ops/、設計書本文）は読むだけ。

禁止: 実口座・証券サイトへの接続、発注、LINE 送信、実際の LLM 呼び出し、秘密情報のコミット、
テストを通すための受入テストの弱体化（skip / xfail / 条件緩和）、勝てるまでのパラメータ探索。

質問があれば build-codex/QUESTIONS.md に書いて止まる。推測で仕様を変えない。
完了したら build-codex/README.md の末尾に「今回やったこと・テスト件数と結果・Claude に伝えたい点」を追記する。
```

## B. 今回の依頼（Claude が毎回書き換える。最終更新: 2026-09-25 08:30 第14回 適用後）

**発行時刻: 2026-09-25 08:30 JST（Claude / Claude Code。見張り経由で公開）**
※ 自動連携は別枠（tools/automation/、auto チャネル）。この B には混ぜない。

```
今回の依頼: R14-01 修正（Codex 案を Claude が移植済み）の受領確認と README 記録。製品変更は不要

ユーザー指示の転記（2026-09-17 21:46 JST「使用率確認なしで実行」／2026-09-25 08:30 JST「Claude が Codex の修正案を適用」）:
開始時の使用率確認は行わない。08:20 の書込拒否は QUESTIONS.md で解決済み。Codex が TEMP に残した修正案を Claude がそのまま製品へ移植した。

反映済み（すべて Claude が実施、2026-09-25 08:30 JST）:
- build-codex/aitrader/evidence_bundle_store.py ← TEMP の r14 案をバイト単位で移植（sha256 1e7cf927d9c29a37006ed14ae973469189cf5e2a65e30753335160d0e7c04249）
- build-codex/tests/test_evidence_bundle_store_concurrency.py ← TEMP の並行試験案（sha256 78440f765414407fb6ebbd01a013f458f4bc3277701706befc0775b72418135c、7 件）
- build-codex/tests/test_evidence_bundle_store.py ← test_replace_failure_leaves_only_ignored_tmp を「tmp を消す」期待へ更新し改名（Codex 自身が計画していた 1 件のみ）
- Windows 実測（Claude、Codex 同梱 Python 3.12.14、-p no:cacheprovider、basetemp は Dropbox 外）: 関連 12 ファイル 230 passed / 5 skipped / 0 failed / 8.22 秒。
  並行試験 7 件は 5 回反復で全通過。8 スレッド×40 回の負荷では 320 put すべて STORED/NO_OP、tmp 残留 0。
- git: 上記を含む往復分をコミット済み（master）。

1. 上記の移植が自分の案と同一であることを sha256 で確認し、受領を README 末尾に短く記録する（Claude 実測の転記は件数・秒・環境のみ）。
2. 契約文書（EVIDENCE_BUNDLE_STORAGE_CONTRACT_DRAFT.md の優先節）に R14-01 の採用内容を 3 行以内で追記する（再読の再試行 3 回・置換失敗時の NO_OP 判定・自 tmp の削除）。
3. 任意: 全体試験を 1 回（製品を変えたため。Codex の判断）。
4. Claude 向け次回 B を必須 3 項目様式で保存・公開する。次の作業がなければ「引き渡し不要」。

編集範囲は README、契約文書、Claude_Opusキャッチボール.md、Codex引き渡しプロンプト.md の該当節。製品・試験・examples・共通仕様は変更しない。
既存試験の削除・skip・xfail・条件緩和は禁止。実API・実売買審査・LINE・証券接続・発注は行わない。

完了条件: 1〜2 の記録、4 の保存と公開。
```

<!-- handoff-ready: 5d99c6feb2fb95b7a1212fea012106b70dfaa2e403932e366478c33959953188 -->

---

## 過去の依頼（履歴。時刻は JST、成果物ファイルの更新時刻ベース）

| 日時 | 依頼 | 結果 |
|---|---|---|
| 2026-09-08 17:07 | フェーズ1前: Claude/Codex 連携仕様案の作成（Codex 自主作業） | Claude_Codex連携仕様案.md |
| 2026-09-08 17:44 | 設計書 v0.2 のレビュー | Codexレビュー_仕様書修正提案_v0.3.md（P1 を設計書 v0.3 に反映） |
| 2026-09-08 18:01 | フェーズ1: 共通仕様で build-codex を並行実装 | 8/8 通過。Claude 側の処理順の誤りを指摘し、修正後に1円単位で一致（36,037,323.83 円） |
| 2026-09-08 18:59 | フェーズ2 前半: 受入テスト先行作成＋判定パケット＋カタログ修正提案 | test_ledger/gate/packet 124 ケース、packet.py、戦略カタログ_修正提案_v0.2.md |
| 2026-09-08 19:36 | フェーズ2: ops v0.1 の再レビュー＋順序3設計 | test_ops_rereview.py 22 ケース（16 失敗を検出）、PHASE2_OPS_REVIEW.md、PHASE2_INTEGRATION.md、Claude引き渡しプロンプト.md |
| 2026-09-08 19:50 | （Claude）ops v0.2: R01〜R19 対応 | 146/146 通過。ops/Claude対応結果.md 第2回 |
| 2026-09-08 19:58 | フェーズ2 後半: ops v0.2 再々レビュー＋共通仕様 v1.1 改訂案＋順序3 実行器 | 20:34 完了（則光さんの PC で実行）。test_ops_rereview2.py 16 ケース（5 通過・11 失敗＝A7/B4）、PHASE2_OPS_REVIEW2.md、共通仕様_フェーズ2_改訂案_v1.1.md、runner.py＋RUNNER.md（自前 20 通過） |
| 2026-09-08 20:34 | （Codex が代行）ops v0.3: S 番号 11 件の修正 | 21:12 完了。211 件通過（共通 162＋主系 43＋ops 自前 6） |
| 2026-09-08 21:12 | （Claude）ops v0.3 の独立レビュー | 21:25 完了。反例 16 件追加、227 件中 225 通過（A: C05、B: C12）、移行案。ops/Claude対応結果.md 第4回 |
| 2026-09-08 21:25 | C05 修正＋移行 CLI＋C12 契約＋所見の採否 | 21:31 完了。既存227件＋追加8件＝235通過。OPS_V031_REPORT.md、migrate CLI。次はClaude独立レビュー |
| 2026-09-08 21:59 | 第5回 D01/E01・D06/D09/D12対応（Codex） | ops v0.3.2。257件中255通過・2失敗。D08/D09旧期待値は未変更、新契約に合わせた更新と独立レビューをClaudeへ依頼文書化 |
| 2026-09-08 22:00 | （Claude）ops v0.3.2 の独立レビュー＋D08/D09 の期待値更新 | 22:12 完了。D08/D09 更新で 257/257、反例 19 件追加で 276 件中 275 通過（B: G07）、ISSUES に契約の隙間 5 点。ops/Claude対応結果.md 第6回 |
| 2026-09-08 22:26 | 第6回 G07・D08契約5点（Codex） | ops v0.3.3、最終279全通過。Windows CLI確認済み。D08テスト接続の不安定さを診断、次はClaude独立レビューと接続close修正 |
| 2026-09-08 22:26 | （Claude）ops v0.3.3 の独立レビュー＋D08 接続 close＋契約 5 点レビュー | 2026-09-09 05:30 完了。286 件全件通過。テスト 4 ファイルの接続を closing() に統一、反例 7 件追加（I01〜I07）。ops/Claude対応結果.md 第7回 |
| 2026-09-09（開始時刻未取得、依頼発行05:30） | 第7回 新台帳provenance/cutover・精算note保護（Codex） | 05:37終了。ops v0.3.4、325件中324通過・旧I06失敗、追加39通過。次はClaude独立レビューとI06/I07更新 |
| 2026-09-09 05:37 | （Claude）ops v0.3.4 の独立レビュー＋I06/I07 の新契約更新 | 05:45 完了。I06/I07 更新で 326/326、反例 38 ケース追加で 364 件中 363 通過（A: K06 DISCARD 後の再送で生の DB 例外）、ISSUES に契約の隙間 4 点。ops/Claude対応結果.md 第8回 |
| 2026-09-09 05:45 | （Claude が実装）ops v0.3.5: K06 修正＋契約 4 点の採否 | 05:55 完了。364 件全件通過。DISCARD 記憶（DUPLICATE/DISCARDED）、IntegrityError 畳み込み、cutover_at ≤ at、精算 scope 3 点、SNAPSHOT 前 replay の明記。ops/Claude対応結果.md 第9回 |
| 2026-09-09 05:58（確認開始） | 第9回 ops v0.3.5独立レビュー・改訂案反映（Codex） | 06:03終了。既存364維持、追加17件中14通過・3失敗。次はClaudeのL02修正とL03契約対応 |
| 2026-09-09 06:03 | （Claude が実装）ops v0.3.6: L02 修正＋L03 契約決定＋strip 誤記訂正 | 06:20 完了。388 件中 386 通過（残 2 は L03 入力拒否と衝突する Codex 試験＝前提の誤り）。反例 7 件追加（M01〜M07）。B は並行セッションの pingpong レビューが先行中のため「保留中の依頼」に置いた。ops/Claude対応結果.md 第10回 |
| 2026-09-09 06:35 | 自動引き渡し 2 方式の実装レビュー（Codex） | 06:55 開発ループから分離 → `tools/自動連携_Codex依頼.md`（別枠）。B は第10回 ops v0.3.6 に戻した |

| 2026-09-09 06:20 | 第10回 ops v0.3.6 に合わせた期待値更新・接続 close・独立レビュー（Codex） | 06:40 完了（追補）。O 系列 7 件追加、402 件中 401 通過（A: O01 遅着通知への保留 APPLY の時点再生） |
| 2026-09-09 06:49 | （Claude）ops v0.3.7: O01 修正＋文書明確化＋独立試験 P01〜P07 | 07:10 完了。411 件中 410 通過（残 1 は共通 test_ledger.py の時計依存＝テスト前提の誤り、Codex へ修正依頼）。ops/Claude対応結果.md 第11回 |
| 2026-09-09 07:10 | 第11回 v0.3.7 独立レビュー＋test_ledger.py 時計依存フィクスチャ修正（Codex） | 07:07 完了。Q 系列 19 件追加、430 件中 428 通過（B: Q09 前倒し訂正、Q10 作成前の通知状態）。時計依存は at=AT で解消 |
| 2026-09-09 08:05 | （Claude）ops v0.3.8: Q09 入力拒否＋Q10 識別補完＋独立試験 R01〜R10 | 08:12 完了。442 件中 441 通過（残 1 は Q09 のテスト前提変更、Codex へ期待値更新依頼）。ops/Claude対応結果.md 第12回 |
| 2026-09-09 08:12 | 第12回 v0.3.8 独立レビュー＋Q09 期待値更新＋改訂案 (i)(j)（Codex） | 08:21 完了。T 系列 15 件追加、457 件中 453 通過（A: T06 EXPIRE 不明参照 ×3、B: T07 時刻なし訂正）。dev チャネルで公開 |
| 2026-09-09 08:23 | （Claude）ops v0.3.9: T06 修正＋T07 有効時刻継承＋独立試験 S01〜S11、R06/R07 訂正 | 08:30 完了。469 件中 468 通過（残 1 は T07 のテスト前提変更、Codex へ期待値更新依頼）。ops/Claude対応結果.md 第13回 |
| 2026-09-09 08:30 | 第13回 v0.3.9 独立レビュー＋T07 期待値更新＋改訂案 (k)（Codex） | 08:56 完了。U 系列 18 件追加、487 件全件通過。文書明確化 C01/C02 を Claude へ依頼。dev チャネルで公開 |
| 2026-09-09 08:58 | （Claude）第14回: C01/C02 の文書明確化＋最終照合 | 09:05 完了。487 件全件通過を確認。ops v0.3.9 の双方確認完了。Codex 宛て B は「引き渡し不要」で公開 |
| 2026-09-09 09:55 | 第15回 フェーズ2 順序4 の共通仕様案＋受入テスト先行作成（Codex） | 10:37 完了。仕様案 v0.1（build-codex/）、test_judges.py 69・test_notify.py 47。ACL 修復後に再実行。pytest 未導入で実測不可→質問→則光さん回答「両方」 |
| 2026-09-09 10:40 | （Claude が実装）第16回 ops v0.4.0: judges / notify | 11:10 完了。633 件全件通過（受入 116＋独立試験 30）。Ledger.proposal() 追加。ops/Claude対応結果.md 第16回 |
| 2026-09-09 11:22 | （Claude が実装）第17回 ops v0.4.1: Codex レビュー V01〜V13 の修正（V10 採用）＋独立試験 S01〜S11 | 11:40 完了。696 件全件通過（Codex レビュー 49 件を含む）。ops/Claude対応結果.md 第17回 |

| 2026-09-11 20:20 | （Claude / Cowork）第18回 ops v0.4.1（Codex 単独継続分）の独立レビュー | 20:57 完了。反例 11 件追加、収集できた範囲で 306 件中 300 通過（A: W01 未来時刻の制御で緊急停止が無効化、B: W02 内容変更 enqueue の黙殺）。aitrader 未取得のため指定全体コマンドは未実行。ops/Claude対応結果.md 第18回 |
| 2026-09-16 23:50 | （Claude）第11回: D10-01〜05 の未決契約整理（段表・件数表・E1〜E9 固定出力・履歴 v2 正規化契約） | Claude_Opusレビュー_証拠履歴_第11回.md。指摘 R11-01（低）1 件。参照計算のみ、pytest・fuzz なし。Codex へ採否と契約反映を依頼（未公開） |
| 2026-09-17 14:29 | （Claude）第12回: 行履歴 v2・結合 v2 実装の独立確認（重要差分 3 点） | Claude_Opusレビュー_証拠履歴_第12回.md。実装バグ 0・契約違反 0。新規反証 30 件（test_history_validity_binding_v2_claude_contract.py）30 passed。R12-01 記録のみ。Codex へ関連実測・README 記録を依頼（公開） |
| 2026-09-17 20:28 | （Claude）v2 CLI 契約案の作成と Codex への実装依頼（ユーザー指示で上限 40%→50% 仮定） | V2_CLI_CONTRACT_PROPOSAL.md。Codex へ v2 CLI 2 本の実装・試験・実測を依頼（公開） |
| 2026-09-17 21:56 | （Claude）第13回: v2 CLI 2 本の読取境界・固定 stderr・終了コードの独立確認 | CLI_V2_INDEPENDENT_REVIEW.md。実装バグ 0。新規反証 30 件（test_history_v2_cli_claude_contract.py）30 passed。R13-01（--help、低）の採否を Codex へ依頼（公開） |
| 2026-09-17 22:28 | （Claude）D13-01〜03 保存契約案の作成と Codex への採否・実装依頼 | EVIDENCE_BUNDLE_STORAGE_CONTRACT_DRAFT.md（3 判断とも採用案を固定、反証表 30 件）。Codex へ実装可能性確認・実装・実測を依頼（公開） |
| 2026-09-20 09:11 | （Claude）第14回: 人工束 put/verify の独立確認（原子性境界・hash 照合・API 呼出回数） | EVIDENCE_BUNDLE_STORE_INDEPENDENT_REVIEW.md。単一スレッド経路はバグ 0。R14-01（中: 多重並行 put で WRITE_FAILED 8.4%・偽 RECORD_CORRUPT 0.6%、データ破損 0）。新規反証 37 件 37 passed。B は保存のみ・未公開（D13 はデスクトップ側に一本化） |
| 2026-09-25 08:30 | （Claude）R14-01: Codex の修正案を製品へ移植（ユーザー承認の例外）・並行試験追加・Windows 実測 230 passed | Codex へ受領・記録を依頼（公開） |

※ Cowork（このセッション）からの `tools/run_codex_build.*` 自動起動は、Anthropic 側のネットワーク方針で OpenAI に到達できず失敗する（2026-09-08 確認）。自動起動は則光さんの PC 上の Claude Code から行う。往復の自動化は `tools/pingpong.ps1`（`tools/PINGPONG.md` 参照、2026-09-09 追加）。
