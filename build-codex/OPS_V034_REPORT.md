# ops v0.3.4：照合済み新台帳と売却後精算金

2026-09-09、第7回BをCodexが実装。既存テストと共通仕様本文は変更していない。実台帳の作成・昇格、実口座接続、実審査、LINE送信は未実施。

## 実装した契約

### 新SNAPSHOTと出所

`init_snapshot(cash, positions, open_orders, at, provenance=None)` を追加した。既存の引数だけの呼出しは従来動作を維持する。新しい別台帳ファイルへ照合済み残高を登録する場合はprovenanceを明示する。既存SNAPSHOTの置換は禁止を維持。

provenanceは辞書でmethod=RECONCILED_OPENINGが必要。未知methodや辞書以外は拒否する。必須項目：source_path、source_sha256、source_last_seq、reconciliation_id、reconciliation_record_path、reconciliation_record_sha256、actor、reconciled_at、reason、cutover_at、previous_policy_version。空文字は拒否、hashは64桁16進、旧最終seqは正整数（bool拒否）、日時はタイムゾーン付き。検証失敗はLedgerError、SNAPSHOTを保存しない。出所をSNAPSHOT payloadへ保存し、再起動でcutoverを復元する。

これは出所の**形式検証**。指定パスを開いて原本hashや照合記録の真実性を照合する処理、担当者の認証、原本の静止確認は自動実装していない。記入しただけで照合済みを証明したことにはならず、OPS_V033_REPORTの手動照合・hash確定手順を前提とする。旧台帳の全event_idも持ち込まない。

### cutoverによる保留

provenance付き台帳で `TRADE / CSV_FILL.at < cutover_at` は自動適用せずPENDING。理由には「cutover 前の約定」を含める。境界ちょうどは受理対象（通常の数量・通知等の検査は別途必要）。時刻なしもこの台帳では境界を確認できないため保留する。provenanceなしの既存台帳は従来動作を維持し、CSVの時刻なし保留もそのまま。

通知IDが新台帳に存在しなくても、境界前の報告は先に保留して失わない。TRADEのReportResultに末尾フィールド `pending: bool=False` を追加。保留時はapplied=False、pending=True、ignored_reasonに理由。CSVは従来のImportResult.pendingへIDを返す。pending_rowsとingest_attemptsのPENDING監査へ保存し、イベントと監査は同一トランザクション。再送は重複扱いで新しい残高イベントを追加しない。同じIDの内容改変は既存のID_PAYLOAD_CONFLICT監査対象。

境界遮断行はresolve_pending(APPLY)で迂回できない。照合済み開始残高に含まれるか確認してDISCARDするか、別途訂正方針を立てる。時刻なし遮断行も直接APPLYせず、取得済み明細で時刻を確定して照合する。通常CSV保留行のAPPLY契約は変更しない。DISCARDした行を再送すれば再び保留され得るため、取込側で処理済みIDと照合記録を保持する。

cutoverは原報告の業務時刻による遮断であり、改変された時刻や境界以後の旧約定IDを一律に検知する機構ではない。保留TRADEを新たに扱う呼出側はappliedだけで成功と解釈せずpendingも確認すること。主系runnerはpending_rowsの存在で新規候補を停止する既存経路を使用する。

### 精算note検証・重複防止

DIVIDENDのnoteがJSONオブジェクトかつcategory=POST_EXIT_SETTLEMENTの場合に検証する。必須：settlement_type、code、source_event_id、source_document_sha256、reconciliation_id、actor、received_at、reason。settlement_typeはFRACTIONAL_CASHOUTまたはLIQUIDATION_CASH、hashは64桁16進、識別文字列は空不可、received_atはタイムゾーン付き。note.codeと引数code、received_atと引数atは一致必須（日時は同じ瞬間なら別UTCオフセットでも可）。amountは正整数円。

(category, source_event_id)を台帳全体のADJUST履歴から再構成し、同じキーは金額・銘柄・証憑が変わっていてもLedgerErrorで拒否する。BEGIN IMMEDIATE内で状態同期と重複判定を行うため、先に開いていた別ハンドルでも重複を検知する。拒否時は現金・保有・イベント件数を変えない。ADJUST拒否の独立監査行は既存実装同様、今回追加していない。

JSONでないnote、JSONオブジェクト以外、他categoryは従来どおり扱う。従ってこの保護は精算categoryを正しく明示した入力に対するもので、自由文へ書き換えても同じ精算を見抜く機能ではない。noteの証憑hashは形式検証のみ。DIVIDENDは投資由来の受取の暫定収納種別で、税務上の配当判定ではない。数量・原価は変えず、net_external_flowには外部入金として加えない（主系RUNNER.mdにも追記）。

既存履歴に、このcategoryで不正noteや二重精算が既に保存されている場合、今回の再生検証で停止し得る。自動修復・履歴書換えはしない。旧版の稼働台帳をそのまま開かず、コピーでcheckし、必要なら照合済み新台帳方式を検討する。

## 検証

ops/で指定対象 `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q` を実行。実際のPC起動は既存Pythonへ依存探索パスを追加したrunpy、`-p no:cacheprovider --tb=short`、bytecode無効化。

**325件＝324 passed / 1 failed in 10.77s、skip/xfailなし。全件通過ではない。** 既存286件は285通過・1失敗、新規test_v034_contracts.pyの39件は通過。内訳は共通162＋主系43＋ops120。

追加39件：provenance必須12・型6、TRADE/CSVの境界6、精算note必須8・不正値5、別ハンドル/再起動後の精算重複1、保留監査失敗時ロールバック1。

- J01：provenanceの12キー欠落、SNAPSHOT未保存。
- J02：不正hash、bool/0の旧seq、空担当者、naive/欠落日時（6）。
- J03：2経路×3時刻、保留永続化・監査・再送・APPLY拒否・再起動・replay/replay_known一致・DISCARD（6）。
- J04/J05：精算必須8キーと不正値5件（13）。
- J06：既存別ハンドル同期、再起動重複拒否、通常note互換（1）。
- J07：監査書込失敗で保留とイベントもロールバック（1）。

唯一の失敗はClaude所有I06で、同じ精算noteの2回目のadjustがLedgerErrorとなる箇所。**新契約と旧期待値の衝突＝テスト前提の誤り**と分類する。「二重計上される」という旧期待値を維持するための実装修正はしない。Claudeが次回重複拒否・1回分残高不変へ期待値を更新する。

I07は現在provenanceを渡さずに新台帳を作るため通過したまま。互換動作の試験としては正しいが、cutover保護を証明してはいない。次回Claudeは有効なprovenanceを渡すケースへ更新または別ケースを追加し、旧時刻の報告が保留されることを検証する。追加J03では今回その保護を確認済み。

## 運用追記・任意機能の採否

POLICY_UPGRADEを用いた規則移行はmigrate CLIのみとし、生SQLでのマーカー挿入・変更を禁止する。I04のような直接改変はアプリの検証境界外。今回のprovenance付き新台帳はinit_snapshot公開APIを使う別方式であり、旧台帳へのマーカー追加で代用しない。

手動取込運用では、全取込停止中のLINEトーク履歴と取得済み証券CSVを未処理の保管先として使い、移行・昇格を進められる。停止開始点・最後の処理ID・再開点と保留一覧を照合記録へ保存し、未処理報告を消さず、再開時にIDと業務時刻で照合する。自動取込を導入した場合はこの手順を流用せず、全書込停止と耐久キューが必要。OPS_V033_REPORTの「キュー未実装なら保留」はこの手動保管方式が成立する場合に限り緩和する。WAL反映、全接続close、静止hash、世代退避、差替え・復旧の他条件は維持する。

任意のmigrate --diffは今回見送り。新台帳と精算の保護に範囲を集中し、旧新逐次再生のエラー出力契約・全履歴走査の負荷・旧規則互換の追加検証を別工程とする。最初の乖離seqは引き続き手作業。未実装のオプションを使えるとは案内しない。

変更実装：ops/aitrader_ops/ledger.py、models.py、__init__.py（v0.3.4）。追加試験：ops/tests/test_v034_contracts.py。README・主系RUNNER.md・ISSUES・改訂案を更新。[次回Claudeへの依頼](Claude引き渡しプロンプト.md)。
終了時刻：2026-09-09 05:37 JST。開始時刻は未取得。[Claude引き渡しプロンプト.md](Claude引き渡しプロンプト.md) を更新済み、未送信。
