# 仮想日次リハーサルの受入条件と再開契約案

2026-09-11。対象は共通合成市場、実 `packet_cli.generate`、記録済みstub審査、専用mock台帳、未送信通知キューを、仮想時計で一日分つなぐ試験である。06:30 / 06:50 / 07:00 / 07:15は入力時刻であり、実scheduler、実市場取得、実AI、LINE、注文、見張りを起動しない。本書は受入・再開契約の提案であり、共通仕様や製品APIの変更、runner片側保存の自動復旧を採用するものではない。

## 現在の境界

| 区分 | 内容 |
|---|---|
| 実装済みAPIで確認できること | 合成市場のロード、実生成器による候補生成、固定Proposalと記録済みstub応答の検証、07:00未満の審査開始拒否、07:15以上の未完了扱い、ゲートと台帳予約、通知案の準備とキュー登録、`delivery=NOT_SENT`、動的STOPによる模擬配信直前停止、停止DBの読取診断 |
| リハーサルの前提として固定すること | seed、営業日カレンダー、as_of、execution_day、合成イベント・単元、初期台帳snapshot、評価額、limits、judge応答と受信時刻、仮想clock、専用の未作成home。各シナリオは外部通信を拒否する設定で実行する |
| 未実装・未決 | 06:30取得サービス、時計駆動の日次scheduler、実休日カレンダーの継続取得、実AI・実通知、SELL候補生成、複数DBの原子確定、Notifier永続STOPの予約前統合、runnerのINTENT/台帳片側保存からの書込復旧、既存予約の所有証明と復旧専用view |

データ欠損は候補が正常に0件である `NO_SIGNAL` と区別する。必須入力の欠損、鮮度不一致、読取不能は `DATA_INCOMPLETE`（内部例外なら `SYSTEM_ERROR`）で候補・審査・予約へ進まず、欠損理由を残す。現APIにこの日次状態語を返す統一入口がない場合、試験harnessの結果として記録し、`run_reviewed_mock` の空候補へ変換しない。

## 実装済みの進捗観測

`run_daily_rehearsal` はscenario・seed・Dropbox外・未作成homeを検証後、`mkdir(exist_ok=False)` でfresh-homeを確保する。確保後は `daily_progress.json` を原子的置換で更新する。各処理段階へ入る前に `status=RUNNING`、`current_stage`、単調増加する `checkpoint_seq` を保存するため、通常終了しなかったrunについて最後に開始した段階を観測できる。

正常系、および想定内の `DATA_INCOMPLETE` / `NO_SESSION` は、先に `daily_rehearsal.json` を保存してから、進捗を `status=COMPLETED`、`current_stage=finalize`、`result_status=<当日結果>` にする。この `COMPLETED` はリハーサルAPI呼出しの終了を表し、売買承認、送信、日次運用の完成を表さない。

通常のPython例外では、元例外をそのまま再送出しつつ、best-effortで `status=FAILED`、`failed_stage`、例外クラス名だけの `error_type`、`partial_state_may_exist=true`、`auto_resume=false` を保存する。例外メッセージや入力内容をエラー欄へ複製しない。進捗保存自体が失敗しても元例外を置換しない。

強制killなど例外処理を実行できない中断では、最後の `RUNNING` が残り得る。これは停止位置の手掛かりであり、中断の原因、直前操作のcommit有無、台帳・journal・通知キューの整合を証明しない。`daily_progress.json` は台帳等とは別ファイルで、同一トランザクションではないため、`RUNNING` / `FAILED` / `COMPLETED` のいずれも単独で安全な再開や予約解放を許可しない。

進捗更新は一時ファイルへの書込み後に置換する方式だがfsyncを行わない。対象は通常例外とプロセス中断後の観測であり、OS書込キャッシュを含む電源断後の保存耐久性は保証しない。fresh-homeの自動削除・再利用も行わず、残った状態は照合対象として保持する。

## 6シナリオの受入条件

全シナリオ共通で、実生成器への入力と生成結果hashが実行中に変化せず、通知キューの全行が未送信であること、実通信呼出しが0回であること、別runと共通原本が不変であることを確認する。失敗後も予約、日次件数、台帳イベント、キュー行を重複させない。

| シナリオ | 固定イベント | 受入結果 |
|---|---|---|
| `normal` | 06:30入力検証完了、06:50生成、07:00開始、両judgeが07:15未満に有効応答 | 実生成候補だけを処理し、ゲート許可分だけ予約・キュー登録する。日次上限等の拒否は理由付きで残る。最終状態は当該結果に整合し、全通知は `NOT_SENT` |
| `data_missing` | 06:30に必須原本の一つを欠損または鮮度不一致に固定 | `DATA_INCOMPLETE`。生成、審査、予約、候補通知キュー登録は0。`NO_SIGNAL` を返さない。どの原本・日付・検査が欠けたかを記録 |
| `holiday` | execution_dayを固定カレンダー上の非営業日にする | 当日処理を正常休止として確定し、生成、審査、予約、キュー登録は0。翌営業日へ日付や応答を自動繰越ししない |
| `judge_missing` | 片側judge応答を供給しない | 欠けたjudgeを明示し `REVIEW_INCOMPLETE`。承認・予約・注文条件付き通知は0。欠損をREJECTやNO_SIGNALへ変換せず、旧runの応答を流用しない |
| `deadline` | 有効応答の受信を07:15ちょうど、または処理確定を07:15以上に固定 | 締切は排他的で、当該候補を承認・予約・候補通知へ進めない。遅延応答は監査対象に限り、時刻を巻き戻して再採用しない |
| `stop_before_review` | 生成後、stub審査開始前にSTOPを固定 | 現行経路どおり記録済みstub審査は実行し、その後のゲートでNEWを拒否する。予約・候補通知登録へ進まず停止理由を残す。既存予約を取消・解放せず、STOPを自動解除しない。EXIT/RECONCILEの扱いをNEWの成功扱いに混ぜない |

`normal` で候補が本当に0件なら `NO_SIGNAL` は正当だが、このケースは正常な全入力を使った生成結果が0件であることを原本とhashで示す。`data_missing` の代替結果としては認めない。

## 模擬フロー再実行の固定入力

再現用manifestは少なくとも次を一度だけ確定し、再実行では読み戻す。再生成して同じはずと推定しない。

- `scenario`、`seed`、戦略名・設定版、policy版、mode=`mock`。
- JSTの `as_of`、`execution_day`、仮想06:30/06:50/07:00/07:15、および各judgeの受信時刻。時刻はaware datetimeとし、後の実行で過去へ戻さない。
- 営業日カレンダー、価格・上場・指数・信用残のデータ版または内容hash、合成events/lots原本とhash。欠損シナリオでは「存在しないこと」も固定入力にする。
- 生成前の台帳識別子、初期snapshot原本、観測した `initial_ledger_seq`、評価資産・高値・日次損益、未確認状態、limits、STOP観測。
- 生成済みProposal配列の原本とhash、packet原本とhash、judgeごとの記録済み応答原本・hash・受信時刻。途中再開でgenerateやjudge応答を作り直さない。
- run_id、execution_day、全候補ID、通知計画の設定・contexts・include_status、および既存キューキー。run_idを替えて同じ日の続きを新規処理に見せない。

原本はrun専用ディレクトリへ保存し、manifest自身にも内容hashを持たせる。再開前にmanifest、原本、journal、台帳、通知計画DB、通知キューDBを照合する。WAL/SHMがある停止DBは現診断契約どおり再開不可とし、sidecarを削除して通さない。

## 完了記録と再開判断

リハーサルharnessの将来の完了記録は、外部配送receiptや台帳イベントのowner証明ではなく、既存成果物を束ねるローカルな試験結果とする。新しい台帳receipt契約を暗黙採用しない。現在の `daily_progress.json` はこの完了記録ではなく、成果物hash、台帳seq、キュー状態を束ねない。完了記録を次段階で追加する場合は、少なくとも次を含める。

- run_id、scenario、execution_day、manifest hash、開始・確定仮想時刻。
- 開始時と完了時に観測したledger seq、台帳識別子、候補ごとの最終状態、予約額、日次件数。
- notification plan/queueの対象キーと状態、`delivery=NOT_SENT`、`real_sent_by_this_call=false`、通信呼出し0件の検査結果。
- 終端状態と理由、生成数、承認数、拒否数、未完了数、キュー数、および成果物hash一覧。

現行の `run_daily_rehearsal` は未作成の専用homeを要求する一回実行の契約であり、同じrunの再呼出しや完了記録からの冪等参照は未実装である。今回の6シナリオ受入では再呼出しを必須にしない。次段階で追加する場合は、完了記録と全hash・最終ledger seq・キュー状態が一致するときだけ保存済み結果を返し、generate、審査、予約、enqueueを再実行しない契約を提案する。不一致、seq進行、原本欠損、別owner疑い、台帳片側保存、WAL/SHM、UNKNOWN/SENT、通知キューの想定外行があれば `NEEDS_RECONCILIATION` として予約を保持し、書込再開しない。

完了前の再開は、既存runnerが安全に扱う「INTENTかつ台帳通知なし」の範囲だけを候補にし、保存済み同一入力を使って現在条件を再評価する。INTENT/CREATED、INTENT/APPROVED、通知あり/INTENTなし等は現行どおり停止する。`diagnose_mock_run` の `COMPLETE` も再開許可へ読み替えない。

## Astra判断用の選択肢

1. **推奨：まず6シナリオをfresh-homeで一回完結するリハーサルとして受け入れる。** 完了済みrunの再参照や途中再開を今回の必須条件にしない。現APIで検証可能な範囲に収まり、自動復旧やowner証明を先取りしない。
2. **次段階：完了済みrunの冪等参照と、INTENT/通知なしだけの限定再開を追加する。** 固定原本・initial/final seq・現在条件の再照合を必須とする。停止位置を増やす試験価値はあるが、日次harnessとrunnerの責任境界を明示する必要がある。
3. **保留：CREATED/APPROVEDからの書込復旧。** 候補単位の台帳所有証拠、既存予約を二重計数しない公開view、STOP情報源と書込直列化が未決である。`RUNNER_RECOVERY_CONTRACT_PROPOSAL.md` の新receipt案を別判断で採用し受入試験を通すまで、今回の完了条件に含めない。

推奨順は1、必要なら2、契約決定後に3である。どの選択肢も実scheduler、実通信、見張り再開を許可しない。

## 2026-09-11採用：完了履歴の読取専用照合

選択肢2から、完了した履歴を読み直す部分だけを今回の実装対象として採用した。`run_daily_rehearsal`の同じhomeでの再実行は禁止したまま、別API `inspect_daily_rehearsal(home)`で保存済み結果を参照する。INTENTからの限定再開も、所有証明を伴う台帳復旧もこの採用には含めない。

日次の最終要約保存後、COMPLETED進捗保存前に、`daily_receipt.json`へ保存ファイルの相対パス・サイズ・SHA256と、期待する完了進捗の連番を一度だけ記録する。索引自身と進捗ファイルは内容一覧から除外するが、進捗のCOMPLETED、連番、模擬モード、結果状態は参照時に別途検査する。既存索引の上書きは拒否する。これはローカル履歴の変更検出であり、電子署名でも台帳イベントの所有証明でもない。

参照時はDBへ接続せず、保存ファイルの集合と内容を2回読み比べる。全体が一致した場合のみ`VERIFIED_HISTORY`と保存済み要約を返す。追加ファイル、欠落、台帳や通知状態の変更、進捗不一致、リンク、DBのsidecar、一時ファイル等は`NEEDS_RECONCILIATION`となり、要約を返さない。画像やHTMLを追加しただけでも対象homeの集合は変わるため、後から作る表示用ファイルは別フォルダへ保存する。

どちらの結果も`current_signal=false`、`auto_resume=false`、`repaired=false`である。照合が通ったことは現在の売買承認や未送信状態の継続保証ではない。外部プログラムと同時に書き換える状況の原子的な一時点観測、OSキャッシュを含む電源断耐久、索引を含む全ファイルを意図的に再作成する改ざんへの対策は対象外とする。検証件数と実装確定はREADMEの最新実測に記載する。
