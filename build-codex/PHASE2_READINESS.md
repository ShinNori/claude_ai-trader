# フェーズ2：現時点の実装範囲と実運用までの不足

**2026-09-12追記：** 人工原本のstrict_input_v1と、要求ページ/銘柄日付集合を確認するstrict_coverage_v1を独立オフライン検査として実装した。詳細は[専用入力](STRICT_INPUT.md)と[範囲検査](STRICT_COVERAGE.md)。以下の市場契約「未採用」はこの限定範囲を除く。実原本の真正性・営業日/鮮度・単元有効期間・永続化・実AI/通知は未完。[完成までの工程表](COMPLETION_ROADMAP.md)が全体の現在地を示す。

その後、[選択価格原本との結合](STRICT_PRICE_COVERAGE.md)と[人工V2ページ連鎖](V2_PAGE_CHAIN.md)も実装した。前者は選択価格行だけ、後者は記録された継続キーだけを検査する。実データ全体の同一時点性・原本真正性へ保証を広げない。[公式資料の再確認](JQUANTS_PUBLIC_DOC_UPDATE_20260912.md)でページングの限界と予定版切替を記録した。

[単元・イベント適用期間](STRICT_VALIDITY.md)も人工証拠の半開区間と実選択値hashの照合まで実装した。実際の適用期間の出所・当時の証拠入手時刻は未確認。新規ストアへの[保存・訂正案](STRICT_EVIDENCE_STORAGE_PLAN.md)は文書化までで、取込や既存home移行は未実装。

2026-09-11、Codex単独開発中のコードを読んだ確認記録。最新の全体試験件数・所要時間・既知の失敗分類は [README](README.md) の最新の実測節を参照する。本書は本番運用の承認書でも、Claudeの独立レビュー済み宣言でもない。

## 現在の要約（2026-09-11最終更新）

本節が現在の到達点である。後続の「最新の限定補強」以降には実装途中の時点で書いた履歴があり、「未接続」「次は実装」等が本節と矛盾する場合は歴史的記述として読む。

| 領域 | 現在実装済み | 現在も保証しないこと |
|---|---|---|
| 合成日次と管理STOP | 新規version 2 homeの明示選択、runner予約前とBUY INTENT直前のSTOP確認、明示STOP/RESUME、通知準備・queue・stub配信/照合、仮想日次6シナリオ | 実scheduler、既存home移行、全外部writerの直列化、実制御認証 |
| 保存履歴と状態表示 | receipt全file hash、runner診断、現在STOP・保存履歴・progressを分けるread-only JSON/HTML、UNKNOWN時の非要約 | 現在シグナル、owner証明、自動再開、稼働中DBの原子的snapshot |
| JSON/path境界 | marker/clock、settings、events/lotsの通常file・link/reparse・size・前後同一性、固有一時fileによる主要JSON保存 | OS上の外部並行操作との完全原子性、fsyncによる電源断耐久保証 |
| 市場source guard | syntheticとjquantsの混在拒否、transaction直前のmode再確認。JQuants clientは通信/JSON例外の秘密値抑止、payload/token/row/pagination基本型をfake sessionで検証 | 現在の外部API仕様への適合、業務rowの完全性、取得原本receipt |
| 市場入力表示 | 既存DuckDBを外部access/extension自動読込なしのread-onlyで検査し、source、5表件数・保存全行日付範囲、固定warningを返す。UNKNOWNは部分要約なし、`current_signal=false`・`ready_for_live=false` | 入力の正しさ、候補生成許可、調整基準、公表日推定履歴、実データ利用承認 |
| SELL/EXIT | 手作り合成Proposalの審査・予約・通知主系を境界試験済み | 市場・保有原本からSELL候補を生成する戦略と実入力契約 |
| 研究成果物の保存 | 4成果物のstage生成・失敗時backup復元、概要exportとHTML再生成の協調writer排他、残存lock/backupの読取診断 | 電源断耐久、readerへの全file一括原子性、残骸の自動採用・復旧 |
| 来歴の独立fixture試作 | 専用modeの新規homeでrun/row来歴・訂正・NO_OP・整合診断を検証。API/CLIは既存市場処理から隔離 | 実データB案の採用、外部response原本、実データ適格性、signal/packet/backtestへの接続 |

現在残る契約は次の順で扱う。

来歴試作の詳細と操作は[PROVENANCE_FIXTURE.md](PROVENANCE_FIXTURE.md)。`provenance_fixture` modeの記録が整合しても、以下の実データ契約を解決したとは扱わない。

1. **価格調整と公表日推定の保存契約。** 調整OHLCが部分欠損した場合のOHLC/volume/turnover基準を決める。`publication_estimated`をDB全体、行単位、取得run単位のどれとして保存するかを決め、既存行・移行・研究許容を定義する。現検査は警告するだけで補正しない。
2. **実データ原本と適格性。** 固定response原本、取得contract版・時刻・期間・件数・hash、公式calendar、銘柄別単元、eventの出所を保存し、欠損を`DATA_INCOMPLETE`として止める。JQuantsの現在仕様は外部確認と利用者判断なしに断定しない。
3. **runner INTENT片側保存の復旧。** 台帳と同一transactionのoperation receipt、owner、seq・内容hash、既存予約を二重評価しないviewが未採用である。診断COMPLETEや日次receiptを再開許可へ転用しない。
4. **実通信の明示許可と受入。** 実AI、実通知、実Webhook、発注、scheduler、見張りは未許可・未接続である。認証、秘密管理、重複防止、失敗・再試行、停止直列化の契約と利用者の明示許可を別工程で必要とする。

未採用の判断事項は、現在利用する外部データ契約版、推定公表日の研究利用可否、調整済み項目の部分欠損方針、銘柄別単元・event原本、取得期間・訂正の保持期間、INTENT receipt schemaと所有境界である。これらは既存AGENTS.mdや共通仕様から一意に決まらないため、commonを変更せず判断待ちとして維持する。

## 現在できること

`synthetic_pipeline.py`は共通合成市場を読み、実戦略と`packet_cli.generate`を通した候補を、明示した両審査の記録済み模擬応答・ゲート・専用台帳・通知キューへ接続する。イベントと単元は合成用設定であり、実データの取得結果ではない。生成物のhash、入力原本、台帳との照合を行う。

`daily_rehearsal.py`はこの接続を仮想06:30〜07:15の6シナリオに分けて実行する。価格欠損と営業日情報の欠落はDATA_INCOMPLETE、明示的な休日はNO_SESSIONとして後続を止める。審査欠落、締切、STOPでは予約を作らない。毎回未作成の専用homeが必要で、既存homeからの自動再開や時計駆動サービスではない。[日次受入と再開案](DAILY_REHEARSAL_PLAN.md)に契約を分けて記載した。

`mock_delivery.py`は登録済みrunのキーだけをstubで処理する。模擬成功、成否不明、STOP、締切、予約月、履歴時刻を扱う。成功記録がキューに残り、台帳だけが未反映の場合には、明示的なrun限定照合APIがある。別DBにまたがる処理全体が一括確定する設計ではない。

`runner_diagnostics.py`と`mock_report.py`は停止したDBの観測・過去の模擬準備内容の表示を行う。診断不確実なら注文条件を表示しない。画面のNOT_SENTは準備時点の履歴で、現在の送信状態を保証しない。

## 実運用と区別する項目

| 項目 | コードから確認した現状 | 実運用までの不足 |
|---|---|---|
| 売買候補の生成 | `strategies/margin_bucket_long.py`と`packet.py`の生成経路はBUY候補を作る。下流にはSELL/EXITの処理がある | 下流のSELL試験が通っても、自動的な売却候補生成まで接続された意味ではない |
| 実AI審査 | `review_runner.py`は記録済み応答を`transport='stub'`で扱い、`ops/judges.py`は実CLI経路を拒否する | CLIの認証・隔離・起動・失敗・再開を含む実接続の受入未実施。模擬の両承認は実AIの承認ではない |
| 実通知 | `ops/notify.py`は`transport='line'`を拒否し、模擬配信はstub固定 | LINE送信・実際の応答・実サービスの重複制御を検証していない |
| 発注 | 現行の接続先は模擬通知キューまで。HTMLにも実注文禁止を表示する | 注文は行っていない。ユーザーが欲しい売買シグナルと、自動発注を混同しない |
| 日次の継続実行 | 主系のrunner/demoは明示呼出しのAPI・CLI。日次06:30取得から07:15締切までを動かす時計駆動の主系サービスは今回の接続に含まれない | PC停止・休業日・途中再開を含む日次運用の受入が必要。開発引き渡し用見張りは日次投資処理とは別物 |
| 市場データ | 生成器は基準日と価格日を照合し、runnerも候補当日の価格を検査する。合成pipelineは固定日を使う | 実市場のデータ到着・訂正・欠損・公式営業日・実イベント・実単元を継続取得する運用の実測ではない |
| runnerの片側保存 | `runner.py`は候補INTENTと台帳通知の片側保存が残ると、照合を要求して停止する | 診断結果を読んで自動再開する機能はない。通知キューSENTの台帳再反映APIと、runnerのINTENT復旧は別の問題 |
| 専用home | 通知準備・登録・模擬配信・照合は共通runnerロックを使う。一方、外部からのLedger/Notifier直接操作はこのロックに参加しない | 利用時は専用homeを共有API以外から直接書き換えない。OSが他プログラムの直接書込みを禁止してくれる設計ではない |
| 診断の限界 | WAL/SHMがあれば診断を保留。停止DBだけをimmutable読取し、`snapshot_consistent=False`、自動再開不可を返す | 稼働中DBの整合した同時点診断は未対応。WALを消して診断を通す運用はしない |
| レポート | HTMLは診断済みの準備履歴を表示する。模擬送信状況は別記録で、画面は配信記録を検証しない | 最新時刻の実シグナルや送達証明として使えない |

## 当時の実装優先順（履歴・一部実装済み）

以下は日次managed接続前に置いた提案である。日次6シナリオ、管理STOPの予約前確認、明示STOP/RESUME、通知経路のmanaged確認は後続節のとおり実装済みであり、現在の未実装一覧として使用しない。

1. **runnerの片側保存を解決する契約と受入試験。** 残ったINTENTと台帳のどちらを根拠にするか、同じ候補・所有run・予約をどう保全するかを先に固定する。診断のCOMPLETEを再開許可に転用せず、修復操作を明示的に分ける。
2. **日次の模擬実行を一連で検証する。** 取得失敗、休日、審査欠落、締切後、PC停止・再起動を仮想時計で再現し、重複処理なく停止できることを確認する。実日次起動や開発見張りの再開は、この試験と別に扱う。
3. **売却候補の生成・入力経路を明示する。** 現在のBUY生成と下流のSELL対応の差を埋め、既存保有・売却可能数・部分約定との関係を通しで検証する。未確定の売却戦略を推測して追加しない。
4. **実データの取得契約を検証する。** 基準日価格だけでなく、営業日・イベント・単元・入力の出所を固定し、欠損時には生成や承認を止める。戦略の利益が出るようにパラメータを変更する工程ではない。
5. **実AI・実通知は別の承認済み接続工程にする。** 現在は実通信禁止であり、接続可能にするだけの変更を先行しない。両審査と通知の実受入が終わるまで、模擬成功を運用可能と報告しない。

この優先順は不足の整理であり、新しい戦略、実通信、発注、見張り再開を許可するものではない。現在の実測範囲と残課題は、[日次結合設計](PHASE2_INTEGRATION.md)、[通知結合](NOTIFY_INTEGRATION_PLAN.md)、[runner復旧案](RUNNER_RECOVERY_PLAN.md)、[模擬デモ手順](MOCK_DEMO.md)と併せて確認する。

## 最新の限定補強（2026-09-11）

上記の未完成事項を維持したうえで、次の変更を実装した。試験件数・所要時間は重複転記せず、[READMEの最新実測](README.md)を参照する。

| API・箇所 | 確認した実装と限界 |
|---|---|
| `run_synthetic_pipeline(home, seed=42)` | 共通合成データ2016-01-04〜2026-08-31を使用。基準日8月28日→執行日8月31日07:10の固定デモで、現生成器のBUYだけを無改変で接続する。日次schedulerでも実市場取得サービスでもない |
| `deliver_prepared_mock` | Notifierへ動的`stop_check`を渡し、選択後に作られたhome/STOPも通知器の送信前確認で検知する。戻り値がbool以外、または確認例外なら送信へ進まない。固定settingsを変更しない |
| `reconcile_prepared_mock` | 全原本・receipt・queueの照合後、当該runの耐久SENTだけを台帳へ反映する。enqueue/flushはしない。修復時刻は元の成功時刻の復元ではない |
| 登録・模擬配信・照合の時刻 | `notification-plans.sqlite`のrun別`notification_clock`へ事前検証完了後の操作時刻を耐久記録する。レポート保存失敗・喪失後も過去時刻へ戻さず、同時刻以降の再試行を許す。STOP・空選択の観測も保持し、既存レポートの時刻検査も維持する |
| `diagnose_mock_run` | SNAPSHOTを公開`validate_snapshot_for_replay`で既存台帳ルールに従って純粋検証する。入力をコピーし、DB接続なしで現金・保有・初期注文等を検査する。不正開始残高、重複通知作成、不正遷移はCONFLICTへ分類する |
| `render_mock_report` | HTML文字列を返すだけで保存・通信しない。診断COMPLETEと成果物の再照合を条件に、APPROVED候補のcode/方向/数量/指値だけを表示する。模擬・実注文禁止を常時表示し、未承認条件を出さない |

この時点の動的STOP追加は模擬配信時だけの限定補強だった。後続で新規version 2 homeのrunner予約前確認と明示STOP/RESUMEを実装した。すべての直接書込元の直列化、既存home移行、実制御認証は現在も保証しない。[STOP統合案](STOP_INTEGRATION_PLAN.md)に残る契約を参照する。

耐久clockは時計を合わせ直す機能ではない。未来へ進めた観測時刻を過去へ戻す救済・自動削除は行わない。複数DBの原子性、外部直接書込との競合、稼働中の診断、INTENTからの書込復旧も引き続き未解決である。公開snapshot検証は初期イベントの整合を調べるもので、診断COMPLETEを現在の売買承認に変えるものではない。

## 日次停止と売却経路の追加確認

日次リハーサルの`daily_progress.json`は最後に入った処理段階を示す。通常例外では型名だけをFAILED記録へ残し、台帳や予約を保持する。強制終了ではRUNNINGが残り得る。台帳とは別の原子的置換ファイルであり、電源断の保存保証や再開許可ではない。

明示したSELLの合成Proposalを、実`review_runner`→通知準備→キュー登録→明示stub配信へ通す独立7件を追加した。現金予約0・売却株予約、保有超過の拒否、STOP下でのEXIT、両審査必須、同銘柄BUY/SELLの区別、通知成功が約定ではないことを確認した。売却候補を市場から選ぶ戦略は追加していない。これらは既存の下流SELL契約の結合検証である。

完了履歴にはファイル集合・サイズ・hashの索引を付け、`inspect_daily_rehearsal`でDB接続や書込なしに再照合できる。合致しても`VERIFIED_HISTORY`であり、`current_signal=false`・自動再開不可を維持する。追加ファイル、通知状態の進行、欠損、sidecar、不正進捗等は保留し要約を返さない。この読取専用参照の採用は、INTENTからの復旧契約を採用した意味ではない。詳細と限界は[日次リハーサル設計](DAILY_REHEARSAL_PLAN.md)を参照する。

停止状態の第一段階として、`aitrader_ops.stop_status.inspect_stop_status`と読取CLIを追加した。正常に閉じた通知DBの停止履歴・kvと明示停止ファイルを確認し、CLEAR/STOPPED/UNKNOWNと理由を返す。欠損DBを作成せず、不正履歴や観測中の変更をUNKNOWNとする。これは後続のmanaged接続前の履歴である。runner予約前の永続STOP確認、共通制御ストア初期化、明示制御は後続節の新規version 2 homeに実装済みであり、全直接書込元の直列化は現在も保証しない。[停止統合案](STOP_INTEGRATION_PLAN.md)と[操作手順](MOCK_DEMO.md)を参照する。

## 管理STOPの予約前接続（2026-09-11）

新規home限定の明示API initialize_managed_mock / apply_managed_control / inspect_managed_stopを採用した。runnerはversion 2 homeに限り、持続STOP・固定停止ファイル・UNKNOWNを審査時とBUY予約直前へ接続する。旧version 1の動作は維持。制御操作とrunnerは同じロックを使う。

[採用契約と限界](STOP_INTEGRATION_PLAN.md)を参照。この節の時点では日次CLIの明示managed選択とmanaged配信経路の共通検査は未接続だったが、後続節で実装済みである。直接書込元全体の直列化、実Webhook認証、INTENT復旧、既存home移行は現在も未採用である。完了結果の再読は履歴であり、解除による承認書換えや現在シグナルの保証ではない。

## 日次managed選択と配信接続（2026-09-11）

日次CLIの --managed-stop により、新規homeだけで初期化から審査・通知キュー登録まで管理STOPを使用できる。stop_before_reviewは永続STOPを使って新規予約0を再現する。模擬配信と照合にも共通managed clock、閉じたDBの事前確認、試行直前の停止ファイル確認を接続した。旧version 1と既定CLIは維持する。

日次履歴は管理marker/clockも含めて照合するが、STOP DBの意味論や現在シグナルを証明しない。初期化が日次進捗開始前に失敗した場合、managed初期化flagが残り、daily_progressは作られない。運用日次scheduler、実データ接続、実審査・通知、制御UIと認証、全書込元の直列化、既存home移行、INTENT復旧は未完成。[採用契約](STOP_INTEGRATION_PLAN.md)と[操作手順](MOCK_DEMO.md)を参照。

## 2026-09-11 模擬運用準備の追加到達点

現在STOP・保存履歴・未照合進捗を分ける読取専用JSON/HTML表示、明示の模擬STOP/RESUME CLIを追加した。表示から制御や予約解除は行わない。CLIも既存の照合条件を緩めず、自動再試行しない。操作は[状態表示契約](OPERATIONS_STATUS_CONTRACT.md)と[明示制御CLI](MANAGED_CONTROL_CLI.md)を参照する。

run診断はmanagedの固定policyを照合し、marker・DB・成果物の観測前後変化やsidecarを保留する。管理marker/clockは通常ファイル・サイズ・同一性を確認する。共通JSON保存は排他的な固有一時ファイルを使い、通知計画・キュー・模擬配信/照合にも接続する。DBと成果物の一括コミットや電源断耐久性を追加保証したものではない。

残工程は、公開資料の確認後も決まっていない実データ原本・調整・公表日・単元等の契約、模擬運用手順の追加検証、既存の未採用復旧案の整理である。新戦略・実通信・発注・見張り再開・INTENT復旧は未採用。HTMLの目視確認はブラウザーURLポリシーで未実施であり、自動内容検査だけを完了扱いにする。

状態表示と明示制御CLI、設定JSONの安全読取まで実装済み。固定fixtureによる応答形式・ページング・取込失敗の反証も実施済みだが、これは現行外部APIへの適合や実データ適格性を確認したものではない。[入力準備状況](DATA_INPUT_READINESS.md)の未採用項目を優先し、単元既定や公表日推定を変更する場合は研究用の既存契約との違いを明示して、推測だけで変更しない。

## 日次・runner入口のraw path検査（2026-09-11）

日次/合成pipeline/mock demo/記録済み審査/runnerは、resolveによりリンク情報を失う前にraw absolute pathと既存親を確認する。runnerのmarker・主要DB・SQLite sidecar・runs directoryも通常型を確認する。日次receiptのseal/inspect直接APIは入口とsnapshot時に全親を確認する。これによりoperations_status経由だけでなく、直接履歴検査にも同じ親path境界を適用する。

正常sidecarの存在自体をwriterで拒否するものではない。読取診断のWAL/SHM保留、非協調外部変更の限界、既存home自動移行禁止は維持する。候補所有・原本の補強は[runner契約](RUNNER_RECOVERY_PLAN.md)、実測は[README](README.md)を参照。

## 通知永続行と読取状態表示の補強（2026-09-11）

通知4入口のraw親/DB/sidecar/runs検査、runner出力保存先の予約前検査と接続失敗後片付けを追加した。通知の保存hash・recipient・合法state・履歴/日時を事前確認し、既知破損でclockや先頭通知だけを進めない。SENT照合も対象noticeの既知欠損を更新前に検出する。一致する固定planを再保存せず、欠損時の再生成は維持する。

状態表示の深いprogress/receipt/summary/managed JSONは読取境界で確認不能へ分類する。共有parserと書込sealの契約は変更せず、通常のmarker不正とrunner診断の分類は維持する。これは模擬経路の安全停止・入力検証の補強であり、実データ契約、実審査・LINE、売却戦略、旧home移行、INTENT復旧、全writer排他・一括atomicityの採用ではない。

最新の実測は[README](README.md)、続きの制約は[Codex継続プロンプト](Codex継続プロンプト.md)を参照。今回の利用上限は90%に更新済みで、リセットや実通信・見張りを起動する許可を追加していない。

## 95%継続枠の原本照合（2026-09-11）

候補通知の期限は台帳Proposalと同一時点、再試行キーは保存行と全attemptの一致を要求する。日付別締切はUTC等の表記をJSTへ変換してから計算する。保存plans.bodyは再生成preparedと照合し、保存hashだけの一致で改変本文を再公開しない。新規30件を含む全体2110 passed / 9 skipped、310.03秒で確認した。

直接Notifier APIの時刻逆行は再現済みだが、直接APIに単調時刻を要求する契約は未採用。主系の登録・配信・照合の既存時刻ガードは維持する。[通知統合契約](NOTIFY_INTEGRATION_PLAN.md)に境界と保留事項を記録した。今回の利用上限は最新ユーザー指示の95%が優先し、実通信や見張り再開の許可は追加しない。

## 検証の更新（2026-09-11 17:39 JST）

未知strategyの接続前拒否、tuple内の他AI判定混入拒否、候補生成・取込の二次ROLLBACK失敗時の元例外保持を含む全体2138 passed / 9 skipped、278.58秒。実データ準備状況の古い未実装記述も現在化した。次の限定採用案・互換影響・受入条件は[判断表](NEXT_CONTRACT_DECISIONS.md)を参照。direct queue時刻下限の採否は質問中で、未実装である。

## 直接通知APIの時刻下限確定（2026-09-11 17:53 JST）

後続の続行指示を受け、直接通知APIの選択対象に限る時刻下限を実装した。Q04、hash/schema、空・指定外・終端filterは維持。新規29件を含む全体2167 passed / 9 skipped、623.24秒で確認し、上記の採否待ちは解消した。市場入力3契約は未採用のままで、[人工データ実験](DATA_CONTRACT_EXPERIMENTS.md)を判断材料として追加した。

## 通知・台帳の二次障害補強（2026-09-11 18:53 JST）

Notifierの4transactionとLedger._commitで、割込み時にも取消を試み、取消の二次障害で元例外を隠さないよう補強した。送信成否不明、検証拒否監査、COMMIT後のメモリ更新規則は維持。既存来歴CLIの読取結合試験も追加し、新規22件を含む全体2189 passed / 9 skipped、602.49秒で確認した。取消不能・COMMIT成否不明の復旧機能を追加したものではない。利用上限は最新ユーザー指示の98%へ更新済み。
