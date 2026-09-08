# 共通仕様の課題記録

## 2026-09-08 Codex：フェーズ2 v1.0（実装前レビュー）

既存の公開シグネチャを維持してテストを先に作成した。以下は仕様の追記案・制限であり、未定義部分を本番の承認条件として確定したものではない。利用者は開発内容の仲介を不要としているため、Claude実装者がこの記録とテストを直接照合できる形にする。

1. **未定義の入力型**：PositionIn、OpenOrderIn、TradeEvent、CsvFill、LedgerView、ReportResult、ImportResultの詳細型・コンストラクタがない。テストでは未定義入力のみ属性レコード（SimpleNamespace）を使用する。TradeEventは本文に列挙された全フィールド。CsvFillはevent_id/proposal_id/code/side/qty/price/fee/at/source/broker_order_idを持つ。LedgerViewはcash/reserved/available（数値）、positions（code→qty/avg_price/stop_orderの属性レコード）、reserved_positions（code→予約金額）、daily_pnl、day_start_equity。実装はこの属性契約を満たすか、正式な型定義を共有仕様に追加すること。テストが具体的な内部DB構造へ依存しないための入力形であり、仮実装ではない。
2. **PARTIAL/FILLEDのqty**：増分か累計かが不明。テストはイベント単位の増分（20→30→50株で計100株）を提案契約とする。同じbroker_order_idには複数の部分約定があり得るため、このIDだけで一律に重複排除してはいけない。証券側の約定IDを将来追加すること。証券IDなしの曖昧なCSVはpendingとし二重計上を避ける。
3. **CORRECTION**：訂正対象IDがない。現契約の「直前の報告」を対象に、同じkindを継承する置換として単価訂正をテストする。複数訂正・到着順逆転にはreplaces_event_idの追加を提案する。PARTIALの訂正とCANCELLEDの訂正は正式な遷移定義が必要。
4. **予約額**：PARTIALでも全量予約を残しつつ約定分をcashから減らすと、予算内の実約定がavailable不足で拒否され得る。v1.0の文言に従い予約解放の最終条件をテストするが、中間残高の正確な式は未確定。推奨は「未約定残数量×上限価格×費用余裕」へ縮小。SELLの現金予約は0とし、保有株数を予約する別管理が必要。
5. **日次損失**：evaluate引数に基準資産や当日損益がない。テスト用LedgerViewにday_start_equityとdaily_pnlを提案し、daily_pnl/day_start_equityで境界を評価する。DDはequity/peak_equity。浮動小数点誤差で10%ちょうどを通さないこと。
6. **営業日・イベント回避**：build_proposals引数に営業日カレンダーがない。追加の任意キーワードbusiness_daysで営業日一覧を渡せるようにする。未指定時の平日のみの計算は研究用互換動作であり、祝日を反映した本番日程ではない。CLIは市場DBのcalendarから明示的に次営業日を渡し、範囲不足なら停止する。合成データのcalendar自体は簡易休日。決算回避の営業日数はgateへ営業日カレンダーを渡す契約がないため、週末をまたぐ±2営業日の厳密テストは正式な入力定義待ち。
7. **呼値丸め**：方向・境界が不明。BUY上限+0.5%を守るため、Decimalで上限を計算し、その価格帯の簡易呼値で切り下げる。raw<1000は1円、1000<=raw<=5000は5円、それ以上は10円。これは共通仕様用の簡易呼値であり、実取引所の呼値表ではない。
8. **SELLパケット**：保有数と売り指値の算定式がbuild_proposalsにない。予算から売却数を作ると誤注文につながるため、今回の生成器はフェーズ1由来のBUYのみ受け付け、SELLを明示エラーにする。ゲートのSELL検証は手書きProposalで別途網羅する。売却パケットは保有数量・売り下限の正式契約を追加後に対応する。
9. **IDの安定性と改版**：同じ銘柄・戦略・版が候補リストに重複した場合はエラー。日付は執行日のJST。入力順に依存しないよう戦略・版・銘柄でソートしてIDを採番。snapshot/policy/予算を変えると同一IDでhashが変わり得るため、再通知には台帳側での改版・旧版失効規則が必要。
10. **ハッシュの保護範囲**：仕様どおりreason/events/market_contextをhashから除外する。したがって同じhashでもモデルへ渡す資料が変わり得る。運用ではsnapshot_idを内容アドレスにするか資料全体の独立ハッシュを検証し、審査開始後の変更を禁止する必要がある。今回のCLIは候補・価格・単元・イベント・参照日に基づくsnapshotを生成する。ゲートでp自体のhashを再計算するかも明文化すべき。
11. **再計算可能な履歴**：ledger_eventsの存在以外にイベントの公開形式・再生APIがない。テストは履歴追記・訂正前レコード保持と再接続を確認するが、「任意時点から再計算できる」ことの完全な証明にはreplay(at)等の公開契約が必要。現時点のテストでは未保証とする。
12. **異常入力**：naive日時、NaN/Infinity、bool数量、空ID、負予算、欠損価格・単元はパケット生成でエラーにする。ゼロ予算/一単元未満は仕様どおり除外しwarningsへ理由を残す。未知イベントはUNKNOWNとして保持する。ops側も不正入力時に残高を変えないこと。

opsが存在しない間、test_ledger.pyとtest_gate.pyはモジュール単位で明示skipする。成功を意味しない。opsが利用可能になれば自動でテストされる（既存パッケージ内のimportエラーは隠さない）。packet側はopsへの依存なしで実行する。

## 2026-09-08 Claude：フェーズ2 ops 実装時の解決記録（上記 1〜12 への回答。既存記述は変更していない）

- #1 入力型: 属性名契約で受け付け（SimpleNamespace 可）、正式 dataclass を `ops/aitrader_ops/models.py` に定義。LedgerView に daily_pnl / day_start_equity を正式追加。
- #2 PARTIAL/FILLED qty: 増分。重複排除は「同一 proposal・同一 broker_order_id・同一数量・同一単価（時刻があれば時刻も）」。broker_order_id 単独では排除しない。
- #3 CORRECTION: `replaces_event_id`（任意）を TradeEvent に追加。省略時は直前の未取消約定。kind 継承、履歴保持。
- #4 予約額: 未約定残数量 × 指値 × (1+fee_margin)。SELL は現金予約 0、株数を reserved_shares で予約。
- #5 日次損失: daily_pnl <= −daily_loss_stop × day_start_equity（Decimal）。DD は equity <= peak × (1−drawdown_stop)。
- #6 営業日: ゲートは暦日 ±2 で判定（引数にカレンダー無し。営業日より保守的）。共通仕様 §4 にカレンダー引数を追加する提案。
- #10 ハッシュ: ゲートで p から再計算し一致必須（改変検出）。
- #11 履歴: 永続化は追記専用 ledger_events のみ。`Ledger.replay(at)` を公開。
- #12 異常入力: naive datetime・bool・負値・NaN はエラー、残高不変（コピー適用→成功時のみ追記）。
- 結果: `ops/` で `pytest ../common/tests/phase2 -q` → 124 passed（skip/xfail なし）。詳細は `ops/README.md`, `ops/Claude対応結果.md`。

## 2026-09-08 Codex：ops再レビューの追記

既存124件通過を確認後、`test_ops_rereview.py` に22件追加。全体130 passed / 16 failed（skip/xfailなし）。opsおよび既存テストは未変更。詳細・全失敗のA/B分類は `build-codex/README.md` 末尾、5項目の回答は `build-codex/PHASE2_OPS_REVIEW.md`、日次組み込み案は `build-codex/PHASE2_INTEGRATION.md`。

- 不変条件・異常入力への反例11ケース：SELL訂正時の取得原価、取消後の予約復活、予約超過作成、約定費用と他予約の競合、初期BUY/SELL注文の予約欠落、CSV明示IDの銘柄・売買不一致2ケース、旧CSVの新候補への二重計上、replayへの未来通知混入、gateのNaN例外。
- 契約追加・テスト前提の区別が必要な5ケース：手数料差のpending化、複数Ledgerハンドルの整合性、再起動時の予約率保持、営業日カレンダー、ゲートの売却株予約控除。既存仕様違反として断定しない。テストは将来契約の提案として失敗したまま残した。
- #6「暦日が営業日より保守的」は訂正が必要。金曜から月曜は3暦日・1営業日で、現ゲートは許可してしまう。共通APIへカレンダーを追加する契約を提案。
- #11 replayの業務時刻と記録時刻を区別する。日時なし通知を常に含める現在の実装は「任意時点」の再現と衝突する。監査には無視・重複・拒否の受信試行も別の追記記録として残す提案。
- CSVは楽天の公開履歴説明を確認したが実ファイルの全ヘッダ・約定ID・時刻精度は未確認。broker_order_idを注文IDと約定IDに兼用しない。pending解決の追記APIも必要。
- 順序3は最新viewでevaluate→create_noticeの順、単一書込主体で予約確定。現在create_notice直後に予約するため、先に作ると自己予約の二重評価になる。模擬公開API結合R20は通過、実CLI・配信・日次実行器は未実装。
- Claudeへの次回依頼は `build-codex/Claude引き渡しプロンプト.md` に保存。上の解決記録は履歴として残し、本文の仕様は変更していない。

## 2026-09-08 Claude：再レビュー R01〜R19 への対応（ops v0.2。既存記述は変更していない）

- 結果: `ops/` で `python -m pytest ../common/tests/phase2 -q` → 修正前 130 passed / 16 failed を再現 → 修正後 **146 passed（skip/xfail なし）**。A 分類 11 件は不変条件・原価・照合・時刻・NaN の修正で解消。B 分類 5 件は既存シグネチャを壊さない**追加**で契約を整合させて解消。詳細は `ops/Claude対応結果.md`。
- #6 の訂正: 「暦日の方が営業日より保守的」は誤り（金→月は 3 暦日・1 営業日）。ゲートは「暦日差 ≤ N または 営業日差 ≤ N」で判定し、`evaluate(..., business_days=None)` を任意引数として追加。省略時は月〜金を営業日とみなす（祝日なし）。共通仕様 §4 への追記案: `business_days: Iterable[date] | None` を evaluate に追加し、実行器は市場DBの calendar を渡す。
- #11 の訂正: `replay(at)` は業務時刻（effective_at）ベースに統一（通知作成にも作成時刻を持たせる）。記録順ベースは `replay_known(seq)` として分離。受信試行は `ingest_attempts` 表に全件追記（残高イベントには再適用しない）。予約率・規則版は SNAPSHOT イベントに保存し再オープン時に優先。
- 共通仕様への追加提案（既存 API は維持）: `LedgerView.reserved_shares`（売却予約株数）、`GateResult.reason_codes`、`create_notice(p, at=None)`、`Ledger.replay_known(seq)`、`Ledger.pending_rows()`、`Ledger.resolve_pending(source_event_id, proposal_id, at, action)`、`Ledger.policy()`、`TradeEvent.replaces_event_id`、`OpenOrderIn` を初期予約に反映（BUY 現金 / SELL 株数）。
- CSV 照合: 約定キーは「注文ID・数量・単価（・時刻）」を全通知横断で照合。手数料差は pending。明示 proposal_id でも銘柄・売買方向不一致は pending。実 CSV の列・約定IDは未確認のまま（要実ファイル）。

## 2026-09-08 Codex 第3回：ops v0.2再々レビューと順序3の模擬実装

- 新規test_ops_rereview2.pyは16件、5通過・11失敗。既存146件は通過維持。A7件：S04後続買付後の売却訂正原価、S05別銘柄の誤重複、S09replay参照切れ、S11空ID監査欠落、S12pending銘柄再検査、S13action列挙検証、S16監査と残高の非原子性。B4件：S03注文中分割、S07時刻なし照合、S14INVALIDコード、S15外部注文照合。Bは現契約との調整が必要で、既存仕様違反と断定しない。
- 5項目の回答はbuild-codex/PHASE2_OPS_REVIEW2.md。改訂案は../共通仕様_フェーズ2_改訂案_v1.1.md。仕様本文は変更していない。
- build-codex/aitrader/runner.pyを実装。模擬専用、初期台帳の明示作成、最新view→evaluate→create_notice→APPROVED→outbox、日次枠、再実行の冪等性、参照カレンダー、固定manifest・Proposal・Verdict・GateResultを保存。実CLI・LINE・発注なし。自前20件通過。
- 評価資産は現金＋前営業日終値×保有数量。日次損益とDDは当日外部入出金を調整したスケール、集中度分母は実額。基準値は呼出側が明示する。日跨ぎ高値管理・時価集中度・DB跨ぎ完全自動復旧は未実装。途中の台帳書込は予約を保持して停止する。
- opsコードと既存受入テストは変更なし。修正依頼はbuild-codex/Claude引き渡しプロンプト.mdのBを最新化。新しいテストの失敗はops担当が修正する。
## 2026-09-08 Codex：指定されたClaude担当作業の実施（ops v0.3）

ユーザーがClaude引き渡しMDのA/B実施を明示指定したためopsを修正。共通仕様・受入テスト・主系コードは未変更。S04/S05/S09/S11/S12/S13/S16を修正。S03注文中分割拒否、S07時刻不足の曖昧一致保留、S14INVALID専用コード、S15EXTERNALの未確認追加を保守的追加契約としてopsに採用。本文の仕様確定は代行しない。

resolve_pendingに任意actor/reasonを追加（未指定はunspecified）。監査と残高イベントを同一トランザクションへ統合。seqは同一読取・当該INSERTと紐付ける。新規schema_version=3。旧イベントを新規制約で再生した互換性、匿名CSV・分割を跨ぐreplay、実約定IDの粒度は未解決。既存DBの移行は実施していない。

検証は共通162＋主系43＋ops追加6＝211件通過（5.49s、skip/xfailなし）。詳細はops/Claude対応結果.md第3回とops/README.md v0.3節。次回は別担当によるops v0.3・移行・replayの再レビューが必要。
## 2026-09-08 21:25 Claude：ops v0.3 の独立レビュー（既存記述は変更していない）

- 実測: ops/ で共通 phase2 ＋ 主系自前 ＋ ops 自前（Codex 6 ＋ Claude 16）＝ 227 件中 **225 通過 / 2 失敗**。失敗は Claude 追加の反例（C05: A、C12: B）。Codex の 211 件は通過維持。詳細は `ops/Claude対応結果.md` 第4回、テストは `ops/tests/test_v03_review_claude.py`。
- C05（A・S09 と同根）: proposal_id なし CSV 約定は replay(at) の context_only 補完の対象外となり、通知作成前の時点で約定が消える。適用時の紐付け先を確定情報として永続化する契約が必要。
- C12（B）: 併合で端株が出る SPLIT（101×0.5）が 50 株に切り捨てられる。端株の扱い（拒否／現金精算イベント）は共通仕様未定義。
- 移行（C14）: v0.2 で受理した履歴が v0.3 の規則で起動拒否される。`MigrationError(seq, kind, reason)`＋`POLICY_UPGRADE` マーカー（マーカー以前は旧規則で再生）＋原本非破壊のコピー検証 CLI を提案。
- 所見（未再現）: 監査失敗時の例外二重化、ID_PAYLOAD_CONFLICT の比較範囲、時刻なし CSV の replay 包含、売却済み分の買付訂正、EXTERNAL の created_at 境界、reason_codes の順序依存。

## 2026-09-08 Codex 第4回：C05/C12・移行追加

明示依頼によりops v0.3.1を実装。CSV適用先を確定保存してreplayが再照合しない。MigrationError(seq,kind,reason)とPOLICY_UPGRADEの旧規則区間を追加。移行CLIは入力を読取専用で開き、別コピーへ追記する方式。原本非変更・旧最終seqの残高一致を追加テストで確認。端株SPLITを拒否しFRACTIONAL_CASHOUTを現金受取だけのイベントとして追加、改訂案へ追記。本文は未変更。

既存227＋追加8＝235件通過。所見6点の採否・制限・実装者はbuild-codex/OPS_V031_REPORT.md参照。旧任意スキーマや端株株数調整の全面移行を保証するものではなく、reason_codesの構造化も別工程。
## 2026-09-08 21:50 Claude：ops v0.3.1 の独立レビュー（既存記述は変更していない）

- 実測: ops/ で 252 件中 **247 通過 / 5 失敗**（Codex の 235 件は通過維持）。失敗は Claude の反例 D01・E01（A）、D06・D09・D12（B）。詳細は `ops/Claude対応結果.md` 第5回。
- D01/E01（A）: `_commit` の内部属性除去が CSV_FILL 限定。TRADE 等の外部入力に `_applied_proposal_id` や任意の `_` キーを混ぜると永続化され、replay(at) の通知解決を偽装できる。全種別で `_` 始まりキーを入力から除去し、内部属性は適用後に台帳側で付与する契約が必要。
- D06/D09（B）: POLICY_UPGRADE マーカーの一意性・`legacy_last_seq` 検証、`--apply` 失敗時の作業コピー処理（一時ファイル→検証→rename）が未定義。
- D12（B）: FRACTIONAL_CASHOUT と保有銘柄の紐付け（code 必須・未保有拒否）が未定義。
- 注: `ops/tests/test_v031_review_claude.py` は別の Claude セッションが 21:39 に作成。本セッションは再実行・検証と追補（`test_v031_review_claude2.py`）を担当。

## 2026-09-08 第5回：D01/E01・D06/D09/D12の採用契約（Codex）

ユーザーの今回指示に従いops v0.3.2を実装。共通仕様本文は未変更。全イベントの外部入力で内部キーを除去し、移行マーカーは1個・直前seq一致、適用は一時DBを検証してから公開、失敗時は一時DB削除。マーカーは業務時刻ではなく記録順境界。端株精算はcode必須かつ現在保有銘柄のみの現金イベントを採用。

既存D08/D09は失敗コピーの存在を要求し、今回Bの「失敗時は削除」と矛盾する。既存試験は変えず、257件中255通過・2失敗（追加5通過）を記録。2失敗を新契約に対するテスト前提の誤りと分類し、Claudeへ期待値更新・再レビューを依頼。全件通過扱いにしない。

D08の残高差は原本保全・取得済み明細とのイベント単位照合を要する。機械的な差額ADJUSTでは解消保証なし。表現不能な訂正は移行保留とし、照合済み開始残高への別の移行方式を設計する必要がある（未実装）。詳細：[第5回対応報告](../build-codex/OPS_V032_REPORT.md)。
## 2026-09-08 22:10 Claude：ops v0.3.2 の独立レビュー（既存記述は変更していない）

- 実測: ops/ で 276 件中 **275 通過 / 1 失敗**（Codex の 257 件は D08/D09 の期待値更新後すべて通過）。失敗は Claude の反例 G07。詳細は `ops/Claude対応結果.md` 第6回。
- G07（B）: `POLICY_UPGRADE` の一意性・境界検証は再生時（`_validate_markers`）だけで、書込時（`_State._on_policy_upgrade`）にない。内部 API 経由で 2 個目のマーカーが受理され、次回起動で初めて壊れる。`rule_version >= 3` での拒否を提案。
- D08 手動照合の契約の隙間（方針には同意）:
  1. `migrate --check` は旧新の最終残高しか返さず、「最初に乖離した seq」の特定支援がない。
  2. 「照合済み開始残高への移行」は SNAPSHOT が 1 台帳 1 回のため新台帳ファイルでしか表現できず、旧台帳の出所（パス・ハッシュ・旧最終 seq・照合記録・担当者）を新 SNAPSHOT に残す契約が未定義。
  3. D12（端株精算は保有銘柄限定）の採用により、全量売却後に届く精算金の受け皿（DEPOSIT / DIVIDEND のどちらか、note 書式）が未定義。
  4. 移行はコピー方式のため、apply 後に原本へ追記された履歴は出力に入らない（`check(原本).last_seq` で検知可）。移行中〜昇格までの原本の書込停止と、`.upgraded.sqlite` の運用パスへの昇格手順（旧原本の退避名・バックアップ世代）が未定義。
  5. Windows 実機（日本語・`!`・空白を含む実フォルダ）での `--check` は未確認。`Path.as_uri()`＋`?mode=ro` の動作確認を則光さんの PC で 1 回行う必要がある。

## 2026-09-08 第6回対応（Codex）：G07採用とD08契約5点

G07はrule_version>=3で書込前に拒否するようops v0.3.3で実装。任意のcheck旧規則例外の辞書化は不採用で、移行不能な旧履歴はMigrationErrorで停止する。

D08の(1)乖離seq出力は今回見送り・先頭からの逐次比較を手作業と明記、(2)新台帳＋出所付き新SNAPSHOTは契約案採用・実装次回、(3)売却後の確定精算金はDIVIDEND＋POST_EXIT_SETTLEMENTのJSON note、(4)全書込停止・静止後hash・世代バックアップ・昇格/復旧は手動契約採用、(5)Windows実機の日本語・記号・空白パスで合成コピーの実CLI読取検査成功。完全な項目・制限は [OPS_V033_REPORT.md](../build-codex/OPS_V033_REPORT.md)。共通仕様本文未変更。

最終279全通過（既存276＋追加3）。ただし既存D08は初回失敗し、check前のgc.collectだけで本体hash変化を再現。テストの_insertが接続をcommit後closeしないため、静止前hash採取が不安定。Claude所有テストの明示closeを次回依頼し、既存試験は変更していない。読取専用接続でもSQLiteのWAL/SHMファイル生成はあり、本体不変と副ファイル不増加は区別する。
## 2026-09-09 05:30 Claude：ops v0.3.3 の独立レビュー（既存記述は変更していない）

- 実測: ops/ で 286 件 **全件通過**（Codex 279＋Claude 追加 7）。D08 の hash 不安定はテスト側の未 close 接続（WAL 反映が gc 時）が原因で、Claude 所有テストを `closing()`＋commit に統一して解消。再現試験 I01/I02 で固定。
- G07: v0.3.3 で解消（I03〜I05）。残る限界（B・運用注記）: 生 SQL で v0.3 台帳にマーカーを入れると再生時にマーカー前が旧規則区間になり、保留中の時刻なし CSV が適用済みに化ける（I04）。`check` は旧新不一致で検出し `apply` は拒否するため、**移行は CLI のみ・生 SQL 禁止**を運用契約に明記する。
- 契約 (2) 新台帳＋provenance: 新台帳は旧 event_id を記憶せず、旧台帳で適用済みの約定 ID を再送すると適用される（I07）。実装案: SNAPSHOT `provenance.cutover_at` を必須にし、`at < cutover_at` の TRADE/CSV_FILL は自動適用せず PENDING にする（時刻なし報告は既存契約で保留）。`init_snapshot(..., provenance=None)` を追加し `method=RECONCILED_OPENING` のとき必須キーを検証。
- 契約 (3) DIVIDEND＋JSON note: note 未検証・同一 `source_event_id` の再送が二重計上（I06）。実装案: `category=POST_EXIT_SETTLEMENT` の note は必須キー検証、`(category, source_event_id)` の重複は LedgerError。主系 `net_external_flow` に精算金を含めない呼出契約を主系側にも明記。
- 契約 (1): `migrate --diff <copy>`（seq ごとの旧新残高と最初の相違 seq、片側再生不能の位置）を次回実装候補として提案。
- 契約 (4): 耐久キュー未実装のため昇格保留とあるが、現運用は LINE 手動報告・CSV 手動取込なので「取込停止＝LINE トーク履歴と証券 CSV がキュー」で成立する。自動取込を実装するまではこの前提を手順に明記すれば昇格可能。

## 2026-09-09 第7回対応（Codex）：新台帳・精算検証を実装

ops v0.3.4でprovenance検証、cutover前TRADE/CSVのPENDING永続化、境界遮断行のAPPLY拒否、DIVIDEND精算note検証・(category,source_event_id)重複拒否を実装。詳細は [OPS_V034_REPORT.md](../build-codex/OPS_V034_REPORT.md)。共通仕様本文は未変更。新しいReportResult.pendingは末尾追加で既存呼出し互換を維持。

指定全体325件中324通過・1失敗（追加39通過）。I06は二重計上を求める旧期待値で失敗、Claudeへ更新依頼。I07はprovenanceなしで互換動作として通過するため、次回はprovenance付き遮断試験へ更新または追加が必要。既存試験未変更。

出所hashは形式検証で実ファイルの真実性照合・actor認証ではない。境界前APPLYは禁止し、照合後DISCARDまたは別途訂正。通常noteと他categoryは互換動作のため自由文による二重精算を自動検出しない。旧履歴に不正な精算JSONや重複がある場合は再生停止し得る。--diffは今回見送り。規則マーカー移行はCLIのみ、生SQL禁止。手動取込停止中はLINEトーク履歴と証券CSVを未処理保管先にして昇格可能という手順をREADMEへ反映した。
## 2026-09-09 05:45 Claude：ops v0.3.4 の独立レビュー（既存記述は変更していない）

- 実測: ops/ で 364 件中 **363 通過 / 1 失敗**（Codex 325 件は I06/I07 更新後すべて通過）。失敗は Claude の反例 K06。詳細は `ops/Claude対応結果.md` 第8回。
- K06（A）: 保留行の DISCARD 後に同じ event_id を再送すると、`ledger_events.event_id` の UNIQUE 制約で `sqlite3.IntegrityError` が生のまま `import_csv_fills` / `report` から漏れ、同一バッチの残りの行が処理されない（ロールバックはされる）。v0.3 以来の既存欠陥。OPS_V034_REPORT の「DISCARD した行を再送すれば再び保留され得る」と一致しない。提案: DISCARD 済み ID を状態に保持し、再送は DUPLICATE（reason_code DISCARDED）として監査のみ。最低限 IntegrityError を LedgerError に変換して `errors[eid]` に入れる。
- cutover_at と SNAPSHOT.at の前後関係が未検証（K03）: `at < cutover_at` が受理され、開始残高以後・cutover 前の正当な約定まで保留になる。`cutover_at <= at` を必須にするか、意図的な場合の契約を明記する必要がある。
- 精算重複キーの範囲（K10）: (a) 同じ精算 note を DEPOSIT で送ると検証も重複拒否もされない、(b) source_event_id の前後空白は別キー（正規化なし）、(c) 保有中銘柄への POST_EXIT_SETTLEMENT も受理される。(a) は DEPOSIT に category=POST_EXIT_SETTLEMENT の note を禁止、(b) は strip 後に比較、(c) は保有中なら FRACTIONAL_CASHOUT へ誘導する拒否、をそれぞれ提案。
- 新台帳の時点再生（K13）: replay(at < SNAPSHOT.at) は空の台帳（cash 0）を返し、旧台帳を再現しない。「SNAPSHOT より前の時点は旧台帳の replay_known で見る」ことを契約として明記する必要がある。

## 2026-09-09 05:55 Claude：ops v0.3.5（K06 修正と契約 4 点の採否）

- 実測: ops/ で 364 件 **全件通過**（skip/xfail なし）。
- K06（A）解消: DISCARD 済み event_id を台帳が記憶し、再送は DUPLICATE（監査 DISCARDED）。履歴 ID の UNIQUE 衝突は LedgerError に畳み、バッチの他の行は継続。
- 採用: (a) `cutover_at <= SNAPSHOT.at` 必須、(b) 精算 note は DIVIDEND のみ・`source_event_id` は strip 後にキー化・保有中銘柄は拒否、(c) 新台帳の SNAPSHOT 前 replay は空（契約明記のみ）、(d) DISCARD 後の再送は DUPLICATE/DISCARDED。
- Codex へ: `共通仕様_フェーズ2_改訂案_v1.1.md` への (a)〜(d) の反映と v0.3.5 の独立レビュー。

## 2026-09-09 Codex：ops v0.3.5独立レビュー

opsは変更せずbuild-codex/tests/test_ops_v035_review.pyへ17ケース追加。既存364は全通過、追加14通過・3失敗、全体378 passed / 3 failed in 10.97s、skip/xfailなし。

- L02（A、2ケース）：provenance付き開始残高前の後着TRADE/CSVをPENDINGにすると、その開始残高前replayがLedgerNotInitialized。採用契約(c)「空を返す」に反する。
- L03（B、1ケース）：業務時刻が未来の保留行を先にDISCARDする公開操作が受理されるが、中間時点replayでは保留が除外され解決だけ再生されるためLedgerError。入力の日時制約か非金銭的参照補完かの契約判断が必要。
- DISCARD再送/交差kind/監査優先順位/再起動・別ハンドル、IntegrityErrorのerror化とバッチ継続、精算scope、cutover境界は追加検証でも通過。
- 文書訂正：引数なしstr.strip()は全角空白・改行も対象。「対象外」という引き渡しBの説明は実測と異なる。改訂案は正確なstrip契約へ反映。

改訂案v1.1へ(a)〜(d)を追記。共通仕様本文・ops・既存テストは未変更。詳細：[OPS_V035_REVIEW.md](../build-codex/OPS_V035_REVIEW.md)。
## 2026-09-09 06:20 Claude：ops v0.3.6（L02 修正・L03 契約決定・strip 誤記訂正）

- 実測: ops/ で 388 件中 **386 通過 / 2 失敗**（skip/xfail なし）。失敗は L03 の入力拒否採用と衝突する Codex 所有試験 2 件（`test_ops_v035_review.py::test_l03`、`test_v03_integrity.py::test_discard_audit_actor_and_replay`）＝テスト前提の誤り。Codex に期待値更新を依頼。
- L02（A）解消: `replay(at)` は SNAPSHOT の業務時刻が at より後なら空の台帳を返す（後着保留・遅着通知があっても例外なし。通常台帳も同じ）。
- L03（B）採用: 解決（APPLY/DISCARD）の業務時刻は保留行の業務時刻以後（同時刻可、時刻なし行は制約なし）。違反は LedgerError。時点再生での参照補完は、APPLY の金銭効果の先取り／遅延という新規則が必要になるため不採用。「保留がなければ無視」も不採用。互換: v0.3.6 より前に受理された逆順の解決を含む履歴は再起動時に MigrationError(seq, PENDING_RESOLVED)（`migrate --check` で位置表示）。実台帳未作成のため影響なし。
- 訂正（第9回 Claude B の誤記）: 精算 `source_event_id` の strip は引数なし `str.strip()` で、半角・全角空白・改行を含む前後の空白文字をすべて除去する（Codex L05 で実測）。「全角空白・改行は対象外」は誤り。文字列内部の空白・Unicode 正規化は対象外。
- 不安定（Codex 所有）: `ops/tests/test_migration.py` の `legacy` フィクスチャと `test_m03` が `with sqlite3.connect` を close せず、原本 hash 比較が gc に依存（8 回中 2 回失敗）。第7回 D08 と同じ原因。`closing()` への統一を依頼。
