# 主系と通知器の停止状態を統一する案

2026-09-11、Codex設計レビューからの更新記録。初版の提案と、末尾に記載した限定実装を区別する。共通仕様変更・実通信・見張り再開は行っていない。参照: [PHASE2_INTEGRATION.md](PHASE2_INTEGRATION.md)、[NOTIFY_INTEGRATION_PLAN.md](NOTIFY_INTEGRATION_PLAN.md)、[順序4仕様案v0.2](共通仕様_フェーズ2_順序4_修正提案_v0.2.md) §8、runner.py、mock_delivery.py、opsのgate.py/notify.py。

## 現在の範囲

runnerは各候補のゲート評価時にhome/STOPの存在だけを渡す。Notifierの永続停止は予約前の主系へまだ接続していない。Notifier.is_stoppedは永続kv停止とsettings.stop_fileの存在をORする。mock_deliveryはhome/STOPとNotifier.is_stoppedのORでNEWを選択から除外し、通知器自身も送信直前に停止を再検査する。

| 対象 | 実装済みのSTOP動作 |
|---|---|
| NEWの新規予約 | runnerのhome/STOPで拒否。Notifierだけの永続STOPは未反映 |
| NEWの準備済み模擬配信 | home/STOPまたはNotifier停止で試行しない |
| EXIT | STOP単独では止めない。株数・二承認・期限等は独立して必要 |
| RECONCILE/RISK | STOP単独では止めない。月予算や日次期限は維持 |
| 既存予約 | STOPで取消・再作成・解放しない |
| UNKNOWN | STOPでもUNKNOWN、同retry_key・予約月・予算を保持 |
| 明示的なSENT台帳修復 | 新たな送信ではない。STOP解除や新規承認の代用にしない |

STOPPEDは送信操作の観測で、PENDING行の取消状態とは限らない。mock_deliveryのentriesにある耐久stateとresultsのSTOPPEDを分ける。07:15以上のNEW/EXITは主系の厳しい締切で除外し、STOP解除後も期限切れを送らない。

## 提案する共通停止の取得方法

専用mock homeの共通停止サービスを設け、実効停止を「home/STOP存在 OR 永続停止 OR 既存settings.stop_file存在」とする。どの情報源も他を打ち消さない。戻り値には実効bool、停止理由の集合、取得時刻、永続制御イベント時刻を含め、未知・読取失敗をfalseへ補完しない。将来settings.stop_fileをhome/STOPへ限定するかは別契約とし、既存設定を勝手に上書きしない。

通知DB未作成を正常な永続停止なしとするには、専用home初期化時の未作成状態を確認できる必要がある。以前存在したDBの欠損や読取拒否と区別できない場合は新規を停止して診断する案。停止を読むだけでNotifierを構築するとDBやWALが作られるため、読み取り専用の公開制御API、または初期化時から共通制御ストアを持つ方式の採否が必要である。内部kvへの直接SQLを主系の永続契約にしない。

runnerへは生の停止boolを固定入力として渡すだけでなく、ロック内で再取得する共通サービスを接続する。保存済みrequest hashに一時的なSTOP状態を混ぜ、同runの再実行を入力変更扱いにしないよう、固定候補入力と操作時の制御観測を別の監査記録にする案。

## 検査時点と直列化

共通runner-lock取得後、候補ごとに台帳・日次枠と停止状態を再取得してevaluateする。許可後create_noticeへ進む直前にも停止を検査する案だが、ファイル存在確認とDB書込にはOS全体の原子性がない。厳密な停止順序はすべてのSTOP/RESUME操作も同じロック下の書込窓口へ集約して保証する。外部手動ファイル作成との瞬間競合を完全保証したと表記しない。

準備・enqueueは送信ではないが、過去の承認結果を現在の許可に変換しない。模擬配信は固定計画照合後、キー選択直前と通知器の試行直前に実効停止を検査する。主系だけがhome/STOPを見て通知器が別stop_fileを見る現構成では、選択後に追加されたhome/STOPを通知器が見落とす可能性があるため、共通サービスを通知器の送信前検査にも接続する必要がある。

直接Notifier/Ledger呼出はrunner-lockを取得しないことを既存試験で確認している。模擬専用homeへの混用は禁止し、Webhook・CSV・出金・制御操作を同じ窓口へ直列化するまで全書込元の競合安全を実装済みとは扱わない。

## 明示解除・時刻

STOPとRESUMEは明示操作だけとし、期限経過・エラー解消・成功試験・Claude制限解除を自動RESUME理由にしない。永続停止のRESUMEには、呼出側が確認した照合時刻が最新STOPの記録時刻以上かつ現在時刻以下であることが必要。イベント本文の自己申告照合時刻は使わない。未来イベント拒否・遅着イベントによる巻戻し禁止・同時刻の現契約を維持する。

Webhook経路では未来timestamp拒否が既にある。一方、直接stop/resume APIはevent_atの未来値を入口で一律拒否する契約ではないため、主系窓口では未来・逆時刻を先に検査する必要がある。現APIの読取から書込までを別制御書込元と競合させない。新しい独自の時計ずれ許容量は設けない。

永続停止を解除してもSTOPファイルが残れば実効停止は継続する。ファイル削除だけでも永続停止は解除されない。共通解除窓口でファイルを消す権限・確認をどこまで持たせるかは未決であり、自動削除は実装しない。解除しても未確認約定、照合保留、損失・DD停止、日次上限、二承認、期限は独立して有効。既に保存されたREJECTED結果を同runで承認へ書き換える方法も別契約である。

## 実装前の反証

停止情報源の全組合せでORを確認し、片方だけの解除でNEWが進まないことを試験する。NEWの予約前・予約後・選択後・試行直前にSTOPを挿入し、予約額・試行数・UNKNOWN予算が増減しないことを確認する。EXIT/状態通知は停止単独で遮断されず、締切・予算・株数・二承認は維持されることも試験する。

時刻は未来制御、遅着STOP/RESUME、同時刻、照合なし、STOP以前の照合、未来照合、解除後07:15直前/同時/直後を含める。DB欠損・読取拒否・不正型・複数プロセス・途中例外でも停止をfalse扱いせず、制御履歴とロックを維持する。UNKNOWNの再試行キーと月予約、SENT修復の無再送、別run不変、実通信禁止を確認する。

第一段階は停止状態の読取と理由表示、第二段階でrunner予約前へ接続、第三段階で模擬配信の直前検査を共通化する案。明示制御窓口・認証・ファイル解除・実Webhookは別段階とし、この設計から実通信や見張り起動の許可を導かない。

## 2026-09-11 限定修正の実装

送信選択後にhome/STOPが作られる窓に対し、Notifierのoptional stop_checkを追加した。既存の永続停止・settings.stop_fileを変更せず、追加callbackの真偽をORする。主系模擬配信はhome/STOPの存在を動的に返し、flushがNEWを試行する直前にも確認する。不正callback型・非bool戻り値・確認例外でNEW送信へ進まない。EXITと状態通知はSTOP単独では止めない。

独立通知器13件0.73秒、主系競合PENDING/UNKNOWN2件10.33秒が通過。選択後にSTOPを作っても試行を増やさず、予約・予算・retry_keyを保持する。修正前redの実測はなく、実装後の独立検証である。

これは予約前の永続STOP統合やOS全体での原子性を実現したものではない。最後の存在検査と実処理の間の外部操作を完全に直列化するには、前述の共通書込窓口が必要。既存停止ファイルの自動削除・見張り起動・実通信は行わない。

## 2026-09-11 次工程：読取専用の停止診断

第一段階の公開読取API `aitrader_ops.stop_status.inspect_stop_status(state_path, now=..., stop_files=...)`を実装対象として採用する。停止の書込・解除・主系ゲートへの接続はこの段階に含めない。Notifierを生成せず、閉じた既存通知DBと明示した停止ファイルの状態を調べる。

| 診断 | 解釈 |
|---|---|
| CLEAR | 正常に読めた停止履歴・現在値に永続停止がなく、指定停止ファイルも存在しない |
| STOPPED | 正常な永続停止または指定停止ファイルの存在を確認した |
| UNKNOWN | DB欠損・読取失敗・不正値・履歴矛盾・未来制御・sidecar・リンク・観測中変更等で状態を確定できない |

UNKNOWNは`known=false`、`effective_stop=true`を返す。これは新規処理へ流用した場合に停止を見落とさないための値であり、現在のrunnerを変更した意味ではない。CLEARも売買承認ではない。DBが初めから未作成なのか消失したのかは判定できないので、いずれもUNKNOWNとする。正常な新規Notifier DBの空の停止履歴はCLEARとして扱える。

STOP_LATE/RESUME_LATEは既存通知器と同じく監査のみで現在状態を変えない。STOP履歴なしの正常なRESUMEも受け入れる。RESUME後も残る最後のSTOP時刻を消失扱いしない。停止以外のkv（予算警告等）をこのAPIの停止契約へ取り込まない。

DBは読取専用・immutable接続とし、WAL/SHM/journalが存在する状態を読取対象にしない。前後のファイル照合で観測中の変更を検知するが、外部書込と原子的な一時点観測は保証しない。DBを作成・移行・修復せず、停止ファイルを削除しない。主系へ接続する前には、初期化時から制御ストアを持つ契約と書込の直列化を別途解決する。最新の試験結果はREADME末尾へ記録する。

## 2026-09-11 採用：新規模擬home限定の管理STOP

`aitrader.managed_stop.initialize_managed_mock`を明示選択した新規homeだけに、version 2 / managed-v1契約を採用する。既存version 1は従来の停止ファイル判定を維持し、自動移行しない。一般の日次リハーサルも従来homeのままである。

- 未作成homeを排他的に作成し、初期化中flag→台帳→閉じたstub通知DB→観測clock→固定policy markerの順で準備する。flagを最後に除去する。途中失敗はflagと現物を残し、既存homeの再初期化・修復を拒否する。
- 固定policyは初期化時刻、settingsのhash、home/STOPと任意のhome内絶対STOPパスを含む。settings本文はmarkerへ保存しない。policyはrunner入力hashに含め、可変な停止観測は候補のstop_observationsへ記録する。
- managed runnerと明示STOP/RESUMEは同じrunner-lock.sqliteのBEGIN IMMEDIATEで直列化する。ロック待ち後に固定policyを再照合する。候補審査時と、許可されたBUYのINTENT保存直前に閉じた通知DBを読取確認する。UNKNOWNはSTOP_NEWとSTOP_STATE_UNKNOWNで新規予約を拒否する。
- STOPだけではSELLを禁止しない。保有株数・両審査・締切・他の既存条件は維持する。完了済runの再読は保存済み履歴を返し、解除後の新しい承認へ書き換えない。
- apply_managed_controlは明示STOP/RESUMEのみ。未来イベントと観測clock以前を拒否し、状態UNKNOWNなら通知DBを作らない。操作の前にclockを原子的置換し、失敗しても時刻を巻き戻さない。照合不足でRESUMEが成立しない場合も観測時刻は進む。これは電源断時の保存保証ではない。
- RESUMEは既存Notifierの照合契約を使う。STOPファイルの削除、通知flush、予約解除、実通信は行わない。永続停止が解除されてもファイルが残れば停止を継続する。

全書込元の直列化は未完成。直接Notifier/Ledger、外部ファイル操作、Webhook、CSV等はこのロックを使わないため、専用homeへの混用を禁止する。外部ファイル作成と予約のOS全体での原子性、異なるDB間の原子性、INTENT自動復旧、実制御の認証は保証しない。managed経路の配信直前確認共通化と日次CLIへの明示接続は次工程とする。

## 2026-09-11 採用：日次managed選択と模擬配信の共通停止確認

日次APIのmanaged_stop=True（CLI --managed-stop）を新たな明示選択として採用する。既定は従来方式で、既存homeを移行しない。managed初期化へ未作成homeの排他的取得を委譲し、同一settingsを初期化・通知計画・キュー登録へ渡す。stop_before_reviewはmanaged選択時だけ永続STOPを明示記録する。

通知計画は厳格に検証したversion 2と固定settings hashを受け付ける。enqueue/delivery/reconcileはrunner-lock内で、Notifierを開く前に閉じたDBとmanaged clockを検査する。UNKNOWNなら操作を拒否し、DBを作らない。正常に停止中ならキュー登録自体やEXIT・状態通知は既存契約に従う。

Notifierを開いた後にimmutable診断を繰り返す方式は採用しない。自分自身のWAL/SHMを不正状態と誤認するためである。ロック保持中の永続STOPはNotifier自身の検査を使い、固定policy/clock/停止ファイルは読取失敗を停止扱いにするcallbackで毎回再確認する。NEW試行直前のcallbackも維持する。直接Notifier書込や外部ファイル操作をロックで完全排他する保証はない。

managed clockの床を明示制御に加えてenqueue/delivery/reconcileへ拡張する。副作用前にhome共通clockとrun別notification_clockを更新する。片方の更新後に失敗しても巻き戻さず、同時刻以降での既存再試行契約だけを維持する。二つのclockやDB間の原子性、電源断の保存保証はない。runnerの全履歴をこのclockで事後補正しない。

日次receiptの形式はversion 1の全ファイルhash索引を維持する。managed marker/clockも索引対象となり、構造・時刻整合を検査するが、STOP DBの現在状態を証明する意味を加えない。DB非接続のVERIFIED_HISTORYとcurrent_signal=falseを維持し、封印後の制御操作や配信で内容が変われば履歴照合は保留となる。封印済み日次homeを自動再開する契約は採用しない。

審査時の固定policyをrunner manifestにも保存し、初回通知準備で現在policyと一致させる。既存のmanaged成果物でこの記録が無いものは推測補完せず拒否する（旧version 1には追加条件を課さない）。初回prepare前の有効な別policyへの差替えも許可しない。

## 共通管理ファイルの安全読取（2026-09-11 採用）

管理marker/clockの読取を共通の通常ファイル読取へ統合する。resolve前にhomeと親のリンク/reparseを拒否し、1 MiB以下・同一ファイル識別情報を確認して限定読取する。clockがリンクや破損ならUNKNOWN/拒否にし、制御・通知側でclockを上書きして補修しない。正常legacyとmanagedの内容契約は維持する。

これは読取境界の補強である。全writerの一時ファイル安全性、外部書込との原子性、fsyncによる電源断保証、既存home移行は新たに保証しない。
