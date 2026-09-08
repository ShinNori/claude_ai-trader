# Codex 引き渡しプロンプト（毎回これをコピーして使う）

作業フォルダ（Codex をここで開く。AGENTS.md を自動で読むため必須）:
`D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader`

## 使い方

Codex を上のフォルダで開き、**次の一文だけ**を貼る（A・B の本文をコピーする必要はない。Codex がこのファイルを読む）:

> ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、〈今回の依頼の一行要約〉を行ってください。

〈今回の依頼の一行要約〉は B の先頭行と同じ文。

1. Codex が終わったら、`build-codex/README.md` 末尾と `build-codex/QUESTIONS.md`（あれば）を Claude に見せる
2. Claude・Codex とも、作業報告の末尾は下の「報告の末尾」テンプレートに従う
3. Claude Code 経由で自動実行する場合は、ai-trader フォルダで Claude Code を開いて「Codex に今回の依頼を渡して、結果を要約して」と言えば `tools/run_codex_build.ps1` がこのファイルを読ませる
4. 自動化する場合（`tools/PINGPONG.md`）: 監視方式 `powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent codex` を PC で常駐させると、この B が更新されるたびに 1 分以内に Codex が起動する。両側を PC で回すなら `tools\pingpong.ps1`

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

## B. 今回の依頼（Claude が毎回書き換える。最終更新: 2026-09-09 第10回）

**発行時刻: 2026-09-09 06:20 JST（前回 Codex 完了: 06:03 JST。第10回は Claude が ops v0.3.6 を実装）**
※ 自動連携（watch_handoff.ps1 / pingpong.ps1 のレビュー）は開発ループと**別枠** `tools/自動連携_Codex依頼.md` に移した（06:55）。
　その作業中・完了時も、この B は変更しない。

```
今回の依頼: ops v0.3.6（L02 修正・L03 入力拒否採用。Claude 実装）に合わせた Codex 所有試験 2 件の期待値更新と、
test_migration.py の接続 close 統一、v0.3.6 の独立レビュー

前提: ops/ での実測は 388 件中 386 通過（skip/xfail なし）。残る 2 失敗は L03 で「解決の業務時刻は保留行の業務時刻以後」を
採用したことと衝突する Codex 所有試験（テスト前提の誤り）。詳細は ops/Claude対応結果.md 第10回・common/ISSUES.md 06:20。

1. 期待値更新（Codex 所有、保護条件は緩めない）
   - build-codex/tests/test_ops_v035_review.py::test_l03: 10:00 の保留を 08:00 で DISCARD → LedgerError、保留は残り、残高・履歴不変。
     10:00 以後の DISCARD は受理され、09:00 の replay は保留行を含む（残高不変）ことを検査する形へ
   - ops/tests/test_v03_integrity.py::test_discard_audit_actor_and_replay: DISCARD の at を保留行の at（AT+1h）以後にする。
     監査 detail の actor 検査・再起動後の pending なしはそのまま

2. ops/tests/test_migration.py: `legacy` フィクスチャと test_m03 の `with sqlite3.connect(path) as con:` を
   `with closing(sqlite3.connect(path)) as con, con:` に統一（原本 hash 比較が gc 依存で 8 回中 2 回失敗。D08 と同原因）。
   assert は変えない

3. v0.3.6 の独立レビュー（反証は build-codex/tests または common/tests/phase2 へ）
   - replay(at < SNAPSHOT.at) が空: 後着保留・遅着通知・訂正・EXTERNAL 注文・別オフセット・移行済み台帳（マーカー前後）
   - 解決時刻の制約: 同時刻・別オフセット・時刻なし行・cutover 保留 TRADE・再生時の一致（旧版で受理された逆順履歴が
     MigrationError(seq, PENDING_RESOLVED) で止まり、migrate --check に位置が出ること）
   - 改訂案 v1.1 に (e) 開始残高前の replay は空（後着保留があっても）、(f) 解決時刻 ≥ 保留行の業務時刻、
     (g) strip は全角空白・改行を含む前後の空白すべて（内部・正規化は対象外）を追記

完了条件: ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q` の実測件数を build-codex/README.md に記録。
388 件すべて通過した場合のみ「全件通過」と書く。build-codex/Claude引き渡しプロンプト.md の B を次の Claude 依頼に更新。
報告は「報告の末尾」テンプレートに従う。
```

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

※ Cowork（このセッション）からの `tools/run_codex_build.*` 自動起動は、Anthropic 側のネットワーク方針で OpenAI に到達できず失敗する（2026-09-08 確認）。自動起動は則光さんの PC 上の Claude Code から行う。往復の自動化は `tools/pingpong.ps1`（`tools/PINGPONG.md` 参照、2026-09-09 追加）。
