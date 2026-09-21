# 実データ入力 readiness（オフライン監査）

**2026-09-12追記：** 専用人工入力の部分調整/推定公表日/原本参照等の検査は[STRICT_INPUT.md](STRICT_INPUT.md)、要求ページと銘柄日付集合の検査は[STRICT_COVERAGE.md](STRICT_COVERAGE.md)として限定実装済み。以下の未採用記述はこの範囲を除く。両者は独立しており、同一の市場原本に結び付いた適格性や取得許可を証明しない。残工程は[STRICT_INPUT_NEXT.md](STRICT_INPUT_NEXT.md)、全体進捗は[工程表](COMPLETION_ROADMAP.md)。

選択価格行から範囲行を内部生成する[結合検査](STRICT_PRICE_COVERAGE.md)と、記録されたV2応答の[ページ連鎖検査](V2_PAGE_CHAIN.md)も追加した。これは人工入力での結合であり、実API応答を取得して照合した記録ではない。公式の現在確認範囲と9月28日予定仕様は[公開資料更新](JQUANTS_PUBLIC_DOC_UPDATE_20260912.md)を参照する。

人工の単元・イベント証拠について、[適用期間と選択値の対応検査](STRICT_VALIDITY.md)を追加した。実際の適用期間・入手時刻を証明する機能ではない。保存と訂正は[次の設計案](STRICT_EVIDENCE_STORAGE_PLAN.md)に分けている。

2026-09-11。`jquants.py`、`packet_cli.py`、`packet.py` と既存共通仕様・試験を、現コードだけから確認した記録である。外部APIへ接続しておらず、J-Quantsの現在のendpoint、認証、項目名、提供範囲、利用条件との適合は確認していない。実データ取得や実売買への移行許可ではない。

## 現在の要約

| 状態 | 現在の到達点 |
|---|---|
| 実装済み | synthetic/jquants source混在拒否と取込transaction、JQuants clientの秘密値を隠す通信・JSON例外と基本response型検査、格納予定DOUBLE値の型・有限性検査、events/lotsの安全な限定JSON読取、phase-1 DB保存先guard、候補・packet入力およびbacktestの単一DB読取transaction、as_of当日価格・明示次営業日を使うProposal生成 |
| 読取表示のみ実装済み | 既存DuckDBをread-only・外部access/extension自動読込なしで検査し、限定source、5表件数・保存全行日付範囲、固定warningを返す。UNKNOWNは集計非表示、`current_signal=false`、`ready_for_live=false` |
| 専用試作のみ | `provenance_fixture`専用homeでcanonical run-rowと履歴を検査するオフライン試作。legacy取込・signal・backtestから隔離され、実データ採用やV2対応を意味しない |
| 未完了 | 現在の個人向け外部APIとの実疎通・取込適合、業務rowの完全性、実responseの固定原本、銘柄別単元・event原本、価格調整部分欠損、公表日推定の行/run履歴、期待期間・訂正を含む適格性判定 |

残る判断の優先順は、(1) 調整OHLC/volume/turnoverと公表日推定の保存単位、(2) response原本・contract版・取得期間/件数/hash・calendar・単元・eventのmanifest、(3) その固定原本を使うオフライン適格性受入、(4) 外部通信の明示許可と実サービス適合確認である。現在のinspection warningを補正や取得許可へ変換しない。

未採用事項は、部分調整rowを拒否・非調整一式・係数補正のどれにするか、推定公表日を研究で許容するか、`publication_estimated`をDB全体・行・取得runのどこへ持つか、単元とeventの正本、取得期間と訂正版の保持方法である。既存共通仕様とAGENTS.mdだけでは一意に決まらないため、commonを変更せず判断待ちとする。

## 1. 現在の入力経路

```text
JQuantsClient（想定legacy v1）
  → fetch
  → market.duckdb（listed / prices_daily / margin_weekly / index_daily / calendar）
  → run_signals(home, strategy, as_of)
  → packet_cli.generate
  → build_proposals
  → immutable Proposal + packet_hash
```

`JQuantsClient`はrefresh tokenが空なら通信前に拒否し、認証POSTとデータGETにそれぞれtimeoutを指定する。HTTP・通信・JSON decode失敗は本文・token・元例外を露出しない固定例外にする。認証payload、ID token、ページpayload、row、pagination keyの基本型を検査し、同じpagination keyの反復を拒否する。これはfake sessionで確認したコード上の契約であり、外部サービスとの疎通確認ではない。

`fetch(home, start, end)`はstart > endを拒否し、既存provenanceが`data_mode=synthetic`なら混在を拒否する。価格、信用残、上場情報、TOPIX、営業日カレンダーのいずれかが空なら、データ行のINSERT前に停止する。5表は一つのDuckDB transactionで`INSERT OR REPLACE`され、成功時に次をprovenanceへ保存する。

- `data_mode=jquants`
- `api_contract=legacy-v1-unverified`
- `publication_estimated=True|False`

価格は調整済みOHLCが4項目すべて存在するとき調整済み列を使い、それ以外は非調整列を使う。OHLCに欠損があれば取込を拒否する。信用残の公表日が取得行に無い場合は基準日+4暦日を推定してwarningを出す。上場日欠損は未知のまま保存し、候補除外を想定したwarningを出す。

## 2. Proposal生成時に確認していること

`packet_cli.generate`はDBから戦略候補を作った後、次営業日を明示calendarから取得する。calendar不足時は平日推測をせず拒否する。価格日は`as_of`以下の最大日を取得し、`price_day == as_of`でなければ古い終値で候補を生成しない。

候補に使うsnapshot hashには、候補、as_of、価格日、次営業日、候補別価格、単元、イベント、DB provenanceが入る。JSONはsort key、非ASCII維持、NaN禁止でSHA-256化する。Proposal側はさらに次を検査する。

- as_ofはdate、policy/snapshotは非空文字列。
- 銘柄コードは4〜5文字の数字または大文字英字、候補はBUYのみ。
- 同一strategy/version/codeの重複拒否。
- 終値はboolを除く有限正数、単元は正のint、予算は0以上のint。
- 指値は前日終値+0.5%を価格帯tickで切下げ、数量は単元整数倍。1単元未満はwarning付き除外。
- eventは決算日またはUNKNOWN、規制boolまたはUNKNOWNへ限定。欠損はUNKNOWNで、審査資料に「情報不足のため承認しない」と表示する。
- 執行期限は明示次営業日の08:59 JST。packet hashは共通仕様の固定項目だけをcanonical JSON化する。

`events_path`と`lots_path`は任意のローカルJSON objectである。イベント欠損はUNKNOWNになる一方、単元欠損は現在`100`へ既定化される。この既定はコード上の動作であり、実銘柄の売買単位を検証した結果ではない。

## 3. 未検証・未充足

### 外部取得契約

- base URL、endpoint、token方式、pagination名、response key、各field名は`legacy-v1-unverified`として固定されているだけで、現在の外部仕様への適合を確認していない。
- response JSONのobject、対象list、各row objectまでは検査する。一方、rowの日付が要求期間内か、全ページの業務条件が同じか、ページ数・総件数が妥当かは検証していない。pagination反復は拒否するが、異なるkeyが続く場合の新しい上限は設けていない。
- DOUBLE格納予定値はbool・非数・非有限をDataFrame構築前に拒否する。OHLC欠損も取込を拒否する。一方、銘柄コードと日付の厳格型、数値の正負・ゼロ、OHLCの大小関係、営業日区分の許容集合、nullable列の業務上の妥当性は取込前に網羅検証していない。
- endpoint間の期間・銘柄集合整合、欠落営業日、部分銘柄欠損、重複行、訂正データ、上場廃止・市場区分変更を原本単位で検証していない。「各配列が非空」は完全性の証明ではない。
- 公表日+4暦日は共通仕様上の研究用fallbackであり、実際の公表時刻を証明しない。`publication_estimated=True`のDBを実データ準備完了と扱う契約はない。

### 保存と原本性

- `fetch`は通信前に`init(home)`を行うため、認証・取得失敗でも空schema等の初期化副作用はあり得る。「部分取込なし」は5表のデータtransactionについての範囲である。
- `INSERT OR REPLACE`は同じhomeへの反復取込を許す。取得run ID、取得時刻、要求期間、ページ件数、response全体hash、原本file、各表件数・期間を保存しないため、DBだけから一回の取得原本を再構成・証明できない。
- 既存`data_mode=jquants`への別期間・訂正版の追加入力は許され、snapshot_idは候補生成時に参照した限定値のhashである。DB全体や外部responseのreceiptではない。
- events/lots JSONは1MiB以下の通常file、親・対象link/reparse、読取前後identity/size/mtime、重複key、非有限値、top-level objectを検査する。相対pathとUTF-8 BOMは維持する。schema version、内容hashの別保存、外部並行操作との完全な原子性は持たない。
- market DBのDropbox外限定とhome・既存親・DB・WAL・tmpの型/link/reparse検査は共通`db.connect(home)`で実装済みで、`fetch`、`generate`が内部で使う`run_signals`、backtestを含む既存write/read-write入口に適用される。検査後の非協調path差替えを完全に原子化する保証はなく、通常WALはwriter互換のため存在だけでは拒否しない。

### 候補生成の入力

- `lots_path`を省略した100株既定は、実データの銘柄別単元確認を代替しない。実データ経路で採用するなら、検証済み原本と欠損時拒否の契約が必要である。
- events欠損はProposalのUNKNOWNとして残るが、`generate`単体は候補を生成する。後段ゲートの拒否まで含めなければ「情報不足候補が承認されない」ことは完結しない。
- `run_signals`が使用した全行、戦略config、codeごとの価格・信用残の選択根拠はsnapshot hashへ直接保存されない。候補と限定入力が同じことは確認できても、元DBから候補に至る完全な系譜receiptではない。
- SELLはこのbuilderで明示拒否される。既存SELL/EXIT主系試験は手作りProposalの境界試験であり、実保有・売却価格からの実データ生成契約を意味しない。

## 4. 既存試験との対応

| 契約 | 既存試験 | 確認範囲 |
|---|---|---|
| 反復取込、上場日未知、公表日fallback | `test_jquants.py::test_ingestion_repeated_and_unknown_listing` | FixtureClientの小さな固定row。外部schemaは未確認 |
| synthetic/jquants混在拒否 | `test_jquants.py::test_real_and_synthetic_cannot_mix` | provenanceが既にsyntheticの場合 |
| 必須配列空でデータ行の部分INSERTなし | `test_jquants.py::test_empty_response_does_not_partially_insert` | TOPIX配列が空の一例。fresh homeでは通信前`init`によるschema作成を許す既存契約 |
| credential空、通信/JSON/response/pagination境界 | `test_jquants.py::test_missing_credentials`、`test_jquants_client_safety.py` | fake sessionで31件。秘密値非露出と正常2pageを確認し、実通信は未確認 |
| 取込全体rollbackと数値guard | `test_jquants_ingestion_atomicity.py`、`test_jquants_numeric_safety.py` | 5表・provenanceの故障時不変、DOUBLE予定値のbool・非数・非有限拒否。業務値域は対象外 |
| DB保存先とpath型 | DB API回帰、独立path境界試験 | Dropbox、親/home/DB/WAL/tmpのlink/reparse等を接続前拒否。非協調TOCTOUは対象外 |
| calendar明示、欠損価格/単元、重複、BUY限定、packet hash | `common/tests/phase2/test_packet.py`、`build-codex/tests/test_packet_local.py` | 手作り純粋入力。外部取得との結合ではない |
| as_of当日価格だけを生成に使用 | `test_synthetic_pipeline_edges.py`、`test_runner_price_freshness.py` | 合成mini DBでstale拒否と後段gateを確認 |
| mapping安全読取と候補計算前拒否 | `test_packet_mapping_safety.py` | 18件。重複key、非有限、サイズ、link/reparse、読取中変更を検査。単元100・UNKNOWN event互換を維持 |
| 候補・packet入力の同一snapshot | `test_packet_snapshot.py` | 4件。公開`run_signals`互換、同一connection/transaction、外部訂正の非混在、例外rollback |
| CLI packet JSONとUNKNOWN event | `test_packet_local.py::test_packets_cli` | CLI単体の`run_signals`差替え。実戦略・実取得のend-to-endではない |

## 5. 次に必要なオフライン受入材料

外部通信を行う前に、利用者が別途取得・保存した非機密の固定response fixtureと、その取得日・対象期間・契約版を用意する。現行adapterへ通し、全endpointのschema、pagination、欠損・重複・訂正、型・有限性、期間境界、transaction rollback、provenanceを反証する。この段階でも実サービス適合とは呼ばず、fixtureが表す契約版への適合と記録する。

続いて、銘柄別単元とeventの検証済み原本、営業日カレンダー、as_of当日価格、戦略が参照した行のmanifestを固定し、同じ入力から同じsnapshot_id・Proposal ID・packet hashが再生成されることを確認する。データ欠損は候補なしの市場判断へ変換せず、`DATA_INCOMPLETE`または入力拒否として残す。これらの契約採否までは、現在のJ-Quants adapterを実データ準備完了とは表示しない。

## 6. 現在採用済みの入力安全境界

2026-09-11、外部接続を行わないfake session試験に限定して、`JQuantsClient`の認証・ページ取得入口を補強した。これは通信例外と想定response形式を安全に拒否する変更であり、J-Quantsの現在仕様への適合、各業務rowの正しさ、実データ利用準備を証明しない。

- 認証POST・取得GETの`requests.RequestException`を固定した`RuntimeError`へ変換し、元例外をchainへ残さない。response JSON decode失敗も固定例外と`from None`で扱う。
- HTTP失敗時にresponse本文、refresh token、ID token、endpoint/keyの生入力を例外へ含めない。
- 認証payloadはobject、`idToken`は空白だけでない文字列を必須とする。bool、数値、list等をtokenとして使用しない。
- 取得payloadはobject、指定keyの値はlist、その各rowはobjectを必須とする。この段階ではrow内の業務field型・値域までは検査しない。
- `pagination_key`は欠損・`None`・空文字を終端とし、継続値は文字列だけを許可する。同じkeyの反復は拒否する。新しい長さ・ページ数上限やendpoint patternは今回追加していない。
- 正常な複数ページでは最初の検索条件を維持してpagination keyを次回要求へ加え、呼出側のparamsを変更しない。

独立`test_jquants_client_safety.py`は31件成功した。正常2ページ、params不変、認証payload/token、rows、paginationの型境界、HTTP・通信・JSON decode失敗における例外文字列と`traceback.format_exception`の秘密値非包含、例外chain抑止をfake sessionで確認した。既存`test_jquants.py`4件との合計35件もオフラインで成功した。実通信試験は行っていない。

候補生成用JSONについても、通常file・親path・1MiB・前後同一性を検査し、重複key、非有限値、不正UTF-8、非objectを拒否する。相対pathとUTF-8 BOM、未指定時の空辞書は維持する。events/lotsを候補計算前に読み、不正入力で`run_signals`へ進まない。単元100株の研究用既定とUNKNOWN eventは変更していない。独立安全試験18件と既存packet関連65件が成功した。

## 7. 反証から修正までの履歴（2026-09-11）

通信を行わず、Temp上で次の3点を再現した。製品・試験ファイルを変更せず、pytest件数には加算しない。

| 入力 | 実測 | 解釈 |
|---|---|---|
| 未作成home、最初のClient.rowsがRuntimeError | homeとmarket.duckdbが残る | 部分データ取込なしと、ファイル副作用なしは異なる |
| `_mapping`へ同じ銘柄キーを二度持つJSON | 当時はエラーにならず後の値を返した | その後の補強で全階層の重複keyを拒否済み |
| miniDBに当日価格1000円と次営業日、候補1件stub、単元指定なし | lot_size=100、予算250000円からqty=200 | 既定値が適用された事実であり、実銘柄の単元確認ではない |

最初と三番目は現在も残る観測事実である。二番目は修正前の履歴であり、現在の未検証事項ではない。共通仕様にある研究用fallbackを勝手に廃止せず、研究用互換経路と実データ適格性の採否を明示する必要がある。

## 8. fetch集約情報の独立レビュー

### 現コードから断定できる不整合

`publication_estimated`は今回取得した`margin`配列だけから`any(...)`で計算し、取込後にprovenanceの同じ一行を`True`または`False`で上書きする。一方、`margin_weekly`は期間を限定せず既存行を保持する。したがって、最初の取得で公表日を推定した行がDBに残ったまま、後の別期間取得がすべて明示公表日を持つと、既存推定行が残存しているのに`publication_estimated=False`となる。これは現DB全体の意味を示す名称・利用を前提にすると不整合であり、単一boolから推定行の有無を安全に判断できない。

価格行はAdjustment Open/High/Low/Closeが4項目すべて非`None`なら調整OHLCを採用し、一つでも欠ければ非調整OHLC一式へ戻る。ただしvolumeはこの判断と独立して`AdjustmentVolume`があれば採用する。このため、調整OHLCが部分欠損した行で「非調整OHLC＋調整出来高」という異なる基準が同じDB行に保存され得る。逆に調整OHLC一式がありAdjustmentVolumeだけ無い場合は「調整OHLC＋非調整出来高」となる。現コードはこれを識別するprovenanceを行単位で保存しない。

### 新しい契約判断が必要な事項

- `publication_estimated`をDB全体の集約値として毎回再計算するか、各`margin_weekly`行へ推定有無を保存するか、取得run単位のprovenanceにするか。schema・既存DB移行・過去行の扱いが必要なので、本レビューだけで方式を採用しない。
- 調整OHLCが部分的なrowを全体拒否するか、非調整一式へ揃えるか、調整係数から補正するか。volume・turnover・adj_factorを同じ基準へ揃える定義と外部項目契約が必要である。
- 推定行または調整基準不明行を研究用として許容する条件と、実データ適格性で拒否する条件。既存共通仕様の「調整済み列を優先」だけでは部分欠損時の選択を一意に定められない。

このレビューでは外部responseの現在仕様、正しい補正式、既存DBの移行方法を確認していない。製品コードは変更せず、上記二点を現在の実データ準備完了表示から除外する根拠として記録する。

## 9. 今回採用：市場入力の読取専用検査レポート

利用者が既存`market.duckdb`の入力状態を確認するため、`inspect_market_inputs(home, *, as_of)`を限定採用した。外部通信、データ取得、候補生成、シグナル判定、DB作成・移行・checkpoint・修復を行わず、SQLite系のrunner/STOP診断とも混ぜない。採用したのは読取観測と警告の範囲であり、実データ適格性の新基準ではない。

### 入口と読取境界

1. homeと`market.duckdb`のraw pathを解決前に検査し、Dropbox、symlink、junction、Windows reparse、通常file以外、欠損、読取拒否を安全なUNKNOWNへ落とす。欠損DBへconnectして新規作成しない。
2. `market.duckdb.wal`または`.tmp` sidecarがあれば、古い本体だけを正常結果として返さない。既存DBは接続開始時から`read_only=True`、`enable_external_access=false`、`autoinstall_known_extensions=false`、`autoload_known_extensions=false`で開き、書込可能接続、`init`、既存`connect(home)` helperの暗黙初期化を使わない。
3. DB本体のidentity、size、mtimeとhashを接続前後に確認する。これは同時変更の検知を補助するが、外部writerとの完全に原子的なsnapshotを保証しない。変化・sidecar出現・query失敗は`INPUT_STATE_UNKNOWN`とする。
4. 戻り値はdictだけとし、homeへJSON、HTML、cache、lock、tmpを保存しない。CLIを設ける場合も標準出力だけにし、生path、SQL例外、row本文、銘柄名・コード一覧、tokenを出さない。

### 返却する限定情報

```text
status: INSPECTED | UNKNOWN
source: synthetic | jquants_legacy_unverified | UNKNOWN
tables: 表ごとの count / min_date / max_date
warnings: 固定reason codeの集合
current_signal: false
ready_for_live: false
read_only: true
observation_atomic: false
```

対象表は`listed`、`prices_daily`、`margin_weekly`、`index_daily`、`calendar`に固定する。件数は0以上の整数、日付範囲は保存全行の検証済みDATEの最小・最大だけを`scope=SAVED_ALL_ROWS`付きで返す。`as_of`は厳格なdate必須で、将来行とcalendar coverageの警告に用いる。任意table名やSQLを引数から受けない。

sourceはprovenanceの`data_mode`を限定語へ写像する。`jquants`は`jquants_legacy_unverified`と表示し、`api_contract=legacy-v1-unverified`を「現在の外部仕様へ適合」と読替えない。provenance欠損、重複、不正型、未知値、schema不一致は推測補完せずUNKNOWNまたはwarningにする。provenanceの全値や未知keyは返さない。

### 警告候補

各警告は集計queryで確認できた事実、または保存契約上確認不能なことを区別した固定codeにする。

| code案 | 根拠 | 表示上の限界 |
|---|---|---|
| `REQUIRED_TABLE_EMPTY` | 必須5表のいずれかが0件 | データ欠損をNO_SIGNALへ変換しない |
| `DATE_RANGE_EMPTY_OR_INVALID` | 非空表でmin/max日付を得られない、またはmin > max | 期待期間との十分性は別入力なしでは判定しない |
| `REQUIRED_VALUE_MISSING` | prices OHLC、margin残高、index close、calendar区分等のNULL件数が1以上 | row本文や銘柄を表示しない |
| `NONFINITE_NUMERIC_PRESENT` | 保存数値列にNaN/±Infinityが1件以上 | 値を補正・削除しない |
| `NONPOSITIVE_PRICE_PRESENT` | prices OHLCまたはindex closeが0以下 | 正しい値を推測しない |
| `OHLC_RANGE_INVALID` | highがopen/low/close未満、またはlowがそれらを上回る行がある | OHLCを並べ替え・補正しない |
| `CALENDAR_VALUE_INVALID` | `is_business_day`がNULLまたはboolとして扱えない | 営業日を曜日で補わない |
| `LISTED_DATE_MISSING` | listed_dateがNULLの行がある | 上場日を推定しない |
| `PUBLICATION_DATE_MISSING` | margin publish_dateがNULLの行がある | 基準日+4日を検査時に書戻さない |
| `PUBLICATION_ESTIMATE_HISTORY_UNVERIFIED` | jquants DBではbatch単位boolから既存全行の推定有無を証明できない | provenanceがFalseでも「推定行なし」と表示しない。Trueなら推定利用を追加表示できる |
| `ADJUSTMENT_BASIS_UNVERIFIED` | 保存行に元responseの調整/非調整選択根拠がない | 調整OHLC部分欠損やvolume基準をDBから断定・補正しない |
| `PROVENANCE_INCOMPLETE` | 許可したdata_mode/api_contract等が欠損・矛盾 | sourceを実データへ推測しない |
| `FUTURE_MARKET_ROWS` | as_ofより後の価格・信用残・指数行がある | 保存全行rangeとas_of時点入力を混同しない |
| `CALENDAR_AS_OF_MISSING` / `AS_OF_PRICE_MISSING` / `CALENDAR_NEXT_SESSION_MISSING` | as_of当日のcalendar・価格、または次営業日のcoverageがない | 平日・古い価格で補わない |

集計前にmain schemaの6対象objectがすべて`BASE TABLE`であり、固定column・型・主キー定義に一致することを検査する。同名VIEWやschema違いではqueryを部分的に続けず、`SCHEMA_UNVERIFIED`で全体をUNKNOWNにする。期待する取得期間、銘柄集合、全営業日の連続性、endpoint間の完全性は現DBだけでは分からないので「欠損なし」とは表示しない。

### 受入候補

- DB欠損時にhome/DBを作らずUNKNOWN。corrupt DB、read拒否、link/reparse、WAL、観測中本体変更も同様。
- synthetic正常fixtureとjquants fixtureについて、5表のcount/date range、限定source、`current_signal=false`を返す。
- 空表、NULL、NaN/Infinity、0以下価格、不正calendar、上場日欠損を固定warningへ分類し、row値・pathを返さない。
- `publication_estimated=True`とFalseの両方で履歴限界を表示し、Falseを推定行不存在の証明にしない。
- 調整済み元列は保存後schemaに無いため、部分欠損を作ったと断定せず`ADJUSTMENT_BASIS_UNVERIFIED`だけを返す。
- API前後でDBとhomeのファイル集合・bytesが不変。外部HTTP client、`run_signals`、packet builder、Ledger、Notifierを呼ばない。

このレポートの`INSPECTED`は既存DBの限定集計を読めた意味だけである。UNKNOWN時は途中で得たsource、表集計、warningを返さず、`source=UNKNOWN`、空のtables/warningsと固定reasonに限定する。候補生成可能、データ完全、現在シグナルあり、審査・注文可能、外部仕様適合を意味しない。入力適格性を判定するには、期待期間、固定原本manifest、銘柄別単元・event、取得contract版を別途採用する必要がある。

独立`test_market_inspection.py`は27件成功した。正常DBの反復読取と全ファイルhash不変、read-only接続と外部access・extension自動取得/読込の無効化、欠損・schema・VIEW・link/reparse・Dropbox・sidecar、観測中DB変更時のUNKNOWN非要約、source分類、および各固定warningをオフラインfixtureで確認した。さらにsynthetic seedを、`str(seed)`と互換なASCII整数文字列（例`0`、`42`、`-42`）だけ完全なprovenanceとして扱い、`--42`、`+42`、空文字、Unicode数字を`PROVENANCE_INCOMPLETE`にする境界8件を追加した。seed新規8件を含む関連inspection/CLI 38件が通過した。実通信は行っていない。

## 10. 今回限定採用：候補とpacket入力の同一DB transaction

現在の`packet_cli.generate`は、`run_signals(home, strategy, as_of)`が一つ目のDuckDB接続で候補を計算して閉じた後、二つ目の接続でcalendar、as_of価格、provenanceを読む。二接続の間に訂正がcommitされると、候補は訂正前の価格・信用残・上場情報等から生成された一方、`snapshot_id`には訂正後の価格・calendar・provenanceが入る可能性がある。後段hashが正しくても、候補計算元とpacket入力が同じDB snapshotだったことを示せない。

既存公開`run_signals(home, strategy, as_of)`の3引数動作を維持したまま、同じ処理を呼出側所有connectionで行う内部関数を`api.py`へ分離した。既存APIは従来どおり自分で一接続を開き、BEGINからCOMMITまでの間に内部関数を呼ぶ。`packet_cli.generate`も一接続のSQL読取transaction内で次を順に行う。

1. source provenanceを検査する。
2. 内部関数へ同じconnectionを渡して戦略候補を計算する。
3. 同じconnectionで次営業日、as_of価格、限定provenanceを読む。
4. transaction内の全SELECTが完了したらCOMMITし、そこで得た値だけからsnapshot_idとProposalを構成する。例外時はROLLBACKする。

公開関数へoptional connectionを追加せず、homeとconnectionの不一致や所有・close責任を公開契約へ増やしていない。戦略取得、`require_known_data_mode`、候補の`asdict`化は内部関数で共有する。packet CLIの旧試験mockが`packet_cli.run_signals`差替えに依存していた場合は、同一snapshotを確認する実DB fixtureへ更新する必要がある。未使用aliasを残して旧mockが有効に見える状態にはしない。

この変更が追加したSQLは読取だけで、候補・価格・calendar・provenanceの時点を一transactionへ揃える。ただし接続は既存`db.connect`を維持し、engineの`read_only=True`や市場inspectionのpath guardを採用していない。そのため未作成home/DBの作成副作用を変えず、接続自体が書込不能である保証も追加しない。外部同一プロセスからの訂正をfixtureで検証することと、既存engine/config互換を優先した限定採用である。

engine read-only、欠損home/DBの非作成、DB path/link/sidecar guardをpacket生成にも適用する案は別の未採用事項として残る。同一transactionは次も保証しない。

- J-Quants response、events/lots file、戦略configの同一原本性や同時差替え防止。
- `publication_estimated`が既存全行を正しく表すこと、調整OHLC/volumeの基準、単元100の正しさ。
- 外部APIの現在仕様、期待期間・銘柄集合の完全性、現在シグナル・承認・実通信許可。

受入条件は、候補計算後に別connectionから価格・provenance訂正をcommitしても当該generateが一方のtransaction snapshotだけを使うこと、次回generateでは訂正後snapshotへ進むこと、公開`run_signals`の3引数利用が不変であること、例外時にtransactionをROLLBACKすることである。欠損・不正入力でDBやhomeを作らないことは今回の受入条件に含めず、既存mapping不正がDB計算前に止まる契約だけを維持する。

## 11. 今回限定採用：格納予定数値の有限性guard

J-Quants responseからDuckDBのDOUBLE列へ格納する予定値をDataFrame構築時に検査する。対象は価格OHLC、volume、turnover、adj_factor、信用買残・売残、TOPIX closeである。`None`は既存の許容位置で保持し、OHLCの`None`は従来どおり後続検査で取込を拒否する。builtin bool、数値変換不能、overflow、NaN、正負Infinityを固定ValueErrorでtransaction前に拒否する。有限な数値文字列は既存DuckDB変換互換のため元値のまま許可する。

このguardは格納変換の不正値を取り込まない範囲だけを採用した。正負・ゼロの業務値域、調整OHLCの部分欠損時の選択、volume/turnoverとの調整基準、公表日fallback、外部API schema、データ適格性を変更・証明しない。NULLとして許容されるvolume等は市場inspectionのwarning対象であり、自動補正しない。

独立`test_jquants_numeric_safety.py`は59件成功した。10数値項目のInfinity、NaN、文字列Infinity、bool、不正文字列を拒否し、拒否前後で5市場表とprovenanceが不変であること、有限数値文字列、volumeのNULL、raw/adjusted OHLCの既存選択、公表日+4日fallbackをオフラインfixtureで確認した。既存J-Quants4件との合計63件も再実測した。実通信は行っていない。

## 12. 限定採用済み：phase-1共通DB接続の保存先guard

AGENTS.mdと共通フェーズ1仕様が求める「実行DBはDropbox外」を、書込可能な共通入口`db.connect(home)`で強制した。これにより、公開`init_db`、`load_synthetic`、`run_signals`、`run_backtest`、`fetch`および直接`connect`利用が同じ保存先境界を通る。今回の採用範囲は保存先と既存path型の検査に限り、既存データの移動や修復は行わない。

### 実装済みの接続境界

1. homeをresolveでlink追跡する前のabsolute pathとして構成し、homeと既存の親componentを`lstat`する。通常directory以外、symlink、junction、Windows reparseを副作用前に拒否する。path componentに大文字小文字を問わず`dropbox`を含む場合も、mkdirやDB open前に拒否する。
2. homeが欠損していて安全な通常親directoryだけなら、既存どおり`mkdir(parents=True, exist_ok=True)`する。作成後に親とhomeを再検査し、安全な通常directoryでなければDBを開かない。この前後guardはmkdirを挟む検査であり、外部変更に対する完全な原子性を意味しない。
3. raw `home/market.duckdb`が既存なら通常file、`market.duckdb.wal`が既存なら通常file、`market.duckdb.tmp`が既存なら通常directoryであることを検査し、各pathのlink/reparseを拒否する。DBが欠損している安全なhomeでは、既存どおりDuckDBによる新規作成を許す。
4. 検査後は従来の書込可能`duckdb.connect`と`SET threads=1`を行う。`SET threads=1`失敗時は取得済みconnectionをbest-effortで閉じ、元の例外を保持する。DuckDB自身が作ったDB/WALの自動削除、禁止場所からの移動、既存homeの自動修復は行わない。

read-only市場inspectionは別入口であり、既存DB欠損を作成せず、通常WALの存在自体を観測保留にする。共通`db.connect`へその挙動を移すと、正常なwriter接続・初期化・transaction回復を壊すため混同しない。

### 確認済みの互換性と受入範囲

- `tmp_path`等の通常Windows absolute path、既定`~/.ai-trader`、安全な相対homeは従来どおり作成・反復接続できる。相対homeはcwd基準absoluteへ変換するが、保存場所の意味を変えない。
- fresh homeの`init`、synthetic load、J-Quants fake取込、run_signals、backtestと共通acceptanceを通し、既存の通常WAL fileと通常tmp directoryも接続可能であることを確認した。
- Dropbox名を含む欠損pathはhomeを作らず拒否する。既存home、親、DB、WAL、tmpについて、reparse属性注入を含む独立境界試験でDuckDB open前拒否を確認した。
- DBを指す内部linkも拒否する。resolve後が同じhome内だから許可する例外を設けず、検査からlinkを隠さない。
- API回帰25件と独立path境界14件が通過した。接続設定失敗時のconnection解放と元例外保持も独立試験に含む。

このguardは保存先境界であり、外部プロセスが検査直後にpathを差し替える競合を完全に原子化しない。DuckDB内部のWAL整合、DB schema、source、業務row、取得原本、調整基準、公表日推定も証明しない。OS ACLや専用serviceによる排他を新設せず、通常WALを危険状態として自動削除しない。

## 13. 限定採用済み：backtestの単一DB読取transaction

`backtest.run`は、data mode、価格、指数、strategy候補生成が行う全SELECTを、1つのDuckDB connection上の明示的な`BEGIN`から`COMMIT`までに収める。これにより、価格と指数を固定した後、別connectionから入った訂正をstrategyだけが読む混在を避け、1回のbacktest内で候補生成元と評価用市場行のDB snapshotを揃える。

例外時はbest-effortで`ROLLBACK`して元例外を保持し、connection contextから解放する。集計と出力directory・CSV・JSON・HTMLの生成は正常`COMMIT`後に始まるため、DB読取またはstrategy生成の例外では新しい結果出力を作らない。研究パラメータ、候補規則、約定仮定、評価式、出力形式、公開`run_backtest` APIおよび既存DB engine設定は変更しない。

この採用はDuckDB内のSQL読取snapshotに限定する。DB外fileの同時差替え、取得原本との一致、実績値としての適格性、調整OHLCの意味、公表日推定の正しさは保証しない。また、共通`db.connect`の既存write接続・欠損DB作成互換を維持しており、engineのread-only化は今回の契約に含めない。

単一snapshotの独立試験3件が通過し、共通受入とtemporal回帰10件も通過した。正常時の1回のBEGIN/COMMIT、例外時のROLLBACK・connection解放・新規出力なし、およびstrategy生成中の外部訂正を同じrunへ混在させないことを確認した。

## 14. 独立fixtureと個人向けV2の公開仕様確認

旧形式の研究互換取込は保持し、別の専用modeで[来歴fixture](PROVENANCE_FIXTURE.md)を検証した。全体1725件合格時点で55件の専用試験を含む。これはB案の実データ採用、旧home移行、公式データ取得ではない。

[個人向けJ-Quants公開資料レビュー](JQUANTS_PUBLIC_DOC_REVIEW.md)に、V2の認証・endpoint・短縮列の差と未確認事項を記録した。現行`jquants.py`はlegacyであり、公開資料の確認だけで実接続済みやV2対応済みとは扱わない。新しい`jquants_v2_contract.inspect_v2_page`は独立したメモリ内の仮レスポンス形状検査で、取込・DB・シグナルへ接続しない。
