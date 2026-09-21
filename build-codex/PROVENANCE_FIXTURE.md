# 来歴付き固定fixtureの隔離技術試作

## 位置づけ

この試作は、外部通信を使わない固定fixtureについて、取得runと各rowの来歴を新規専用homeへ保存し、read-onlyで照合する限定実装である。実データ方針、現在の外部API仕様、価格調整の正しい基準、公表日推定の実運用利用、入力の業務的適格性は採用しない。既存資料に記録されたデータ不確実性を解消した証拠として扱わず、すべての表示で`ready_for_live=false`を維持する。

[市場データ来歴v1実装案](MARKET_PROVENANCE_DRAFT.md)は引き続き未採用である。本試作はその全面採用ではなく、専用mode、固定mapping、transaction内履歴の保存と読取検査だけを隔離して試す。

## 専用homeとmode

- 初回初期化は呼出時に存在しない新規homeだけを許可する。初回成功後の同じ専用homeには、完全一致のno-opと明示fixtureによる訂正runだけを許可する。それ以外の既存directory、既存DB、既存schemaを自動移行、追記、修復、削除、再利用しない。
- `provenance.data_mode`は厳密に`provenance_fixture`とする。`synthetic`、`jquants`、未知modeと同じhomeへ混在させない。
- 既存の`fetch`、`load_synthetic`、`run_signals`、`run_backtest`は`provenance_fixture` modeを許可せず、既存のknown mode契約に従って拒否する。packet、daily runner、審査、通知、発注へ自動接続しない。
- 既存synthetic/jquants home、共通フェーズ1仕様、既存変換C、既存成果物の意味を変更しない。
- 実行DBはDropbox外とし、既存共通path guardを通す。禁止場所からの自動移動は行わない。

## 固定入力

入口は通信client、token、URL、入力pathを受けず、呼出側がコード内で固定した5表相当のmappingだけを受ける。対象は`listed`、`prices_daily`、`margin_weekly`、`index_daily`、`calendar`に限定する。要求期間と記録時刻も明示入力とし、暗黙の現在時刻や外部responseを補わない。

入力mappingは全件をtransaction前に正規化・検査する。table名、row key、列、DATE、bool、null、有限数値、文字列を型で区別し、tableとrowは固定順でcanonical JSONにする。NaN、Infinity、未知table・列、重複key、部分的な調整OHLCなど、試作が意味を一意に記録できない入力はDB変更前に拒否する。

## runとrow履歴

一つの成功transactionで、主要5表の現在値、取得run、runに属する全row、現在行からrunへの参照を確定する。成功runだけを保存し、通常例外では全体をrollbackする。FAILEDをCOMPLETEDとして残さず、失敗を推測して再開しない。

run-rowにはrow keyとhashだけでなく、正規化したrow全体のcanonical JSONも保存する。訂正で同じ主キーの現在値が変わる場合は新しいrunを追記し、以前のrun-row JSONを上書き・削除しない。これにより試作DB内では旧run時点に保存した値を履歴として読める。canonical JSONの保存は外部response原本、署名、取得者、外部serviceの正しさを証明しない。

canonical JSON bytesとSHA-256は同じ正規化規則から作り、run IDも固定入力から決定する。同一run IDの完全一致はno-opにできるが、期間、contract、canonical JSON、row索引、hashのいずれかが不一致なら衝突として拒否する。hash一致をowner証明や悪意ある改ざん防止へ読み替えない。

## transactionと失敗境界

1. 初回ならhomeが非存在であること、継続なら既存homeが完全な専用mode/schemaであることを副作用前に確認し、保存先、引数、固定mapping全体を検査する。
2. 専用schemaを初期化し、`data_mode=provenance_fixture`と固定markerを同じ試作の範囲で設定する。初期化途中のhomeを既存homeとして自動採用しない。
3. 明示transaction内でmode・markerを再確認し、主要5表、run、canonical run-row JSONとhash、現在行来歴を保存する。
4. COMMIT前に現在行と来歴の一対一対応、run参照、保存JSONの再hash、件数を照合する。
5. 例外時はbest-effortでROLLBACKし、元例外を保持する。プロセス強制終了、WAL、電源断、rollback不能時の自動復旧はこの試作で採用しない。

## 読取inspection

`inspect_provenance_fixture(home)`は、既存DBをread-onlyかつexternal access・extension自動取得/読込なしで開き、mode、主要・来歴tableのschema/主キー、COMPLETED run、run-row canonical JSONとhash、現在行参照・現在値、enum形状を検査する。DBは128 MiB、各対象tableは100000行を観測上限とする。DB前後hashまたはsidecarが安全に照合できなければUNKNOWNとし、整合時も`VERIFIED_FIXTURE`は固定fixtureの記録整合だけを意味する。次を固定する。

```text
ready_for_live: false
current_signal: false
read_only: true
automatic_resume_allowed: false
```

marker欠損、別mode、不正JSON/hash、孤立・欠損・余分なrow、現在行との不一致、sidecar、link/reparse、観測中変更では安全側へ分類し、途中のrow値や候補を返さない。inspectionはDBを作成、移行、修復、削除せず、旧runを現在値へ自動復元しない。

## PowerShellでの明示実行例

次は引数形を示す置換用の例であり、記載したfixture filename、home名、期間、記録時刻で成功した実績ではない。`$FixturePath`は利用者が内容を確認した固定JSONへ、期間と`--recorded-at`はそのfixtureに対応する明示値へ置き換える。初回の`$FixtureHome`は存在してはならず、その親directoryだけを事前に用意する。Dropbox配下を指定しない。

```powershell
Set-Location 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader\build-codex'

$FixturePath = 'C:\replace\fixed-provenance-fixture.json'
$FixtureParent = Join-Path $env:LOCALAPPDATA 'ai-trader-fixtures'
$FixtureHome = Join-Path $FixtureParent 'provenance-fixture-example'
New-Item -ItemType Directory -Path $FixtureParent -Force | Out-Null

python -m aitrader.provenance_fixture_cli ingest `
  --home $FixtureHome `
  --fixture $FixturePath `
  --from 2026-08-01 `
  --to 2026-08-31 `
  --recorded-at '2026-09-11T06:30:00+09:00'

python -m aitrader.provenance_fixture_cli inspect --home $FixtureHome
```

ingestの終了code 0は固定fixture履歴を保存できた意味だけで、実データ採用や売買準備完了ではない。inspectは`VERIFIED_FIXTURE`だけ終了code 0、それ以外は2とする。既存homeを初回用として空にしたり移行したりせず、同じ専用homeへの再入力は完全一致no-opまたは明示訂正runの契約だけを使う。CLIは外部通信を行わない。

### 2026-09-11のオフライン実CLI実測

Dropbox外の`C:\Users\s\AppData\Local\Temp\ai-trader-provenance-demo-20260911-043730`を新規専用homeとして使用した。fixture 1の初回取込は`COMPLETED`、同じ主キーを持つfixture 2の訂正取込は別runの`COMPLETED`、fixture 1の旧batch再入力は`NO_OP`となった。その後のinspectionは`VERIFIED_FIXTURE`、`runs=2`、主要5表は各1行で、`ready_for_live=false`、`current_signal=false`、`automatic_resume_allowed=false`を返した。これは固定した人工fixtureと保存履歴の実測であり、実データ取得または外部仕様適合の実測ではない。

inspectionに用いたPowerShell commandは次のとおりである。Python executableとhomeをabsolute pathで指定した。外部通信は行っていない。

```powershell
Set-Location 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader\build-codex'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m aitrader.provenance_fixture_cli inspect --home 'C:\Users\s\AppData\Local\Temp\ai-trader-provenance-demo-20260911-043730'
```

## 保証する範囲

- 非通信の固定fixtureを新規専用homeへ隔離する。
- 現在行と、成功した各runのcanonical row JSON・hashを同じDB transactionで保存する。
- 訂正前のrun-row JSONを履歴として保持し、同一入力の決定性と衝突を検査できる。
- 既存modeの入口が専用modeを候補生成やbacktestへ通さない。
- 失敗を実データ不足の市場判断、NO_SIGNAL、運用許可へ変換しない。

## 保証しない範囲

- 固定fixtureが実際の外部response原本、現在のJ-Quants契約、公式calendar、実単元、実eventと一致すること。
- 調整OHLC、volume、turnover、factorの基準、公表日または推定日の正しさ。
- 期待期間・銘柄集合・訂正履歴の完全性、実績値の適格性、戦略収益性。
- 実通信、実データ取込、実AI、通知、注文、scheduler、見張りの許可または接続。
- 既存homeの移行、安全な自動再開、複数DB/fileをまたぐ原子性、fsyncと電源断耐久性。

## 検証項目

prototype対象55件が通過した。内訳は先行48件（writer 11、CLI 14、inspection 18、既存経路からの隔離5）に、row順序と同値正規化2件、実別process 1件、基礎6表のschema境界4件を加えたものである。新規home正常作成、既存空homeのDB作成前拒否、専用mode固定、5表・run・run-row・現在来歴の同一transaction、canonical JSON/hash決定性、同一入力no-op、訂正時の旧JSON保持、衝突とrollback、inspectionの整合・不整合分類、CLI入力境界、既存`fetch`・`load_synthetic`・`run_signals`・`run_backtest`・packet生成の専用mode拒否、`ready_for_live=false`を固定fixtureだけで確認した。

初版レビューで見つかった空schemaの`VERIFIED_FIXTURE`化は修正され、runと各主要表の非空を要求する反証が追加された。historical canonical JSONについても、dateやnameの型を不正にしたうえで関連hashとrun IDまで再構成した自己整合風の改変を拒否する境界を確認した。その後の意味整合7件を含め、全体1885件の実測で確認済み。追加工程の試験はREADMEで別集計する。実通信は行っていない。

### 来歴の意味上の自己整合（2026-09-11 追補）

hashの一致に加え、各COMPLETED runで5表すべての行が存在すること、volume/turnoverのMISSINGとNULLの一致、DEFAULT_ONEの調整係数が1であること、DATE_PLUS_4の日付が基準日から4暦日後であることを検査する。既存変換で生成可能なfactor PROVIDEDとNULLの組合せは拒否しない。この検査も固定fixtureの内部整合だけで、実市場の正しい公表日や調整係数を証明しない。
