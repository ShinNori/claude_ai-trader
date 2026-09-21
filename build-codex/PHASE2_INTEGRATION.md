# フェーズ2 順序3：主系への台帳・通知ゲート組み込み設計

> 2026-09-11 追加更新：`aitrader.synthetic_pipeline`で共通合成市場→実戦略→packet_cli.generate→両模擬審査→ゲート→未送信キューまで結合した。seed42の実測は生成5件、承認2件、日次上限拒否3件。後述の「手作り候補のみ」は先行デモの履歴である。実データ・実AI・配信・日次自動運転まで完成した意味ではない。[合成市場の実行手順](MOCK_DEMO.md)を参照。

## 最新の実装範囲（2026-09-11、Codex単独開発）

以下の模擬専用経路を実装済みです。これは本書の計画全項目の完成や、実運用できる自動売買システムの完成を意味しません。下に残す過去の設計・担当・実装状況は履歴です。

- `review_runner`：事前に用意したClaude/Codex両者の模擬応答を検証し、台帳・ゲートへ接続する。実際のCLIは起動しない。
- `notification_plan` / `notification_queue`：通知文面を固定し、対応するキーを通知キューへ登録する。登録だけでは送信しない。
- `mock_delivery`：当該runのキーだけをstubで模擬送信する。主系の候補通知は07:15以上で除外し、STOP・履歴の時刻・固定した入力も確認する。
- `reconcile_prepared_mock`：模擬成功が通知キューへ残り、台帳反映だけが中断した場合、当該runの反映を復旧する。再送・再登録はしない。

実行例と結果の読み方は [合成デモの実行方法](MOCK_DEMO.md)、最新の全体試験結果は [README](README.md) を参照してください。模擬成功の `SENT` は実LINE送信や発注の実績ではありません。

現行デモの入口は、コード内で手作りした合成 `DEMO` 銘柄のProposalです。`packet_cli` による市場DBからの候補生成と、その後の審査・通知までの全経路はまだ一体として結合していません。実データの定時取得、実Claude/Codex CLI審査、実LINE送信、日次全体を動かす自動スケジューラ、主系の売り候補生成は未結合・未実装の残工程です。個別部品や模擬試験の存在を、これらの運用経路の完成と読み替えないでください。

実口座・証券発注には接続していません。Claudeの利用制限解除についてユーザーから再開指示があるまで、Codex・Claudeの両見張りは停止・無効化を維持します。

---

> 2026-09-08更新：下記は初期設計の履歴。現在は `aitrader/runner.py` に模擬専用の直列実行器を実装済み（[使い方・20件の検証](RUNNER.md)）。ops v0.2は時刻付きcreate_notice、reserved_shares、business_days等を追加済み。今回の再々レビューは [PHASE2_OPS_REVIEW2.md](PHASE2_OPS_REVIEW2.md)。完全な自動復旧・実CLI・配信は未実装で、設計の全項目を実装したという意味ではない。

2026-09-08。現段階は設計と模擬Verdictによる公開APIの結合試験まで。実際のClaude/Codex CLI、スケジューラ、LINE送信、証券発注は実装しない。今回の22追加ケースで見つかった問題は [再レビュー](PHASE2_OPS_REVIEW.md) とREADME末尾を参照。opsの修正はClaude担当。

## 1. 接続点と保存先

現在の `aitrader.packet_cli.generate(home, strategy, as_of, ...)` は **Proposal属性のdictのリスト** を返す。`as_of` はdate、`expires_at`はaware datetime。`packets` CLIのJSONではISO8601文字列になる。`aitrader.packet.build_proposals` は主系のProposal dataclassを返す。opsのProposalとは別クラスだが属性契約は一致する。

順序3では主系に型検証アダプタ、日次実行器、審査応答の正規化、ローカルoutboxを設ける案。市場DBを読むgenerateと、台帳の更新を担当する実行器を分ける。各AIは同一の固定パケットを独立に読むだけで、台帳に書き込まない。

実行時ファイルはDropbox外の `${AI_TRADER_HOME}` に保存する。市場データDBと `ledger.sqlite` は分離。案として `runs/<営業日>/<run_id>/` に入力manifest・パケット・Verdict・結果を、`orchestration.sqlite` に処理状態・outboxを保存する。模擬試験はテスト専用の一時ディレクトリを使い、実台帳を開かない。

manifestには営業日、データ基準日、取得完了時刻、データ版、政策版、起動モード、台帳seq、評価資産・高値・日次損益の基準を保存。どの版で判断したかを後から追えることが目的。

## 2. 日次実行順（JST）

| 時刻・順番 | 入力 | 実行内容 | 出力 | 失敗時の挙動 |
|---|---|---|---|---|
| 06:30 取得 | 実行営業日、営業日カレンダー、前営業日までに公表された市場情報 | 取得→重複・欠損・公表時刻検証→市場DB更新。口座の初期スナップショットと前日照合状態も確認 | 検証済みデータ版、manifest、取得完了状態 | 欠損を0や古い終値で補わない。DATA_INCOMPLETEまたはSYSTEM_ERRORとして候補経路停止、障害通知案を作る |
| 06:50 候補 | 検証済みデータ、確認済み単元・決算・規制情報、予算、最新台帳 | 前営業日をas_ofにrun_signals→generate。未確認が残る場合NEW停止。未確定CSVも照合状態へ含める | 固定したProposal群、候補除外理由、snapshot_id、packet_hash | 次営業日が本日の営業日でない、価格が古い、単元不明なら停止。初期残高未登録も停止。正常な候補0件だけNO_SIGNAL |
| 07:00 審査開始 | 固定パケットと同じmarket_context、審査方針版 | 模擬claude/codex Verdictを別々に用意し、応答を検証。将来はここへ実CLIアダプタを接続 | 候補ごとのREVIEW_PENDING/APPROVED/NOT_APPROVED/INVALIDと受信記録 | 片側欠損・形式不正・hash不一致を承認にしない。前日や旧runの応答を流用しない |
| 07:00〜締切前 ゲート | 検証済み二承認、最新台帳view、Limits、資産・損益、日次件数、STOP・未確認 | 単一書込主体で候補ごとに順次、ゲート再判定→create_notice→APPROVED→outbox準備 | GateResult、予約済み通知、重複送信防止キー付き通知案 | 不許可は候補結果に保存し予約を作らない。例外・予約拒否は送信対象にせずSYSTEM_ERROR。後続候補は最新余力を再読込 |
| 07:15 締切 | 未確定候補、outbox、実行状態 | 審査受付を閉じ、候補カード／サインなし／不承認／審査未完了／障害の当日結果を確定 | この段階ではローカル通知案のみ。将来はこの時刻までに配信を完了 | 未完了を「サインなし」に変えない。遅延承認は監査に残すだけ。旧承認で追送しない |
| 08:59以後 | 送信済み通知のexpires_at | expires_atを過ぎたSENT通知をEXPIREDへ | 通知期限の失効イベント | 実注文が未確認なら予約を残す。失効を取消と推定しない |
| 16:30、翌06:50 | 注文報告・CSV・未確認一覧 | 16:30照合依頼→翌06:50に解決判定 | 照合状態と新規停止フラグ | 未解決ならNEW停止。EXIT・照合・障害通知は継続可能 |

07:15は「審査受付・配信締切」、08:59はProposalの注文有効期限であり別の時刻。設計書の配信締切を守るため、実配信時には07:15より前に送信所要時間の余裕を確保する。数値は送信器の契約時に決める。模擬モードの受付条件は `received_at < 07:15:00 JST` とする案で、ちょうど07:15は遅延扱い。共通ゲートの `now <= expires_at` は変更しない。境界を最終確定する際は契約と試験へ追記する。

## 3. Proposalの変換と審査の固定

1. generateのdictかCLI JSONかを明示する。JSONならas_ofをdate、expires_atをaware datetimeに復元し、JSTの本日08:59と一致することを検査する。単なる `tzinfo` の付与で時刻を推測しない。
2. 数量・単元はboolを除く正整数、価格は有限正数、現物・寄付指値・銘柄・版・IDを検証する。CLI既定単元100は研究用であり本番の確認済み単元の代用にしない。
3. `events.next_earnings_date` はdate/ISO日付/明示None/UNKNOWNを区別して正規化する。未提供をNoneに変えない。events、参照資料、データ公表時刻をmanifestに保存する。
4. `ops.models.Proposal(**validated_fields)` に変換し、`compute_packet_hash` を再計算して元のhashと一致必須とする。出力のhashを書き換えて不一致を隠さない。
5. 同じ `render_packet(p, market_context)` を両者に渡す。judge・received_at・model・cli_version・run_idは呼び出し側が付与する。相手AIの結論を入力へ含めない。
6. 応答スキーマにない数量や価格の変更指示、余分なjudge、複数の判定、古いrun、遅延、欠損はINVALID。confidenceは採否に使わない。

events自体は現packet_hashの対象外だが、審査中は候補と一緒に固定する。新しいイベント情報が届いた場合はmanifest・snapshot_idを更新して新パケットとして再審査する。UNKNOWNを後から安全な値に差し替えて旧承認を再利用しない。

現時点のgenerateはBUY生成まで。SELLは保有数量と売り指値の契約待ちで明示エラーになるため、本設計でSELL生成済みとは扱わない。EXIT経路はopsへ手作りの合成SELLを渡した試験でのみ確認済み。

## 4. evaluate→create_noticeの順序と二重予約防止

**現在のcreate_noticeはCREATED時点で予約する。従って先にcreate_noticeしてからそのままviewをゲートに渡すと、自分の予約を二重に評価してしまう。** 審査待ちProposalは台帳外のrun状態へ保存する。

順序は「単一書込ロック取得→台帳最新状態・日次枠・STOP再読込→候補の重複確認→evaluate→許可ならcreate_notice→APPROVED→outbox準備」。一件ごとに予約を確定してから次の候補へ進む。evaluateとcreate_noticeを独立した並列ワーカーで実行しない。Webhook、CSV、出金も同じ書込キューへ直列化する。

`Ledger.view(daily_pnl=..., day_start_equity=...)` に実際の評価値を渡す。0やNoneの既定値で損失判定を通さない。equityは同じ基準時刻の評価資産、peak_equityは入出金を考慮した履歴上の基準高値。計算方法・入出金調整は契約化が必要。現在のゲートの集中度は `qty×avg_price` の簿価なので、時価対応には新しいビュー項目が必要。avg_priceを書き換えて代用しない。

日次2件の上限は「送信済み＋配信枠確保済み（未送信／送信結果不明）」で先取りする案。ゲート引数 `new_sent_today` の現契約は送信済み件数なので、当面実行器が未送信分を含む枠を別途厳格に検査し、実際の送信済み件数を渡す。送信待ちを全て0件扱いで通さない。日付はJST執行営業日、重複再実行は件数を増やさない。

SELLはgateが保有数しか見ないため、現台帳のcreate_noticeによる株数予約確認まで成功して初めて配信可能とする。ただし初期open_ordersが予約に入らない問題（R07）はClaudeの修正が先。BUYとSELLの同銘柄併存で、未約定のBUYを売却可能株数に加えない。

## 5. 再実行・停止・配信失敗

outboxキー案は `(execution_day, proposal_id, packet_hash, notification_kind)`。同じID・同じhashの再実行は既存状態を再開し、create_noticeを繰り返さない。IDが同じでhashが異なる場合は停止して候補版の衝突を記録し、既存予約を上書きしない。現生成器の連番は候補集合で変わり得るため、当日manifestを再利用する必要がある。

台帳とoutboxは別DBなので現在のAPIだけで跨る原子性は保証できない。台帳のCREATED/APPROVEDとoutboxの不足を起動時に照合し、送信器を解放する前に復旧する設計が必要。次のクラッシュ点を順序3の受入条件とする。

| 停止位置 | 復旧方針 |
|---|---|
| create_notice前 | 保存済みProposalを再検査。まだ予約・送信なし |
| CREATED後、APPROVED前 | 同じパケットと有効な二承認を再確認して継続。確認不能なら未送信であることを確定してREJECTEDへ。期限超過でも実注文の存在は別途判定 |
| APPROVED後、outbox前 | 同じキーでoutboxを再構築。台帳を二重作成しない |
| 配信要求後、応答不明 | 配信キーで照合・冪等再試行。送信成功と断定してSENTにしない一方、未送信とも断定して予約を解放しない。NEW停止で照合待ち |
| 配信成功確認後 | 送信時刻・配信IDを保存してSENT。途中停止しても配信結果から復旧し、二度送らない |

現フェーズの模擬試験ではAPPROVEDまでで止める。模擬承認に基づいて本物のSENTを記録しない。ネットワークを使わない通知案は `mode=mock, delivery=NOT_SENT` と明記する。

07:15時点で片側が未完了なら通知案は「本日の審査は締切までに完了しませんでした。未完了の候補は見送ります。」。一部だけ完了している場合は、全ゲート通過済み候補と未完了件数を別々に示し、未完了候補の注文条件は出さない。締切後の応答を過去runへ採用しない。監視は日次状態が確定しない場合もSYSTEM_ERRORの通知案を作り、沈黙を成功扱いしない。

## 6. 今回実行した模擬結合試験と次の受入条件

`test_ops_rereview.py::test_r20_mock_packet_to_gate_then_reserve` は **通過**。主系 `build_proposals` の実出力に模擬claude/codex APPROVEを渡し、gate許可→実Ledger.create_notice→APPROVED→予約額一致→二件目の余力不足による拒否まで確認した。実CLIも実送信も使っていない。packet_cliの市場DB取得からの日次全体、JSON変換アダプタ、締切制御・outboxは本設計段階であり未実装・未試験。

共通フェーズ2試験の実行場所は `ops/`、コマンドは `python -m pytest ../common/tests/phase2 -q`。現在は再レビューの反例16件が失敗するのが記録された状態であり、模擬結合1件の通過を全体の完成と扱わない。

順序3を完成とする前に必要な試験：generateのdict／CLI JSONからの型とhash一致、as_ofの一営業日ずれ拒否、片側タイムアウト、07:15境界と遅延応答、同じrun再起動、複数候補の予約・日次枠の競合、CREATED/APPROVED/outbox各停止点からの復旧、STOP中のEXITと障害通知、翌朝未確認、損失情報欠測、初期注文取り込み、修正後の台帳replay。実CLI接続はこれらを模擬Verdictで通してからの別工程とする。
