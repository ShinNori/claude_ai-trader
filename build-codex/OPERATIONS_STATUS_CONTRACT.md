# 統合運用ステータス表示契約案

2026-09-11。対象は完全合成mock homeの読取専用表示である。実通信、送信、注文、STOP/RESUME操作、復旧、見張りを行わない。本書は表示契約の提案であり、現在のシグナルや再開許可を生成する仕様ではない。

## 1. 三つの状態を分離する

統合結果は次の三領域を別々に返す。一つの総合boolへ畳み込まない。

| 領域 | 情報源 | 意味 | 許可しない読替え |
|---|---|---|---|
| `current_stop` | managed version 2の `inspect_managed_stop(home, now)` | 指定した現在観測時刻におけるSTOP情報源の読取結果 | CLEARを売買・再開許可にしない。UNKNOWNをCLEAR扱いしない |
| `sealed_history` | `inspect_daily_rehearsal(home)` | receiptと全ファイルhashに一致した保存済み日次履歴 | VERIFIED_HISTORYを現在のSTOP、送達、注文、現在シグナルにしない |
| `progress_observation` | `daily_progress.json` の安全な限定読取 | 最後に保存された処理段階の手掛かり | RUNNING/FAILED/COMPLETEDだけでcommit、整合、完了receipt、再開可否を推定しない |

`overall` を設ける場合は、`ATTENTION_REQUIRED`、`HISTORY_AVAILABLE`、`NO_VERIFIED_HISTORY` のような表示優先度に限定する。運用可否を表す `READY`、`SAFE_TO_TRADE`、`CAN_RESUME` は返さない。

## 2. 状態の優先順位

1. `current_stop.known=false` または取得例外は、`status=UNKNOWN`、`effective_stop=true` として最上位の注意表示にする。
2. `current_stop.status=STOPPED` は停止理由を表示する。停止中でも封印履歴の検証結果は独立して表示できる。
3. `sealed_history.status=VERIFIED_HISTORY` は「保存履歴を検証済み」とだけ表示する。`saved_summary` は履歴欄だけで使用する。
4. receipt不一致・欠損は `NEEDS_RECONCILIATION` とし、未検証summaryを表示しない。
5. progressはreceiptの代替にしない。封印履歴がない場合も、限定フィールドで「最終観測段階」を表示するだけにする。

現在STOPと封印履歴が食い違って見えても統合して修正しない。例として、封印時CLEAR・現在STOPPED、封印時STOPPED・現在CLEARはいずれも正当な時系列になり得る。両方の観測時刻と出所を並べる。

## 3. UNKNOWN時に表示しない情報

`current_stop` がUNKNOWN、または履歴・progressが未照合の場合、次を表示・返却しない。

- proposalの銘柄コード、方向、数量、指値、期限、証券リンク、注文文面。
- 「本日の候補」「現在有効」「送信可能」「発注可能」「再開可能」という判断。
- 未検証ファイルから読んだ候補数、承認数、予約額、利用可能額、台帳seq、通知状態。
- 生の例外文、DB値、絶対path、設定値、recipient、秘密情報、任意の保存済みmessage。

表示可能なのは、固定した安全な状態語、理由コード、`known/effective_stop`、読取専用・自動再開不可のフラグ、および検証済みの観測時刻である。封印履歴が独立にVERIFIEDなら、UNKNOWN中でもexecution_day、scenario、最終status、生成・承認・拒否の集計値を「過去の保存結果」と明記して表示してよい。ただし個別注文条件は本統合画面では常に扱わず、既存のmock reportとも現在状態を混同しない。

## 4. progressの限定読取

progress読取はDBを開かず、ファイルを作らず、次の許容キーだけを型検査して返す。

- `status`: `RUNNING | FAILED | COMPLETED`。
- `current_stage` / `failed_stage`: 実装で定義した固定段階名だけ。
- `checkpoint_seq`: boolを除く0以上の整数。
- `result_status`: COMPLETED時だけの既知終端状態。
- `error_type`: FAILED時だけの例外クラス名。例外messageは返さない。
- `partial_state_may_exist` / `auto_resume`: FAILED時はそれぞれtrue / falseだけを許容。

重複JSONキー、壊れたJSON、リンク・junction、読取拒否、観測中変更、未知キーや不正型は `UNVERIFIED_PROGRESS` にする。`daily_progress.json` のCOMPLETEDとreceiptのVERIFIEDは別々に示し、不一致なら注意状態を下げない。

## 5. 時刻表示

呼出側の `now` はtimezone付きdatetimeを必須とし、JSTへ正規化する。各時刻は意味を添えて別フィールドにする。

- `current_stop.observed_at`: 今回STOPを観測した時刻。
- `current_stop.control_event_at`: 検証できた最新制御イベント時刻。欠損を観測時刻で補わない。
- `sealed_history.execution_day`: 過去の執行営業日。
- progressには耐久保存された時刻がなければ新しい時刻を捏造しない。
- `generated_at`を設ける場合は画面/結果を構成した時刻であり、状態発生時刻とは表記しない。

未来・naive・破損時刻を文字列のまま表示しない。UNKNOWN理由へ落とす。

## 6. pathと出力

入力homeは一度解決し、Dropbox外の通常ディレクトリに限定する。状態DB、receipt、progress、STOP fileの既存安全検査を迂回しない。返却値にhome、state_path、stop_files等の絶対pathを含めず、固定ラベルだけを使う。

公開APIはdictを返すだけとし、元homeにstatus JSON、HTML、cache、lock、SQLite、tmpを保存しない。HTML rendererも文字列を返すだけにする。保存が必要な将来CLIは明示された別output先を使い、source home配下を拒否し、既存ファイルを暗黙上書きしない。標準出力は可能だが、未検証summaryや絶対pathを含めない。

検査前後で元homeのファイル集合・size・hashが変わらないことを受入条件にする。読取のためにNotifier、Ledger、書込可能SQLite接続を構築しない。

## 7. API受入候補

- managed v2のCLEAR / STOPPED / UNKNOWNと、VERIFIED_HISTORY / NEEDS_RECONCILIATIONの全組合せ。
- legacy v1はcurrent STOPを「管理対象外」と明示し、home/STOPだけからmanaged CLEARを捏造しない。
- progress欠損、RUNNING、FAILED、COMPLETED、重複キー、未知段階、bool seq、観測中差替え。
- current STOPのWAL/SHM/journal、DB欠損、未来clock、STOP file linkで、個別注文条件とsaved_summary由来の現在表現が出ないこと。
- receipt改変時に `saved_summary` が出ないこと。VERIFIED履歴がある場合も `current_signal=false`、`auto_resume=false` を維持すること。
- home内外のリンク・junction、外部pathを埋めたJSON、読取拒否でもhome外を読まないこと。
- APIとHTML呼出しの前後で元homeがbyte単位で不変であり、出力ファイルが増えないこと。
- HTMLはエスケープ、外部resourceなしのCSP、送信・操作フォームなし、UNKNOWNを最も目立つ位置へ表示すること。

既存 `render_mock_report` は診断COMPLETE時に個別の模擬注文条件を表示する別画面である。統合運用ステータスはより狭い概要表示とし、現在STOPの確認不能時にmock reportへのリンクや注文条件を自動表示しない。

## 8. 残操作・再試行の判断表

この表は表示後に人が調査対象を選ぶための案であり、復旧操作の採用や実行許可ではない。表示APIは状態を変えず、下記の「読取専用確認」までに留める。

| 観測状態 | 自動で行わないこと | 読取専用で確認可能な公開API・成果物 | その後の扱い |
|---|---|---|---|
| 現在STOPがUNKNOWN | CLEARへの補完、NEW予約、STOP/RESUME、DB作成・修復、STOP file削除 | `build_operations_status(home, now)` / `render_operations_view(home, now)` の限定結果。managed v2では内部的に `inspect_managed_stop` の理由コードを利用 | `effective_stop=true` のまま要確認。再観測は診断であり再開ではない |
| receipt・ファイル集合・hash・seqが不一致 | 未検証summaryの表示、receipt再封印、変更後DBの再承認、予約解放、完了扱い | `inspect_daily_rehearsal(home)` の `NEEDS_RECONCILIATION` と、統合表示の履歴状態 | 保存物を保持して手動照合。既存receiptを上書きしない |
| progressがFAILED | 失敗段階からの自動再実行、partial stateの削除・再利用、予約解除、完了receipt生成 | 統合表示の検証済み `failed_stage`、`checkpoint_seq`、`error_type`、`partial_state_may_exist=true`、`auto_resume=false` | 残骸があり得る前提で要確認。error messageや未検証業務値は表示しない |
| progressがRUNNING | 死活判定、強制終了、再開、同じhomeでの別run開始、完了推定 | 統合表示の検証済み `current_stage` と `checkpoint_seq` | プロセス中断後にも残り得る観測記録として扱う。見張り・自動再試行へ接続しない |
| 既存home、初期化中flag、または版・policy不一致 | 再初期化、自動移行、marker/policy補完、既存ファイル削除、fresh-home処理への再利用 | 統合表示、managed marker/receiptの既存厳格検査。履歴が有効なら `inspect_daily_rehearsal` | 現物を保存して要確認。managed v2の作成契約を既存homeへ遡及適用しない |
| managed clockより前の時刻、未来制御、clock破損・逆行 | 時刻補正、clock巻戻し、STOP/RESUME適用、古い時刻での処理再試行 | `inspect_managed_stop` を利用する現在STOP欄のUNKNOWN理由。receiptではmarker/clockの構造・時刻整合のみ | より新しいtimezone付き観測時刻で診断し直せるが、それ自体は制御・再開許可ではない |
| Notifier DBにWAL/SHM/journalが存在 | immutable本体だけを読んだCLEAR判定、sidecar削除・merge、Notifier構築、DB修復、NEW処理 | `inspect_managed_stop` / `inspect_stop_status` のUNKNOWN診断を統合表示で限定提示 | DB一式を変更せず要確認。sidecar消失後の再観測も診断に限定する |

`CLEAR`、`VERIFIED_HISTORY`、`COMPLETED`のいずれも単独では残操作の許可にならない。再試行する場合も、新しいhome、新しい固定入力、現在時刻、STOP、台帳、期限、審査を通常入口で改めて検証する必要がある。完了済みrunは保存履歴を返すだけで、同runの結果を書き換えない。

## 9. 未採用の復旧契約

次は [RUNNER_RECOVERY_CONTRACT_PROPOSAL.md](RUNNER_RECOVERY_CONTRACT_PROPOSAL.md) の提案段階にあり、この画面から採用済み・実行可能と表示しない。

- 台帳と同一トランザクションで保存するoperation receiptと、所有runの証明。
- receiptを照合して自分の既存予約だけを除外する `view_for_existing_notice` 相当の公開view。
- 締切後の独立監査記録 `HISTORY_RECONCILED` と、その予約保持・翌日照合・終端化。
- INTENT片側保存からの自動再開、自動承認、予約の自動解放。

[STOP_INTEGRATION_PLAN.md](STOP_INTEGRATION_PLAN.md) で未完成とされる全書込元の共通ロック、外部STOP file操作との原子的直列化、実制御の認証も未採用のままである。現在のmanaged限定実装、読取診断、模擬配信直前確認から、これらの保証や実通信・見張りの許可を導かない。

## 読取診断の観測境界を補強（2026-09-11 採用）

run診断の対象marker・2つのDB・5成果物について、読取前のリンク/reparse検査と前後のファイルhash照合を追加する。rollback journal、-wal/-shmおよび対応するドット形式sidecarがあれば停止中の安定した診断とは扱わない。観測中のDB・成果物・固定policy変更は候補を出さずNEEDS_RECONCILIATIONとする。既存DB欠損MISSING、成果物欠損CONFLICTの分類を維持し、再作成しない。

前後一致は外部書込と原子的なsnapshotを取得した証明ではない。snapshot_consistentは従来のFalseを維持し、確認できた前後一致だけをobservation_unchangedで表す。現在の売買承認や自動復旧許可を追加しない。

## JSON保存の一時ファイル契約（2026-09-11 採用）

共通write_jsonは、固定名の一時ファイルを再利用せず、同じ親フォルダへ排他的な固有名で作成する。JSON/UTF-8化を先に検証し、保存先・親のリンク/reparseを拒否する。書込後に置換し、通常の置換失敗では旧targetを保持する。失敗した固有tmpは残して自動復旧・削除しない。古い固定tmpも消さず再利用しない。

通知計画・キュー・模擬配信/照合レポートもこの保存方式へ接続する。従来これらの保存値には共通マスクを適用していなかったため、共通保存へ移行する際も値の同一性を維持する。共通writerの既定マスクは変更しない。DBコミットとファイル置換の原子性を追加したわけではなく、片側失敗は既存の照合・同入力再試行契約で扱う。

外部からの同時パス差替え全体を排他する保証、fsyncによる電源断の保証、研究用CSV/HTMLの直接保存、SQLiteの保存契約は今回の対象外である。

## 深いJSONの読取分類（2026-09-11 採用）

読取専用APIは、JSONの入れ子がパーサの処理可能な深さを超えた場合に、`RecursionError`を呼出元へ漏らさず保守的な読取分類を返す。これは保存情報の内容が不正と断定する分類ではなく、現在の読取境界で安全に観測できないことを表す。

- `build_operations_status` では、`daily_progress.json`の深さ超過をprogressの`UNKNOWN / PROGRESS_UNREADABLE`とする。preflight中のmanaged markerは`MOCK_MARKER_INVALID`とし、後段の直接policy確認中に深いmanaged markerを検出した場合はSTOPの`UNKNOWN / MANAGED_STOP_UNKNOWN`とする。`inspect_managed_stop`内で検出したmarker/clockについては、次項の`MANAGED_POLICY_INVALID`を引き継ぐ。overallは通常のUNKNOWN合成規則に従う。
- `inspect_managed_stop` では、managed markerまたは`managed-stop-clock.json`の深さ超過を`UNKNOWN / MANAGED_POLICY_INVALID`とし、`effective_stop=true`を維持する。
- `inspect_daily_rehearsal` では、receiptまたはprogressの深さ超過を`NEEDS_RECONCILIATION / UNREADABLE_OR_UNSAFE_HISTORY`、保存summaryを`NEEDS_RECONCILIATION / INVALID_SUMMARY`、managed markerまたはclockを`NEEDS_RECONCILIATION / UNSAFE_MANAGED_HISTORY`とする。これらの分類で`saved_summary`は返さない。

構造や値が通常のmarker契約に合わない場合の既存エラーと、パーサが深さのため観測できない場合は、内部の検出原因として区別する。一方、公開APIは未検証内容や例外本文を返さず、どちらも既存の固定理由コードで保守的に表示する。

今回は共有JSON parser、`managed_stop_policy`、`_clock`、sealのwriter契約を変更しない。深さ超過の情報を自動修復・再保存せず、予約解除、自動再開、receipt再封印にも接続しない。新しいfile size上限やJSON深さ上限を契約として追加したわけではなく、ファイル間の原子的snapshotを保証するものでもない。試験件数はREADMEの最終統合結果を参照する。
