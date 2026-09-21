# 主系と通知器の模擬結合設計

作成: 2026-09-11 08:11 JST、Codex並列調査。設計メモのみで、通知器の結合実装・実配信・実注文・見張り再起動を行った記録ではない。Claudeの制限解除後もユーザーの再開指示まで両見張りは停止を維持する。

参照: [RUNNER.md](RUNNER.md)、[PHASE2_INTEGRATION.md](PHASE2_INTEGRATION.md)、[順序4仕様案v0.2](共通仕様_フェーズ2_順序4_修正提案_v0.2.md) §3/7/8/9、`aitrader/runner.py`、`aitrader/review_runner.py`、`ops/aitrader_ops/notify.py`。共通仕様本文は変更しない。以下の「案」は未採用契約として区別する。

## 現在の接続状態

主系の `run_reviewed_mock` は明示された両judgeのstub記録を審査し、`runner.run` がゲート通過候補を台帳CREATED→APPROVEDとする。資金・株数の予約はcreate_notice時点で発生する。`orchestration.sqlite`のoutboxは通知案の保存場所であり、Notifierの配信outboxではない。

NotifierはAPPROVED/SENTの候補をenqueueできる。enqueueだけでは送信せず、flushのstub成功では模擬台帳にもSENTを記録する。したがって既存runnerの `delivery=NOT_SENT` を配信結果として上書き・流用しない。SENTはstub受付成功を意味し、LINE受付・到達・注文の実績ではない。

## 提案する段階と保存

最初は「通知案の準備」だけを既定とし、明示した模擬配信操作でのみflushする案。`transport='stub'`を固定し、結果列は利用者または試験が明示する。列不足は現Notifierどおりfailure。実LINEや実審査へのフォールバックは設けない。

実行先はinitialize_mockで作成したDropbox外の専用home。既存のledger.sqliteとorchestration.sqliteに加え、専用 `notification.sqlite` と結合操作のjournalを置く案。実台帳や別書込元とは共有しない。NotifierとLedgerはfinallyでcloseする。

| 段階 | 入力・処理 | 永続出力 |
|---|---|---|
| 審査と予約 | 固定Proposal、両模擬Verdict、時点評価→既存runner | APPROVED、予約、runnerのNOT_SENT結果 |
| 通知計画固定 | 保存済みAPPROVED候補・Verdict・表示contextを照合しrender_message | kind、候補ID/hash、本文、設定版、表示値の基準時刻、計画ダイジェスト |
| enqueue | 固定計画を同じkeyで登録。create_noticeは呼ばない | NotifierのPENDINGと既存key/duplicate結果 |
| 明示模擬配信 | 状態再確認→flush(stub) | 独立した `mode=mock, transport=stub` 配信結果、月予算、alerts |
| 結果照合 | 耐久SENTをreconcile_sentで台帳へ反映 | 修復keyと修復操作時刻。実送信済みとは表示しない |

各段階は単一の書込主体へ直列化する。現runner-lockはrunner同士しか保護しない。通知準備、flush、Webhook模擬入力、CSV等を同時にこの台帳へ書く構成にはしない。将来の共通ロック化は別の実装と並行試験が必要。

## 変換と予約・枠の扱い

候補のBUYはNEW、SELLはEXITへ変換する。主系generateがSELLを生成できるという意味ではなく、SELL結合は検証済み合成Proposalで試験する。render_messageはゲートの代用品ではない。保存した両Verdictと同一候補であることを結合側でも照合する。

現runnerの候補outboxにはVerdictや表示contextがないため、bodyだけから本文を生成しない。保存済みrun入力と関連づける。name、account_confirmed_at、予約後available_after、日次送信済み件数と上限を時点付きで固定する案。再実行時に現在余力で本文を作り直すと同一key内容衝突になるため、計画済み本文を再利用する。未確認値を0や確認済みへ補完しない。

予約はrunnerだけが行い、結合側は再予約しない。現在のrunnerはBUYのINTENT/APPROVEDを日次枠として数えるため、NotifierのSENT/UNKNOWNをそこへ再加算すると同じ候補を二重に数える。統合後の日次枠の基準は候補IDの集合として設計し、月通知予算と資金予約を別々に保持する必要がある。月予算は全kind合算でNotifierに委譲する。UNKNOWNの翌月retryは初回予約月を保持する現契約を維持する。

候補key案は `(execution_day, proposal_id, packet_hash, NEW/EXIT)`。run_idを変えて重複候補を出さない。Notifierは同じkind/候補IDにも重複防止があるが、異なるkeyで本文が変わった場合も既存行を返せるため、結合側で計画ダイジェスト衝突を先に拒否する。

| runner状態 | 通知案への写像 |
|---|---|
| APPROVED候補 | NEWまたはEXIT |
| NO_SIGNAL | RECONCILE、status=NO_SIGNAL。日付単位1件 |
| REVIEW_INCOMPLETE | RECONCILE、status=REVIEW_INCOMPLETE。未完了の注文条件を載せない |
| NOT_APPROVED | RECONCILE、status=REJECTED |
| REVIEW_INVALID / SYSTEM_ERROR | RECONCILE、status=ERROR。審査不能・障害の理由を区別 |
| CANDIDATES全体要約 | 候補カードに加えて別通知するかは未決。自動的に追加通数を消費しない |

一部承認＋一部未完了の場合、承認カードと未完了状態を区別する。例外で正常resultが得られなければNO_SIGNALへ変換しない。failure.jsonを信頼できる固定障害文面に変換する案とし、生の例外や環境情報を通知へ転記しない。

## STOPと締切

現runnerはhome/STOPだけを読む。Notifierは自身の永続STOPと設定stop_fileのORを読む。予約前の新規停止を統一するには、結合側が共通停止状態をrunnerへ明示注入できるAPIが必要。NotifierのSTOPをファイル削除・作成で間接的に同期しない。STOP中に既に存在するNEW予約は解放せず、EXIT/RECONCILE/RISKは通知器の規則で継続可能とする。

現runnerは07:15以上をREVIEW_INCOMPLETE、Notifierは07:15超過で候補を抑止する。この境界差は実在する。結合案は候補について07:15ちょうどもflush開始を拒否し、より厳しい主系境界を維持する。ただしflushは全待機行を一括処理するため、候補だけ止めて同じoutboxの照合・警告を継続するフィルタAPIが未整備。契約を採用して実装・境界試験するまでは、締切後flushの結合を有効化しない。

STOPPED/BUDGET_BLOCKEDは現在flushの観測結果で、待機行自体はPENDINGのままの場合がある。観測結果を永続的な取消と見なさない。解除後や予算回復後にも期限を再検査する。UNKNOWNはSTOP・期限・後続failureでも未送信と断定せず、予約とretry_keyを保持する。

## DBを跨ぐ停止と再実行

3つのDB全体に原子性があるとは扱わない。完了runの再実行は当時の結果の参照であり、現在の送信許可ではない。新たな模擬flushは現在の台帳・STOP・期限を必ず検査する。

| 停止位置 | 今回設計の回復方針・制約 |
|---|---|
| INTENT保存後、create_notice前 | 既存runnerの同run・同入力再開を使う |
| CREATED/APPROVED後、runner outbox前 | 現runnerは照合要求で停止する。既存予約を消して作り直さない。自動修復未実装 |
| runner完了後、通知計画前 | 保存済み入力を照合して計画作成可能。送信前には現在状態を再確認 |
| 計画保存後、enqueue前 | 固定本文・keyでenqueue。台帳を再作成しない |
| enqueue後、結合journalの記録前 | 同じkey・同じ本文のenqueueで既存行を回収し、戻された実際のkeyを保存 |
| SENDING中 | 現Notifierのclaim回収に委譲。古いclaimはUNKNOWN扱い。未送信扱いで予約解放しない |
| outbox SENT後、台帳SENT前 | reconcile_sentで既存成功を再適用。flushによる新規送信は行わない |
| 台帳SENT後、配信結果ファイル前 | 結合journalとNotifier公開結果から再出力。runnerのNOT_SENT履歴を書き換えない |

現Notifier.statusは集計であり、任意keyの本文・状態・成功時刻を取得する公開APIではない。SENT行はflush結果にも再登場しない。結果ファイル喪失後の完全再構築には、結合journalの耐久記録またはNotifierの公開読取API追加が必要。内部SQLを結合の契約にしない。reconcile_sentの時刻は修復時刻であり、初回送信成功時刻の復元とは異なる。

## 未決契約と次の実装条件

1. 共通STOPのrunner注入方法と、予約・通知・Webhook/CSVを直列化するロック範囲。
2. 07:15ちょうどの通知境界統一、候補停止中もRECONCILE/RISKを処理するflush選択API。
3. stub SENTと既存NOT_SENT履歴を分離した利用者表示・保存形式。
4. 固定通知計画の入力同一性（参考情報、表示context、受信者・設定版を含む）と再実行衝突時の公開エラー。
5. 日次候補枠の集合基準、日付単位NO_SIGNALと複数runの状態通知の更新・重複規則。
6. runnerの台帳片側保存の修復API、Notifierのkey単位読取API、初回成功時刻の保持。

これらは既存契約の緩和として黙って実装しない。本メモは未決を残したまま試験可能な準備経路を切り出す資料であり、実通信の承認を求めるものではない。

次の受入試験案: 同run/別runの同候補再実行、変更context拒否、予約額不増、SENT/UNKNOWNの日次二重計数なし、STOP前後のNEW/EXIT、07:15前・同時・後、部分承認＋未完了、日跨ぎNO_SIGNAL重複、月跨ぎUNKNOWN、予算上限、計画/enqueue/成功記録の各停止点、reconcile_sent後の無再送、全操作の実通信禁止。pytestはDropbox外basetempとno:cacheproviderを使用する。本メモ作成では試験を実行していない。

終了時刻: 2026-09-11 08:11 JST。引き渡し不要（Codex単独の次工程用）。

## 通知準備 第1段階（2026-09-11 実装）

aitrader.notification_plan.prepare_notifications(home, run_id, execution_day, *, contexts, settings) を追加。contextsは候補IDごとの明示表示値、settingsは通知描画設定。保存済み模擬runのAPPROVED候補を台帳・両Verdict・実行DB原本に照合しNEW/EXITのカードを準備する。mode=mock、delivery=NOT_SENT、本文とaltTextに模擬警告を含む。enqueue/flushは行わない。

通知計画はruns/<day>/<run_id>/notification_plan.json、同一性はnotification-plans.sqliteへ記録。候補・審査成果物のhashは主系が実行DBのmanifestへ固定する。旧runでhashがない場合は自動信頼せず新しい模擬runを要求する。保存ファイルだけを揃えて書き換えても原本照合で拒否する。台帳と同じrunner-lockで主系実行と通知準備を直列化する。

第1段階ではNO_SIGNAL等の状態通知をまだ生成せず、候補がなければplansは空。空計画は配信成功や障害なしの証明ではない。状態通知は次段階で追加する。送信可否を判定するAPIではなく、現在時刻・STOP・予算・再送の検査は将来の模擬送信段階に残る。

## 通知準備 第2段階（2026-09-11 実装）

include_status=True を明示すると、当該runのNO_SIGNAL/REVIEW_INCOMPLETE/NOT_APPROVED/REVIEW_INVALIDについてRECONCILE状態カードを追加する。既定Falseは候補カードのみ。キーは日付:run_id:STATUS、plan_type=RUN_STATUS、source_statusを保持。固定文面と承認/未完了/不正の該当件数だけを表示し、未承認候補の注文条件・リンク・生の理由を載せない。該当件数は重複し得る旨を表示する。

状態は当該runの履歴であり日次全体の結果ではない。部分承認では承認カードと状態カードを分ける。CANDIDATESだけなら追加要約なし。include_statusも固定計画入力に含まれ、同runの後付け変更を拒否する。実配信・enqueue・flushは未実装のまま。

第1段階試験20件10.02秒、第2段階14件7.46秒が個別通過。第1段階の初回2失敗は状態カードまで対象としたテスト前提誤りBであり、承認候補限定の既定動作を検証する形へ修正。状態カードの期待は第2段階の独立ファイルで検証している。

次工程: stub enqueue結合前に、通知器のkey単位読取と固定計画の照合、STOPと締切の統一を設計・受入試験化する。現時点で安全に準備可能なカードの範囲を超えた送信や自動復旧を行わない。

## 通知キュー段階（2026-09-11 実装）

aitrader.notification_queue.enqueue_prepared_notifications(home, run_id, execution_day, *, now, contexts, settings, include_status=False) を追加。事前にprepare_notificationsで同じ全設定（line.allowed_user_idを含む）を固定する。未準備の入力を自動準備せず拒否する。登録日時は執行日と同日。実行原本・固定計画・台帳を再検証し、同じrunner-lock内でNotifierのstub enqueueのみを呼ぶ。

Notifier.get_entry(key)は保存された通知を公開項目のコピーとして返す。欠損KeyError。message/attemptsは独立コピーであり、読取で状態や予算を変えない。結合側はkey/kind/proposal_id/message/recipientを照合する。既存同keyの不一致は全件事前検査で拒否。別key同候補の重複はenqueue後の返却値で拒否するため、前のカードだけ登録されている場合がある。跨DBの全件原子性は保証しない。同じ入力/keyで再実行して回復する。

receiptはnotification_queue.json。action=ENQUEUE_ONLY、sent_by_this_call=False。既存SENT/UNKNOWN/SENDINGを含む場合delivery=PRIOR_ATTEMPT_EXISTSとして未送信と断定せず、各entries.stateを参照する。新規PENDINGのみ等の場合はNOT_SENT。候補台帳が既にSENTなら準備側のAPPROVED制約で拒否するため、送信後の包括的回復APIの代用ではない。

この段階は送信許可ではない。flush・実送信・予約追加はしない。STOP/07:15/予算の送信前検査と送信結果修復は、次の模擬配信段階の契約・試験に残る。

## 選択送信APIの方針（2026-09-11）

次段階の模擬配信をrun限定で行うため、Notifier.flush(now=..., keys=None)の選択APIを追加する。Noneは従来の全件処理。明示キー列はその通知のみ処理し、対象外行のSENDING回収・期限更新・再送を行わない。空選択は無動作、未知キーや不正選択は最初の送信前に拒否する。既存月予算は共有DB全体で維持する。

これは送信範囲を限定する追加契約で、主系からflushを自動呼出する許可ではない。主系結合は固定計画・recipient・STOP・厳しい07:15境界を照合する別段階が必要。reconcile_sentは全件対象のままなのでrun限定復旧に流用しない。

## 2026-09-11 キューと選択APIの追加検証

get_entry独立6件、キュー基本7件、キュー反証8件、境界12件、選択flush19件を追加した。境界の初回5失敗はA（非文字列run_idのTypeError2件、不正日付拒否前ロックDB作成3件）で、公開RunError化とロック前入力確認により12/12通過5.92秒。キュー反証8/8通過6.71秒、選択API19/19通過0.60秒。

receiptは送信履歴と今回の無送信操作を分離した。既存UNKNOWN/SENT/SENDINGがあればPRIOR_ATTEMPT_EXISTS、action ENQUEUE_ONLY・sent_by_this_call=Falseとする。UNKNOWNを未送信扱いで予約解放しない。

主系からの模擬flush呼出しはまだ追加していない。安全な次工程は、準備済み選択keyを再照合し、home/STOPと永続STOPを合成し、07:15以上の候補を選択から除外して状態通知だけ継続する接続と反証。実送信・実審査LLM・見張り再開は行わない。

## 模擬配信結合（2026-09-11 実装）

aitrader.mock_delivery.deliver_prepared_mock(home, run_id, execution_day, *, now, contexts, settings, stub_results, include_status=False) を追加。固定済み計画・原本・キュー登録receipt・全対象行をrunner-lock内で再照合し、実行別keysだけstub flushする。計画・queueを自動作成せず、明示された結果列だけ使う。

主系境界を維持し07:15以上のNEW/EXITはSKIPPED_DEADLINE、home/STOPまたは永続STOPはNEWをSTOPPEDとする。これらは操作観測でありUNKNOWN状態や予約を変更しない。状態通知は別に継続できる。既存SENTはALREADY_SENTで再送しない。送信後は既存計画再照合に限り台帳SENTを許容し、新規prepare/queueのAPPROVED制約は維持。

reportはmock_delivery.json。delivery=SIMULATION_ONLY、real_sent_by_this_call=False、simulated_sent_by_this_callを分離。entriesは耐久状態、resultsはこの操作の観測。実LINE受付・到達・実注文の証拠ではない。キュー登録・更新・過去試行より前の時計へ戻す実行を拒否する。

初期独立15件8.85秒通過。初回の1失敗はSENT時の正常な台帳イベント増加まで禁止したテスト前提誤りBで、予約不増・SENT記録を確認する形へ修正。時刻逆行・レポート喪失等の反証を追加中。台帳とoutboxの不一致に対する完全自動復旧は別契約である。

## 明示的な模擬台帳修復（2026-09-11）

Notifier.reconcile_sent(now=..., keys=None)も選択APIに対応した。Noneは従来全件、空選択は無動作、未知キーは全件事前拒否。選択外の台帳・通知キュー・試行履歴・予算を変更しない。台帳に記録する時刻は修復時刻で、初回成功時刻を復元したものではない。独立試験15/15通過1.07秒。

主系のreconcile_prepared_mock(home, run_id, execution_day, *, now, contexts, settings, include_status=False)は既存計画・原本・receipt・全対象キューを再照合し、runner-lock内で当該runの選択照合だけを実行する。flush/enqueueは呼ばず、UNKNOWNを成功扱いしない。mock_reconciliation.jsonのrepaired_keysは今回修復したキーであり、再実行で空になることは過去の修復失敗を意味しない。模擬配信と同じ同日・履歴時刻制約を適用する。独立試験8/8通過15.84秒。初回fixtureの承認設定漏れ1件はBとして修正。

台帳SENT・キュー未SENTの逆方向不一致は拒否する。台帳APPROVED・キューSENTは通常配信ではALREADY_SENTのまま再送せず、明示修復APIで扱う。成功後レポート保存に失敗しても、耐久キューと台帳から再実行できる。DBを跨ぐ完全な原子性は保証しない。

## 時刻の解釈と追加反証

原本と一致するmanifest.started_atはrunnerが審査結果を処理した確定時刻であり、review_runnerへの審査開始引数とは別である。通知登録・模擬配信・明示修復はこの時刻より前を拒否する。未来や不正な審査応答を報告するREVIEW_INVALIDカードまで止めないため、無効応答のreceived_atの最大値を下限にはしない。

審査07:10・通知07:00の初回登録、古いキューからの配信、成功記録からの修復を反証し初回A3。修正後4/4通過10.72秒。通常配信15件、時刻5件、保存障害回復5件、デモ8件、選択照合15件を含む全体909件は82.63秒で通過した。この後の明示修復8件・時刻4件は個別検証であり、全体統合結果とは区別する。

## 2026-09-11 08:44 JST 統合

全体940件101.25秒通過。再enqueue時に前回receipt・queue作成/更新・試行時刻より前を拒否する。初回反証A3、修正後4/4通過10.55秒。この問題自体による不正再送は再現せず、観測履歴の巻戻しを修正したもの。

主系enqueue/deliver/reconcileの全操作は同一runner-lockを保持する。別SQLite接続による同時取得拒否と例外後解放を含む5/5通過11.47秒。standalone Notifierはこのロックを使わないため、同じ専用homeへの直接操作との混用を保証しない。見張り停止は維持する。

合成デモは既定NOT_SENT、--simulate-deliveryを明示した場合だけSIMULATION_ONLYへ進む。result/plan/queueは準備時点の履歴で、最新の模擬結果は別mock_delivery欄。独立10/10通過5.46秒。[PowerShell手順と読み方](MOCK_DEMO.md)。現時点のデモ候補は手作りであり、市場DBからの実候補生成は別途結合中。

## 観測時刻と市場データの追加修正

07:16にSKIPPED_DEADLINEを観測後、07:11で同じrunを再実行すると模擬送信できる問題を確認。STOP解除後の逆時刻操作、修復観測の巻戻し、後続enqueueの逆時刻も含め初回A5/B0。登録receipt・模擬配信・修復の既存observed_atを共通検査するよう修正し、同時刻許容を含む6/6通過13.89秒。観測ファイルを失った場合までの耐久時刻保証ではなく、外部改変や直接Notifier操作を混在させない専用homeの契約である。

`synthetic_pipeline`は共通合成データを読み、実戦略・generateの無改変Proposalを模擬両審査へ渡す。seed42実測7.36秒、5候補中2承認・3日次上限拒否、予約478856円、未送信2件。独立4/4通過9.03秒、入力境界15/15通過1.80秒。合成eventsとlotsを生成前に明示し出典hashを保存する。実イベントを確認したという意味ではない。

実生成経路の古い価格利用をA1として反証し、generateはprice_dayがas_ofと異なれば候補ゼロ時も拒否する。生成器反証4/4通過7.78秒。外部Proposalを受けるrunnerも候補の基準日価格を有限正数必須へ修正。欠損/別銘柄のみ/古い日付/ゼロ/負値で承認するA5と、NaN/無限大で生例外になるA3を確認した。新9件は修正後全通過。既存fixtureへ省略されていた価格を明示し、既存価格欠損試験は明示削除で維持、29/29通過13.36秒。期待値を弱めていない。

runnerの台帳片側保存は自動復旧せず、[復旧案と読取診断](RUNNER_RECOVERY_PLAN.md)へ分離する。通知側の明示修復で未完了runnerを完成させることはできない。

## レポート喪失時の時刻保持（2026-09-11）

前述の「観測ファイルを失った場合の耐久時刻は保証しない」という制約に対し、新しい操作ではnotification-plans.sqliteのnotification_clockへrun単位でobserved_atを記録する。全事前照合後、最初のenqueue/flush/reconcileの前に別DBへcommitし、共通runner-lockを保持する。STOP・締切・空選択の無送信操作も観測として記録する。操作やレポート保存が後で失敗しても時計は残し、同時刻以降だけ再実行を許可する。

旧DBは初回有効操作時に時計表を追加する。既存レポートの時刻下限も併用する。過去に時計がなくレポートも失った操作の時刻は復元できず、台帳DBや時計の外部削除・改変の安全性を保証するものではない。入力不正・原本不一致・既知キュー不一致は時計記録前に拒否し、不正入力で将来時刻を先取りしない。登録中に判明する部分登録衝突など、操作開始後の失敗は時刻を保持する。

独立障害回復5/5通過18.88秒、移行・時計整合8/8通過20.67秒。初回実行時に実装済みで修正前redは未観測。別runの時計は独立し、時刻の異常を自動補正しない。

## 通知直前のcandidate原本照合（2026-09-11）

保存済みrun結果だけで通知準備へ進まない。通知共通の_prepare_lockedで、全candidateのowner/day/side/hash、合法state、判定原本、APPROVED outbox原本をrunnerと同じSELECT検査で確認する。candidate missing/INTENTや保存resultとの不一致も拒否する。

検査はLedgerを開く前、通知plan保存・binding・queue・clockの変更前に実施する。prepare/enqueueに共通で適用し、既存の模擬配信・照合もこの準備検査を通る。現在の市場条件を再審査する機能や、完了履歴の自動再承認ではない。診断だけ実行してから不正状態を見逃すのではなく、通知操作自身が必要な原本を確認する。

新規反証17件で候補owner/day/side/hash改変、欠損、INTENT、結果不一致、outbox欠損をprepare/enqueue両方で拒否し、通知DB非作成と既存状態不変を確認。最終全体結果はREADME末尾参照。非協調外部writerの完全排他や複数DBの原子性は追加保証しない。

## 通知入口のpathと保存hash検査（2026-09-11）

prepare、enqueue、模擬deliver、明示reconcileは、raw homeをresolveする前にhomeと既存親componentを検査する。runnerの基本marker・台帳・原本DB・共有lockに加え、notification-plans.sqlite、notification.sqlite、managed-stop-clock.json、STOPを通常fileかつlink/junction/reparse pointでないことを確認する。SQLiteの-wal、-shm、-journalも存在する場合は同じ型とlink検査を行う。writer入口のため通常のsidecarの存在自体は拒否しない。

runs/<execution_day>/<run_id>以下の原本、通知計画、queue receipt、模擬配信・照合reportも、resolve前に各親componentのlink/junction/reparse pointを検査する。これは入口で観測できるpathを安全側に拒否する検査であり、操作中の非協調外部writerとのTOCTOUを完全に排除する保証ではない。

Notifier.get_entryは保存行を返す前にcontent_hashを再計算し、kind、proposal_id、messageの一致を全行で検査する。enqueue、deliver、reconcileの主系はclock書込み前にget_entryで対象行をすべて読み、保存hash破損を拒否する。content_hashの対象はkind、proposal_id、messageであり、keyとrecipientはhash対象外である。そのため主系は別途keyとrecipientを固定計画に照合する。get_entryの返却shapeにcontent_hashは追加せず、既存呼出しとの互換性を保つ。

## 保存行の状態・履歴と冪等な計画参照（2026-09-11）

clock前のget_entry検査は、保存hash/recipientだけでなく既存5state、attemptsの辞書listと各at、created_atおよび非NULLのexpires_at/claimed_atのaware日時も確認する。未知stateをUNCHANGEDとして通常処理へ流さない。ops由来の保存内容エラーは主系の固定RunErrorへ変換し、既存の呼出側契約を保つ。固定planの既存内容が一致すれば再保存せず、mtimeを変えない。plan欠損時は元のbindingから再生成する既存経路を維持する。

SENT照合は対象noticeをすべて先行照会してから反映する。後続noticeの既知欠損で先頭だけSENTへ変えることを防ぐが、検査後の外部変更や途中DB故障を跨ぐ一括atomicityは保証しない。実測・試験前提修正の記録はREADME末尾参照。

## 候補期限・再試行キー・plan本文の照合（2026-09-11）

主系clock前のget_entryは候補の保存期限と台帳Proposal期限をJST正規化後の同一時点で照合し、候補期限欠損を拒否する。非候補の期限Noneは維持する。retry_keyは非空文字列で、既存各attemptのキーと一致を要求する。状態別attempt件数やUUID形式を新しい契約として強制しない。初回空attemptには過去キーの根拠がなく、行と履歴を整合的に変更する外部操作の検出は保証しない。

通知計画DBのplans.bodyは保存fingerprintだけで信用せず、そのcanonical digestを同一入力から再生成したpreparedと照合する。不一致・解析不能を固定RunErrorとして、カード登録やJSON再生成前に拒否する。正常な欠損JSON再生成は維持し、確認済みの再生成値を使用する。既存hash/schemaやkind/sideは変更しない。実測はREADME末尾を参照。

## direct Notifier APIの時刻逆行（当初の未決記録、2026-09-11）

一時DBによるオフライン再現では、07:00に登録したNEW通知を07:10の模擬failure後に `Notifier.flush(now=07:05)` で再試行すると、処理は拒否されずSENTとなった。保存された `updated_at` は07:05へ戻り、attemptは07:10、07:05の順で残った。また、queue側をSENT・`updated_at=07:10` とした状態で `Notifier.reconcile_sent(now=06:59)` を直接呼ぶと、台帳のAPPROVEDがSENTへ更新された。実通信は行っていない。

主系の模擬配信・明示照合は、既存の `mock_delivery` が `created_at`、`updated_at`、全attemptの時刻より前の `now` をclockや配信・台帳変更の前に拒否する。このため上記は主系専用home経路の再現ではなく、direct Notifier APIを主系外から呼んだ場合の境界である。

direct `flush` / `reconcile_sent` にも選択対象queue履歴以後の単調な `now` を必須とするかは未決であり、今回修正していない。採用する場合の最小案は、対象行の既存整合検査後かつ全副作用前に、`now` が `created_at`、`updated_at`、いずれかのattempt時刻より前なら拒否することである。空選択と指定外行の非検査は既存どおり維持する。

この案は、台帳の通知状態時刻を通知作成時刻以後へ一律制限する規則とは別である。ops README Q04では、作成時刻より前のAPPROVED/SENTを含む既存履歴との互換性から、その広いLedger規則を不採用としている。direct APIのqueue履歴下限を検討する際も、Q04を暗黙に変更しない。

### 後続の限定採用

上記の再現後、queue履歴下限だけを限定採用した。`flush`は選択されたactive行、`reconcile_sent`は選択されたSENT行を全件先行検査し、呼出しの`now`が各行の`created_at`、`updated_at`、いずれかのattemptの`at`より前なら、claim、alert、stub、予算、queue、台帳を変更する前に拒否する。同時刻と別timezone表記の同一時点は許容する。

空keysは従来どおり無操作、指定外行は非検査とする。`flush`のterminal行と`reconcile_sent`の非SENT行を新しい本文検査対象へ広げず、既存filterを維持する。DB schema、content hash、state遷移は変更しない。Ledger通知状態の時刻を通知作成時刻以後へ一律制限する規則は引き続き不採用で、Q04の既存履歴を変えない。試験件数はrootの後続実測を参照する。

## 第18回：制御未来時刻と変更内容の重複識別（2026-09-12）

直接Notifier.stop/resumeはevent_atがnowより未来ならINVALIDで拒否する。JST正規化後に比較し、DB履歴・kvへ記録しない。WebhookのINVALID、読取stop_statusの未来履歴UNKNOWNと同じ時刻境界を適用する。遅着は既存の監査・IGNOREDを維持し、同時刻を許可する。すでに破損した履歴の自動復旧ではない。

enqueueで別key・同一候補kind/proposal_idの内容変更要求があった場合、旧key/state/duplicate=Trueにchanged=Trueを付ける。既存本文の変更や再送は行わない。完全重複の応答はそのまま、同keyで内容変更した場合のValueErrorも維持する。利用側はchangedを訂正内容が保存されなかったことの識別に使い、自動的に別候補や訂正通知へ変換しない。詳細と実測は[第18回対応報告](OPS_REVIEW18_REPORT.md)。
