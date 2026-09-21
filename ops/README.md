# ops — 運用台帳・通知ゲート・ハードリミット（フェーズ2、Claude 実装）v0.2

2026-09-08 v0.2: Codex 再レビュー（R01〜R19）を反映。変更点は末尾の「v0.2 変更点」と `Claude対応結果.md`。

`aitrader_ops` は市場データDBに依存しない純粋ロジック。入力はすべて引数で渡す。LLM 呼び出し・LINE 送信・証券接続・発注は含まない。

## セットアップ・テスト

```powershell
cd ops
python -m venv .venv ; .\.venv\Scripts\Activate.ps1      # Linux: source .venv/bin/activate
python -m pip install -r requirements.txt                 # pytest のみ（本体は標準ライブラリだけ）
python -m pytest ../common/tests/phase2 -q                # 台帳・ゲート・パケットの共通受入テスト
```

共通テストは `sys.path` に `../ops` と `../build-codex` を自分で追加するので、`build-codex/aitrader/packet.py`（Codex 実装）が同じ階層にあれば追加設定なしで動く。`packet_hash` は Codex 側の関数を使い、ops 側の `compute_packet_hash` は同じ正規化ルールでゲート内の再計算に使う（両者が食い違えばゲートが不許可を返す）。

## 構成

```
aitrader_ops/
├─ models.py   Proposal / Verdict、入力型（PositionIn, OpenOrderIn, TradeEvent, CsvFill）、出力型（Position, ReportResult, ImportResult, LedgerView）、
│              compute_packet_hash（packet.py と同一規則）、reserve_amount（Decimal 計算）
├─ ledger.py   Ledger（SQLite イベントソーシング）、LedgerError / LedgerNotInitialized
├─ limits.py   Limits（既定: 5銘柄・25%・1日2件・日次損失2%・DD10%・決算±2日・費用余裕0.2%）と境界判定（Decimal）
└─ gate.py     evaluate(...) → GateResult(allowed, category, reasons, reserve_amount, notes)
```

## 入力型（ISSUES #1 の正式化）

テストは `SimpleNamespace` を渡すため、実装は**属性名だけ**に依存する（`_get(obj, name)`）。正式な dataclass も `models.py` に用意した。

| 型 | 属性 |
|---|---|
| PositionIn | code, qty, avg_price, stop_order=False, stop_price=None |
| OpenOrderIn | proposal_id, code, side, qty, limit_price, broker_order_id=None |
| TradeEvent | event_id, proposal_id, kind(ORDERED/PARTIAL/FILLED/CANCELLED/SKIPPED/CORRECTION), qty, price, fee, at(tz-aware), source, broker_order_id=None, **replaces_event_id=None**（訂正対象。省略時は直前の約定報告） |
| CsvFill | event_id, proposal_id(None 可), code, side, qty, price, fee, at, source="csv", broker_order_id=None |
| LedgerView | cash, reserved, available, positions{code→Position}, reserved_positions{code→予約額}, daily_pnl=0, day_start_equity=None, **reserved_shares{code→売却予約株数}**（R19） |
| ReportResult | event_id, applied, error, duplicate, ignored_reason, trade_state, warnings |
| ImportResult | applied[], skipped[], pending[], errors{}。pending は `Ledger.pending_rows()` で参照し `resolve_pending(event_id, proposal_id, at, action=APPLY/DISCARD)` で人間が解決 |

## 状態遷移

通知: `CREATED → APPROVED → SENT → EXPIRED`、`CREATED/APPROVED → REJECTED`（REJECTED は実取引状態を SKIPPED にし予約解放）。初期スナップショットの open_orders は `EXTERNAL`（発注済み扱い）として登録し、BUY は現金予約・SELL は株数予約に含める（R06/R07）。
実取引: `UNCONFIRMED → ORDERED → PARTIAL → FILLED`、`→ CANCELLED`（未約定のまま取消）、`→ SKIPPED`（明示的見送り。約定後は不可）。
通知の EXPIRED は実取引状態・予約を**変えない**。`unconfirmed()` は 通知が SENT/EXPIRED かつ 実取引が UNCONFIRMED/ORDERED/PARTIAL のものを返す。

## 解釈（共通仕様・ISSUES との対応）

| 論点 | 本実装 |
|---|---|
| 予約額（ISSUES #4） | BUY: `未約定残数量 × 指値 × (1+fee_margin)` を円で切り上げ（Decimal）。部分約定ごとに縮小。注文が完了（FILLED/CANCELLED/SKIPPED/REJECTED）したら 0 で固定し、訂正で約定を打ち消しても復活しない（R02）。SELL: 現金予約 0、保有株数を `reserved_shares` で予約（同銘柄の二重売却を通知作成時に拒否）。fee_margin はスナップショットに保存され再オープン時はそれを使う（R16） |
| PARTIAL / FILLED の qty（#2） | イベント単位の**増分**。残数量超過はエラー（残高不変）。FILLED でも残数量が残れば PARTIAL として記録し `warnings` に理由（予約は残す＝保守的） |
| CANCELLED | qty は取消数量（残数量以下）。適用後は残数量を全て解放し、約定ゼロなら CANCELLED、一部約定済みなら FILLED（完了扱い、警告付き）。完了後の訂正は価格・原価だけを直し予約は復活しない |
| SKIPPED | 約定ゼロのときのみ。予約解放。約定後の見送り報告はエラー |
| ORDERED | UNCONFIRMED → ORDERED のみ。約定後に遅れて届いた発注報告は無視（残高不変、`ignored_reason`） |
| CORRECTION（#3） | `replaces_event_id` があればその約定、なければ直前の未取消の約定を対象に、元を打ち消してから訂正値（qty 省略時は元の数量）で再適用。kind は元を継承。履歴は削除せず `reversed` 印を付ける。冪等。**SELL の打ち消しは売却額ではなく取り崩した取得原価（`cost_delta`）を戻す**（R01） |
| 冪等（#2） | 同じ event_id は payload が違っても無視（`duplicate=True`）。LINE と CSV は別 ID でも「同一 broker_order_id・同一数量・同一単価（時刻があれば時刻も）」なら**全通知を横断して**重複と判定（R12）。手数料だけ違う場合、LINE 報告は重複扱い＋警告、CSV は pending（R13） |
| CSV の曖昧一致 | 明示 proposal_id でも銘柄・売買方向が通知と違えば pending（R11）。broker_order_id が無く、既存約定と同じ数量・単価なら pending。proposal_id が無い行は 同銘柄・同方向の未決済通知が1件だけなら紐付け、それ以外は pending。pending 行は永続化され `resolve_pending` で解決 |
| 原子性 | 状態のコピーに適用 → 不変条件検査（現金≥0、余力≥0、保有≥0、売却予約≤保有）→ 成功時のみ `ledger_events` に追記 → 状態置換。検証エラー時は残高・保有・予約が一切変わらない。通知作成・約定・出金のどれでも余力が負になる操作は拒否（R03〜R05） |
| 負の余力 | 約定代金で現金が負になる報告はエラー。出金・費用で `cash − reserved` が負になる調整もエラー |
| 履歴（#11） | 残高を動かすのは追記専用の `ledger_events` のみ。起動時に全再生。`replay(at)` は業務時刻（effective_at）が at 以前のイベントを再生（通知作成にも作成時刻を持たせる。R14）。`replay_known(seq)` は記録順で seq 以下を再生（その時点で知っていた残高）。受信した報告は適用・重複・無視・保留・拒否のすべてを `ingest_attempts` に監査行として追記（残高イベントには再適用しない） |
| 日時 | naive datetime はエラー。比較は tz-aware 同士。UTC で渡された JST 翌朝も正しく扱う |
| 通知作成時刻 | `create_notice(p, at=None)` は at 省略時に現在時刻（JST）を業務時刻として記録。`unconfirmed(as_of)` はこの作成時刻で絞り込む |
| 複数ハンドル（R15） | 公開メソッドの入口で `MAX(seq)` を比較し、他ハンドルの追記があれば自動再生。書き込みは `BEGIN IMMEDIATE` で直列化し、競合時はやり直す。それでも順序3では単一書込主体を推奨 |

### ゲート（gate.py）

| 論点 | 本実装 |
|---|---|
| 許可条件 | 契約検査 ＋ claude/codex がちょうど1件ずつ APPROVE（同一 proposal_id・packet_hash） ＋ `now <= expires_at` ＋ 構造化イベント検査 ＋ 資金・保有・リミット。理由は全部列挙。`reasons`（日本語）と `reason_codes`（PACKET_MISMATCH, REVIEW_INCOMPLETE, EARNINGS_UNKNOWN, INSUFFICIENT_AVAILABLE, STOP_NEW, RECONCILIATION_PENDING, PROPOSAL_EXPIRED など）を分けて返す。NaN/Inf の指値は例外にせず理由付き不許可（R17） |
| packet_hash（#10） | p から再計算して一致を確認（審査後の改変を拒否）。Verdict の hash とも一致必須 |
| confidence / risks | 判定に使わない。`notes` に転記するだけ |
| 執行日・決算回避 | `expires_at` を JST に直した日付。決算回避は「暦日差 <= N」または「営業日差 <= N」のどちらかで該当すれば不許可（両方で数えて小さい方＝どちらか一方より必ず保守的）。営業日は `evaluate(..., business_days=[...])`（任意）があればそれ、無ければ月〜金。**v0.1 の「暦日の方が保守的」は誤りだった（金→月は3暦日・1営業日）。R18** |
| UNKNOWN / 欠損 | events に `next_earnings_date` / `margin_regulated` が無い、または UNKNOWN → 不許可（保留） |
| NEW と EXIT | side=SELL は EXIT。EXIT は STOP・未確認・日次件数・日次損失・DD の制限対象外、現金予約 0、**売却可能株数（保有 − 売却予約株数 reserved_shares）≥ qty** 必須（R19）。二重承認・期限・契約検査は EXIT にも必須 |
| 集中度 | (保有簿価 `qty×avg_price` ＋ 同銘柄の予約額 ＋ 本候補の予約額) ≤ max_weight × equity（Decimal で境界ちょうどを許可） |
| 銘柄数 | 保有銘柄 ∪ 予約中銘柄 に本候補（新銘柄なら +1）を加えて max_positions 以下 |
| 日次損失（#5） | `daily_pnl <= −daily_loss_stop × day_start_equity`（ビューに day_start_equity が無ければ判定しない）。DD は `equity <= peak × (1 − drawdown_stop)` |

## テスト結果（2026-09-08 v0.2）

`python -m pytest ../common/tests/phase2 -q` → **146 passed, 0 skipped, 0 xfail**（test_ledger 30 / test_gate 55 / test_packet 39 / test_ops_rereview 22）。
v0.1 時点では再レビュー22件のうち 16 件が失敗（130 passed / 16 failed）。v0.2 で A 分類 11 件・B 分類 5 件をすべて解消。共通テストは変更していない。

追加の自己検証（テスト外）: fee_margin=1% で作った台帳を既定値で再オープンしても予約額が変わらない、別ハンドルの約定を即座に反映、手数料差の CSV が pending になり `resolve_pending(DISCARD)` で消える、`ingest_attempts` に APPLIED/DUPLICATE/PENDING が記録される、`replay_known(seq)` と `replay()` の使い分け。

## v0.2 変更点（R 番号は build-codex/PHASE2_OPS_REVIEW.md）

R01 SELL 訂正で取得原価を戻す / R02 完了注文の訂正で予約を復活させない / R03〜R05 通知作成・約定・出金で余力が負になる操作を拒否（不変条件検査を一元化） / R06・R07 初期 open_orders を EXTERNAL 通知として予約に含める / R11 明示 proposal_id でも銘柄・方向を照合 / R12 全通知横断の約定キー照合 / R13 手数料差は pending / R14 通知作成に業務時刻を持たせ replay(at) から除外、replay_known(seq) を追加 / R15 別ハンドルの追記を自動再生・書込直列化 / R16 fee_margin をスナップショットに保存 / R17 NaN を理由付き不許可 / R18 決算回避を暦日と営業日の小さい方で判定、business_days 引数を追加 / R19 売却可能株数 = 保有 − 売却予約。加えて `ingest_attempts` 監査表、`reason_codes`、`pending_rows` / `resolve_pending` を追加。

## 既知の制限

- `business_days` を渡さない場合の営業日は月〜金（祝日を含まない）。本番では市場DBの calendar を渡すこと（順序3の実行器の責務）
- 同一 broker_order_id・同一数量・同一単価の部分約定が複数回ある場合、時刻が無い報告は重複と誤判定し得る（時刻付きで報告すれば区別できる）
- 逆指値の約定は `TradeEvent` の SELL 通知として扱う前提（設計書 12章）。逆指値専用の通知種別はフェーズ3（LINE 接続）で定義

## v0.3（2026-09-08）— 最新の契約と結果

この節は上記v0.2の解釈表に優先する。S番号の詳細・実装者・検証はClaude対応結果.mdの第3回を参照。ユーザーの指定に従いCodexがopsを修正した。共通仕様本文は未変更。

- 同株数のSELL価格・費用訂正では取得原価差分を固定する。数量訂正は後続取引再計算が未定義のため拒否。
- 注文中SPLITは拒否して照合。unconfirmedはEXTERNALを含む。いずれもv0.2より保守的な追加制約（旧履歴の互換性は別途確認が必要）。
- CSV横断照合は銘柄・売買方向を先に照合。時刻不足の一致はpending。LINEの時刻不足の別ID報告は照合エラー。同じevent_idの再送は冪等。
- resolve_pendingのactionは列挙検証、APPLYにも銘柄・方向・既存約定の検査を行う。任意actor/reasonを追加し監査に残す。未指定actorはunspecifiedで、認証済みとは扱わない。
- APPLIED/PENDINGの監査とイベントは同一書込トランザクション。拒否・無視・重複も監査記録。監査失敗なら残高イベントもロールバック。空IDも拒否監査を残す。CSV重複はDUPLICATE、ID内容違いはID_PAYLOAD_CONFLICT。
- replayは有効な約定が参照する後日通知の識別情報を補うが、通知の将来予約は含めない。全ての遅着・分割・匿名CSVの業務時点再現は未保証。記録順の当時状態はreplay_known(seq)。
- INVALID理由コードはREVIEW_INVALID。ただし日本語部分一致による他コードの分類は残る。
- 新規スナップショットと監査のschema_versionは3。旧イベントは書換えない。旧DBは必ずコピーで再生・差分確認してから移行する必要があり、自動移行は未実装。

実行：ops/から共通フェーズ2＋主系自前＋ops自前をまとめて **211 passed in 5.49s**。内訳162＋43＋6、skip/xfailなし。実CLI・実LINE・実口座操作はない。
## 2026-09-08 Codex 第4回：ops v0.3.1（最新）

C05/C12と移行CLIを実装。共通162＋主系43＋ops30＝235 passed in 5.85s（既存227全通過＋追加8、skip/xfailなし）。既存テストは未変更。詳細と6所見の採否は ../build-codex/OPS_V031_REPORT.md。

CSVの適用先を内部_applied_proposal_idに確定保存。MigrationErrorはseq/kind/reasonを保持。python -m aitrader_ops.migrate --check <copy>は読取専用検査、--apply <copy>は別の<stem>.upgraded.sqliteへbackupしPOLICY_UPGRADEマーカーを追記。原本も既存出力も上書きしない。旧規則区間の残高とreplay_knownの一致を検証し、不一致なら失敗。入力原本の移行・差替えは未実施。

端株SPLITは拒否。ADJUST/FRACTIONAL_CASHOUTは確定受取金の現金加算だけで、株数を切り捨てない。株数・原価調整全体の自動化は未実装。旧匿名CSVの業務時点再生、売却済みBUY訂正、reason_codes完全構造化は引き続き制限。
## 2026-09-08 第5回対応：ops v0.3.2（Codex実施・最新）

D01/E01の全入力内部キー除去、D06のマーカー1個・直前seq一致、D09の検証後公開と失敗一時ファイル削除、D12のcode必須・保有銘柄限定を実装。マーカーは業務時刻atでなく**記録順seqの境界**。成功時だけ `.upgraded.sqlite` を公開し、以前の「失敗コピーを残す」契約を置き換える。

ops/で指定対象 `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q` を実行：**257件＝255 passed / 2 failed in 7.15s、skip/xfailなし**。元252件は250通過・2失敗、追加F01〜F04は5ケース通過。既存テストは変更していない。D08/D09は失敗コピーの存在を要求する旧前提で失敗し、新しい削除契約に対する**テスト前提の誤り**と分類。全件通過ではない。

D08の手動照合は、原本・コピーのハッシュ保存→checkの旧新残高差と最初の相違seq特定→取得済み証券明細で約定・銘柄・数量・金額の照合→根拠付き訂正方針の確定→別コピーで合法な訂正と残高再検証の順。差額だけの現金調整や履歴書換えで強制一致させない。現APIで表現できない差は移行保留とし、照合済み開始残高への移行方式を別途設計する（今回未実装）。旧新差が残る入力は再試行でも拒否されるが、失敗ファイルが再試行を妨げることはない。

詳細な契約・実行環境・手動照合手順・残課題：[OPS_V032_REPORT.md](../build-codex/OPS_V032_REPORT.md)。ClaudeにはD08/D09の新契約に合わせた期待値更新と独立レビューを依頼する。実台帳・外部サービスは操作していない。
## 2026-09-08 第6回：ops v0.3.2 の独立レビュー（Claude）

ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**276 件＝275 passed / 1 failed（G07）、skip/xfail なし**（共通 162＋主系 43＋ops 71）。D08/D09 は採用された失敗時契約（一時ファイル上で検証→成功時だけ `.upgraded.sqlite` を公開→失敗時は削除）に期待値を更新し通過。追加した `ops/tests/test_v032_review_claude.py`（G01〜G13、19 ケース）のうち 18 通過。

G07（B）: 移行済み台帳に内部 API `_commit('POLICY_UPGRADE')` で 2 個目のマーカーを書くと書込時は受理され、次回起動で MigrationError になる。公開 API からは到達しない。提案は `_on_policy_upgrade` で `rule_version >= 3` を拒否（詳細は `ops/Claude対応結果.md` 第6回）。

D08 手動照合手順は方針に同意。契約の隙間 5 点（check の乖離 seq 出力、照合済み開始残高への新台帳方式と出所記録、売却後の精算金の受け皿、移行中の原本静止と昇格手順、Windows 実機での `--check` 確認）は `common/ISSUES.md` に追記。全件通過ではない。

## 2026-09-08 第6回対応：ops v0.3.3（Codex、最新）

G07を採用し、既にrule_version>=3の状態ではPOLICY_UPGRADEを書込前に拒否する。正規の旧規則2からの初回再生は許可。checkの旧規則再生失敗はMigrationErrorで停止する現契約を維持。

指定対象全体の最終実測：**279 passed in 7.62s（既存276＋追加3）、skip/xfailなし**。初回277通過・2失敗はD08の未解放接続によるhash変化と新規H03の補助ファイル不増加という前提誤り。H03を読取用WAL/SHMのみ許容する検査へ修正し再検証。既存テスト未変更。D08はcheck前のgc.collectだけでhash変化を再現でき、最終通過でも不安定さは残る。Claudeへ接続close修正を依頼。

D08契約5点：

1. 最初の旧新乖離seq出力は今回実装しない。担当者が静止済みの同じ履歴をseq昇順に逐次比較する**手作業**。check.violation.seqとは区別し、差が相殺され得るため二分探索しない。
2. 新ファイル＋新SNAPSHOTに照合済み残高を置く方式を契約案として採用。公開provenanceに旧台帳パス・hash・最終seq・照合記録とそのhash・担当者・時刻・理由等を残す。APIと遅着ID対応表は次回実装、未解決を捨てた移行は禁止。
3. 全量売却後の確定精算金はDIVIDENDを暫定受け皿とし、DEPOSIT（外部入金）に分類しない。noteはJSONでcategory=POST_EXIT_SETTLEMENT、settlement_type、code、source_event_id、source_document_sha256、reconciliation_id、actor、received_at、reasonを記録。数量・原価は変えない。税務上の配当分類を意味しない。書式検証・重複防止は未実装なので、人が受取IDと既存ADJUSTを照合し、net_external_flowへ外部入金として加えない。
4. STOPに加えて全書込元を止め接続を閉じ、WAL反映後に静止基準hash/seqを確定。照合コピーを検査・移行し、昇格前に原本hash/seq不変と残高を再確認。旧原本は `ledger.pre-upgrade.<JST日時秒>.<UUID>.sqlite` へ退避し、検証済み候補だけ運用パスへrename。最低直近3世代＋今回移行元を別媒体にも保持し、未照合世代は削除しない。差替え中は停止維持、書込再開後の単純巻戻しは禁止。耐久キュー・昇格自動化は未実装で、必要な受信保全ができない間は昇格保留。
5. Windowsの日本語・!・空白入り実パスで合成コピーのCLI --checkを実行、終了0、旧新cash=1,000,000、hash不変、移行出力なし。読取接続でもWAL/SHM補助ファイルは生成される。

[採否・完全なnote/provenance契約・原本静止から復旧までの手順・Windows実測・D08診断](../build-codex/OPS_V033_REPORT.md)。これは手順の文書化で、実台帳の昇格を実施したわけではない。
## 2026-09-09 第7回：ops v0.3.3 の独立レビュー（Claude）

ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**286 件＝286 passed、skip/xfail なし**（共通 162＋主系 43＋ops 81）。**全件通過**。

D08 の hash 不安定は Claude 所有テストの `sqlite3.connect` が close していなかったこと（WAL の反映が gc 時に起きる）が原因。4 ファイル 22 箇所を `closing()`＋commit に統一し、`test_v033_review_claude.py` I01/I02 で close 後の hash が gc・check・apply を挟んでも不変であることを固定した。G07（v0.3.3）は I03〜I05 で解消を確認。契約 5 点は同意（(2) 遅着 ID の遮断、(3) 精算 note の検証・重複防止は未実装で、`common/ISSUES.md` に次回実装案を記録）。

## 2026-09-09 第7回対応：ops v0.3.4（Codex、最新）

init_snapshotにprovenance=Noneを追加。RECONCILED_OPENINGの必須項目・SHA256形式・正整数seq・時差付き日時を検証しSNAPSHOTへ保存する。provenance付き台帳のTRADE/CSV_FILLはat<cutover_atなら理由「cutover 前の約定」でPENDING、時刻なしも保留、境界一致は通常検査対象。ReportResult.pendingを追加し、保留も監査と同時永続化。境界遮断行はAPPLYで迂回不可、照合のうえDISCARDまたは別途訂正する。provenanceなしは従来動作。出所パス/hashの実物照合や担当者認証は自動化していない。

DIVIDEND noteがJSONオブジェクトかつcategory=POST_EXIT_SETTLEMENTなら、settlement_type/code/source_event_id/source_document_sha256/reconciliation_id/actor/received_at/reasonを検証。受取種別2種、空文字禁止、hash形式、引数code/atとの一致、正整数円を要求し、(category,source_event_id)の重複はLedgerError。別ハンドル・再起動でも拒否。通常note/他categoryは互換、ADJUST拒否の独立監査は未追加。精算金は主系net_external_flowに含めない。旧履歴に不正note・二重精算がある場合は再生停止し得るので、コピーで確認してから移行する。

ops/の指定全体を実行：**325件＝324 passed / 1 failed in 10.77s、skip/xfailなし**。既存286は285通過、追加J01〜J07は39通過。I06は二重計上を期待する旧試験のため、新契約に対するテスト前提の誤り。既存試験未変更。I07はprovenanceなしなので従来どおり通過し、遮断確認にはprovenance付きケースへの更新が必要。全件通過ではない。

規則マーカー移行はmigrate CLIのみ・生SQL禁止。照合済み新台帳はprovenance付き公開APIで作成する別方式。手動取込運用では全取込を止め、LINEトーク履歴と証券CSVを未処理保管先にして昇格可能。停止/再開点・処理ID・保留を記録し、再開時に照合する。自動取込では耐久キューが別途必要。原本静止・WAL反映・全接続close・hash・世代バックアップ・復旧の条件は維持。

任意のmigrate --diffは今回見送り。乖離seqは手作業を維持し、性能・逐次再生エラー契約を別途検証する。[完全なAPI契約・失敗分類・制限・試験内訳](../build-codex/OPS_V034_REPORT.md)。実台帳・外部サービスは未操作。
## 2026-09-09 第8回：ops v0.3.4 の独立レビュー（Claude）

ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**364 件＝363 passed / 1 failed（K06）、skip/xfail なし**（共通 162＋主系 43＋ops 159）。I06/I07 を v0.3.4 の契約（同キー精算の拒否、provenance＋cutover の保留）に更新して通過。追加 `ops/tests/test_v034_review_claude.py`（K01〜K13、38 ケース）のうち 37 通過。

K06（A）: 保留行を DISCARD したあと同じ event_id を再送すると、`ledger_events.event_id` の UNIQUE 制約で `sqlite3.IntegrityError` が `import_csv_fills` / `report` から生のまま漏れ、同一バッチの他の行も処理されない（台帳自体はロールバックされ無傷）。v0.3 の DISCARD 導入時からの既存欠陥で、OPS_V034_REPORT の「再送すれば再び保留」とは一致しない。提案は DISCARD 済み ID を記憶して DUPLICATE（reason_code DISCARDED）にすること。全件通過ではない。

## 2026-09-09 第9回：ops v0.3.5（Claude 実装、最新）

K06（A）の修正と契約の隙間 4 点の採否。ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**364 件＝364 passed、skip/xfail なし。全件通過**（共通 162＋主系 43＋ops 159）。

- DISCARD 済み event_id は台帳が記憶する（`PENDING_RESOLVED(DISCARD)` の再生で復元）。再送は DUPLICATE（TRADE `duplicate=True` / CSV `skipped`）、監査 reason_code は `DISCARDED`（公開フィールドが変わっていれば ID_PAYLOAD_CONFLICT が優先）。残高イベントは追加しない。
- 履歴 ID の UNIQUE 衝突（`sqlite3.IntegrityError`）は LedgerError に畳む。CSV は `errors[eid]`、TRADE は `error`。バッチの他の行は処理を続け、生の DB 例外は漏れない。
- `provenance.cutover_at <= SNAPSHOT.at` を必須にした。違反は SNAPSHOT 登録時に拒否（保存されない）。
- 精算 note（`category=POST_EXIT_SETTLEMENT`）: DIVIDEND 以外の kind に付けると拒否（DEPOSIT は外部入金専用）。`source_event_id` は strip 後に重複判定。保有中の銘柄には記録できない（端株精算は FRACTIONAL_CASHOUT）。
- 契約の明記: provenance 付き新台帳の `replay(at < SNAPSHOT.at)` は空の台帳（cash 0・保有なし）を返し、旧台帳を再現しない。SNAPSHOT より前は旧台帳の `replay_known` で見る。
- OPS_V034_REPORT の「DISCARD した行を再送すれば再び保留され得る」は本版で置き換え。

## 2026-09-09 第10回：ops v0.3.6（Claude 実装、最新）

L02 の修正と L03 の契約決定。ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**388 件＝386 passed / 2 failed、skip/xfail なし**（共通 162＋主系/レビュー 60＋ops 166）。**全件通過ではない**。2 失敗は L03 の入力拒否採用と衝突する Codex 所有試験（`test_ops_v035_review.py::test_l03`、`test_v03_integrity.py::test_discard_audit_actor_and_replay`）で、期待値更新を Codex に依頼。

- `replay(at)`: SNAPSHOT の業務時刻が `at` より後なら空の台帳（現金 0・保有/予約なし）を返す。後着保留・遅着通知があっても `LedgerNotInitialized` は漏れない。SNAPSHOT ちょうどは含む。
- `resolve_pending`: 解決の `at` は保留行の業務時刻以後に限る（同時刻可・時刻なし行は制約なし）。違反は LedgerError で履歴に残らない。v0.3.6 より前に受理された逆順の解決を含む履歴は再起動時に `MigrationError(seq, PENDING_RESOLVED)` で止まる（`migrate --check` で位置が分かる。実台帳は未作成）。
- 訂正: 精算 `source_event_id` の strip は引数なし `str.strip()` で、全角空白・改行を含む前後の空白をすべて除去する（内部の空白・Unicode 正規化はしない）。第9回 B の「全角空白・改行は対象外」は誤記。
- 付記: `ops/tests/test_migration.py::test_m03` は `with sqlite3.connect` を close しないため原本 hash が不安定（8 回中 2 回失敗）。第7回 D08 と同じ原因。Codex 所有のため未変更。

## 2026-09-09 第11回：ops v0.3.7（Claude 実装、最新）— O01 遅着通知を参照する保留 APPLY の時点再生

ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**411 件＝410 passed / 1 failed、skip/xfail なし**（共通 162＋主系/レビュー 74＋ops 175）。**全件通過ではない**。1 失敗は `common/tests/phase2/test_ledger.py::test_unconfirmed_next_jst_morning` で、今回の変更とは無関係の**時計依存**（下記）。O 系列 7 件を含む Codex 追補は全通過。

- O01（A）解消: `replay(at)` が時点内の約定のために持ち込む「遅着通知の識別（context_only）」の対象を、TRADE / CSV_FILL に加えて **人間が APPLY で確定した保留行の解決（`PENDING_RESOLVED`, action=APPLY）** にも広げた。09:00 保留 → 11:00 作成の通知 → 10:00 に APPLY の履歴は、`replay(10:00)` で現金 900000・100 株・予約 0 になり、`replay(09:00)` / `replay(10:00 − 1µs)` は 1000000 のまま（先取りなし）。DISCARD は通知を参照しないので対象外（通知作成時刻以後にその通知の予約が通常どおり現れる）。L03 の入力制約は変えていない（契約変更なし）。
- 文書の明確化（第10回の記述を訂正）: 早い解決（保留行の業務時刻より前の APPLY/DISCARD）の拒否は **残高イベント列（`ledger_events`）・残高・保留行を変えないが、`ingest_attempts` に `REJECTED / VALIDATION` の監査行が残る**（実測）。「履歴に残らない」は残高イベントに限った意味で、監査なしではない。時点再生 `replay(09:00)` の `LedgerView` には pending 欄がなく、返るのは 09:00 時点の残高であって「10:00 の保留を含む」という意味ではない。現在の保留は `pending_rows()` で別に見る。`migrate --check` の逆順解決の位置は、マーカーなしなら exit 0 の `violation` 欄、マーカー後なら exit 1 の `MigrationError` 欄で、どちらでも seq / kind が取れる。
- 追加試験 `ops/tests/test_v037_review_claude.py`（P01〜P07、9 ケース、全通過）: 遅着通知への保留 APPLY を、解決前 / 解決時刻ちょうど / 通知作成時刻で再生、別オフセット（UTC・−05:00・+09:00）、通知作成時刻＝解決時刻、DISCARD は通知を持ち込まない、再起動後の一致と記録順再生（`replay_known` の各 seq）、再生の読取専用性（履歴・現在残高・seq 不変）、L02（開始残高前は空）と L03（早い解決は拒否・監査行のみ）の維持。
- 時計依存（Codex/共通所有、未変更）: `test_ledger.py` の `ledger` フィクスチャが `create_notice(p)` を `at` なしで呼ぶため `created_at` が実行時刻になり、`test_unconfirmed_next_jst_morning` の固定時刻 2026-09-09 06:50 JST を **2026-09-09 06:50 JST 以降は常に超える**（第10回追補の 06:40 JST 実測では通過、06:52 JST 以降の実測で失敗）。フィクスチャで `at=AT` を渡す修正を Codex に依頼。

## 2026-09-09 第12回：ops v0.3.8（Claude 実装、最新）— Q09 訂正時刻の契約、Q10 通知状態参照の識別補完

ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**442 件＝441 passed / 1 failed、skip/xfail なし**（共通 162＋主系/レビュー 93＋ops 187）。**全件通過ではない**。1 失敗は `build-codex/tests/test_ops_v037_review.py::test_q09_backdated_correction_before_apply_has_valid_replay` で、Q09 に入力拒否を採用したことによる**テスト前提の変更**（「09:30 の前倒し訂正を受理できる」前提）。Codex に期待値更新を依頼。時計依存だった `test_ledger.py::test_unconfirmed_next_jst_morning` は Codex の修正（`at=AT`）で通過。

- Q09（B）採用: **訂正（CORRECTION）の業務時刻は、訂正対象の約定が残高に反映された業務時刻（有効時刻）以後に限る**。通常の約定の有効時刻はその約定時刻、保留 APPLY の有効時刻は解決時刻、訂正の訂正はその訂正の時刻。違反は LedgerError（`ingest_attempts` に REJECTED/VALIDATION、残高イベント列・残高・seq は不変）。同時刻（別オフセット表記も）は可、時刻なし訂正には制約なし。`rule_version ≥ 3` にのみ適用。約定記録に `effective_at` を持たせて判定する。
  - 理由: 保留 APPLY に限らず、通常の約定（10:00）を 09:30 で訂正した履歴も従来から `replay(09:30)` で「訂正対象がない」と停止していた（実測）。時点再生で訂正を「対象が反映されるまで保留」する案は、業務時刻 09:30 の訂正が 10:00 に効くという二重の時刻意味を持ち込み、未来 APPLY の先取りも招くため採らない（L03 と同じ判断）。訂正で約定時刻を前に直す用途は、対象の有効時刻以後の時刻で訂正し、本来の約定時刻は `broker_order_id`/note で残す運用とする。
  - 互換: v0.3.8 より前に受理された前倒し訂正を含む履歴は、再起動時に `MigrationError(seq, TRADE)` で止まり、`migrate --check` の violation に位置が出る（R05）。実台帳は未作成のため影響なし。旧 v0.2 規則区間（POLICY_UPGRADE 前）は対象外。
- Q10（B）採用: **通知状態（NOTICE_STATE）と期限切れ（EXPIRE の proposal_ids）が時点内にあるとき、参照先の遅着通知は識別だけ（context_only）を補完**する。O01 と同じ方針。予約は持ち込まず、金銭効果はない（状態は `LedgerView` の公開欄にない）。EXPIRE は列挙された ID だけを対象にする。通知作成を消した故障注入では従来どおり LedgerError / MigrationError（不明参照の一律無視ではない）。
  - 通知状態の業務時刻を作成時刻以後に制限する案は不採用。Codex Q04（作成 14:00 の通知への 11:00 承認・12:00 送信）が受理・通過している既存契約と衝突し、旧履歴を MigrationError にする割に金銭的な意味がないため。
- 追加試験 `ops/tests/test_v038_review_claude.py`（R01〜R10、12 ケース、全通過）: 前倒し訂正の拒否と監査、有効時刻ちょうど（+09:00/UTC/−05:00）と以後の受理・訂正の訂正、通常約定の前倒し訂正拒否、時刻なし訂正、旧版で受理された前倒し訂正履歴の MigrationError と check 位置、作成前の状態変更・期限切れの各中間時点再生（予約 0・非先取り・再起動・replay_known）、複数 ID の EXPIRE、故障注入の拒否、Q04 の形の維持、L03/O01/合算・超過拒否の維持。

## 2026-09-09 第13回：ops v0.3.9（Claude 実装、最新）— T06 EXPIRE の不明参照、T07 時刻なし訂正の有効時刻継承

ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**469 件＝468 passed / 1 failed、skip/xfail なし**（共通 162＋主系/レビュー 108＋ops 199）。**全件通過ではない**。1 失敗は `build-codex/tests/test_ops_v038_review.py::test_t07_timeless_correction_chain_remains_replayable` で、T07 に「時刻なし訂正は対象の有効時刻を継承する」契約を採用したことによる**テスト前提の変更**（時刻なし訂正を 09:00 で再訂正できる前提）。Codex に期待値更新を依頼。T06 の 3 ケースは解消。

- T06（A）解消: `EXPIRE` の `proposal_ids` に台帳にない ID があれば `LedgerError("通知 … がありません")`（KeyError は漏れない）。再起動は `MigrationError(seq, EXPIRE)`、`migrate --check` は旧規則でも再構築できないため `MigrationError`（violation の正常戻りではない）。列挙の一部だけ不明でもその ID を名指しして失敗する。未知 ID の一律無視はしない。
- T07（B）採用: **時刻なし訂正（CORRECTION, at=None, replaces_event_id あり）は、訂正対象の有効時刻を継承する**。保留 APPLY を対象にすれば解決時刻、通常約定なら約定時刻、訂正なら（継承済みの）その訂正の有効時刻。時点再生ではその継承時刻を時刻フィルタに使うので、対象より前の時点には現れず（非先取り）、対象以後の時点で対象と一緒に効く。以後の日時付き訂正は継承時刻を基準に Q09 の制約を受ける（09:00 の再訂正は拒否、10:00 ちょうど以後は受理）。「時刻なし訂正に制約なし」は入力時に変わらない（時刻がないので時刻制約は掛からない）が、有効時刻は持つ。対象が時刻なし（時刻なし約定を時刻なし訂正）なら継承する時刻もなく、従来どおり常に時点内。
  - 不採用: 時刻なし訂正への入力拒否（時刻なし保留行と同様、時刻を持たない入力を退ける理由がない）、時点再生で対象欠落を読み飛ばす案（不明参照の無視）。
  - 互換: v0.3.9 より前に受理された「時刻なし訂正 → それより前の日時付き再訂正」を含む履歴は再起動時に `MigrationError(seq, TRADE)`、`migrate --check` の violation に位置（S07）。実台帳未作成のため影響なし。旧 v0.2 区間は対象外。
- 追加試験 `ops/tests/test_v039_review_claude.py`（S01〜S11、12 ケース、全通過）: EXPIRE 不明参照の replay/再起動/check（位置付き）と部分不明、時刻なし訂正の継承（保留 APPLY・通常約定・連鎖・時刻なし対象）、継承時刻を基準にした再訂正の拒否/受理（別オフセット）、旧版履歴の MigrationError と check 位置、SELL 取得原価、部分合算と取消後の訂正、cutover 遮断（日時付き訂正は拒否・時刻なし訂正は従来どおり cutover 保留）、Q09/Q10 の維持。
- Codex 指摘 4 の訂正: `test_v038_review_claude.py` R06 のコメント（EXPIRED は予約を解放しないので作成時刻以後は 60120 が残る。assert を追加）、R07 の説明（other も期限切れ集合に入る。列挙外の非補完は Codex T05 が直接観測）。

## 2026-09-09 第14回：ops v0.3.9 の双方確認完了（Claude。文書の明確化 C01/C02、実装変更なし）

ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**487 件＝487 passed、skip/xfail なし。全件通過**（共通 162＋主系/レビュー 126＋ops 199）。Codex 第13回（U01〜U05 18 件、T07 期待値更新）と一致。ops v0.3.9 の実装は変更していない。

- C01（旧規則区間の適用範囲）: 「旧 v0.2 区間は対象外」は **Q09 の入力制約（訂正時刻 < 対象の有効時刻を拒否する比較）に限る**。訂正が対象の有効時刻を継承する処理（`_on_trade` の `effective`）と時点再生の事前走査（`_effective_rows`）には版判定がなく、POLICY_UPGRADE 前の旧区間の履歴にも適用される。したがって旧区間では「受理済みの残高（現在残高・`replay_known`）は保持される」が、「過去版と同じ時点表示」までは保証しない（旧版では時刻なし訂正が常に時点内だったが、v0.3.9 では対象の有効時刻以後にしか現れない）。Codex U04 は正規 `migrate.apply` で旧区間へ保存した受理済み残高 962000 の保持、マーカー後の新操作への Q09 適用、check の 2 形式と原本不変を確認している。
- C02（`replaces_event_id` 省略時）: 公開モデル（`TradeEvent.replaces_event_id`）は省略可能で、共通仕様も「直前の約定を対象」とする。省略時、**状態側**（`_on_trade`）は直前の有効約定を対象に選び、時刻なし訂正はその `effective_at` を継承する。一方、**時点再生の事前走査**（`_effective_rows`）は明示 ID がないと対象を解決せず、時刻フィルタ用の時刻は None（常に時点内）のまま。よって「継承できず従来どおり」は事前走査の制限であり、明示 ID 付きと同じ時点再生の保証（対象より前の時点に現れない）は省略経路には及ばない。省略経路で対象が時点外の時点を再生すると従来どおり「訂正対象となる約定報告がありません」で停止する（不明参照の無視ではない）。「呼び出し側が常に指定する」は現行の試験・運用の事実であって API 保証ではない。これは既知の制限の明確化で、新しい契約や実装は追加していない。
- 開発ループの扱い: 次の Codex 宛て B は「引き渡し不要（ops v0.3.9 の双方確認完了）」として公開。自動連携自身のレビュー・常駐起動の確認は別枠（auto チャネル）で続ける。

## 2026-09-09 第16回：ops v0.4.0（Claude 実装、最新）— フェーズ2 順序4 judges / notify

ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**633 件＝633 passed、skip/xfail なし。全件通過**（共通 162＋順序4 受入 116＋主系/レビュー 74＋ops 281）。

- `aitrader_ops/judges.py`: `review(...)`（stub / replay 専用。締切 = min(expires_at, 07:15 JST)、失敗優先順位 入力 hash → 締切 → timeout → process_error → malformed → 応答 ID/hash、決定ログ JSONL）と `build_cli_request(...)`（起動計画の純粋生成）。transport=cli は隔離環境の確認まで ValueError。
- `aitrader_ops/notify.py`: `render_message`（Flex カード / 状態文言、confidence 不掲載、楽天リンク検証）、`Notifier`（SQLite outbox、flush 直前の期限・台帳・STOP・月予算再確認、SENDING claim、同一 retry_key、`reconcile_sent` で復旧）、`handle_webhook`（生 body 署名検証 → userId → webhookEventId 一意管理 → `Ledger.report` 写像、STOP/RESUME）。transport=line は本ループでは無効。
- `Ledger.proposal(proposal_id)` を追加（通知に記録された Proposal の公開コピー）。
- 設定: `settings.broker.link_template`（{code} のみ）、`settings.line.allowed_user_id` / `monthly_budget`（既定 200）、任意 `settings.stop_file`。秘密は `.env` の `LINE_CHANNEL_SECRET` / `LINE_CHANNEL_ACCESS_TOKEN`（テストはダミー）。
- 未接続: 実 CLI・実 LINE・HTTPS 受け口・実口座。仕様案 §2.2 の隔離要件を満たした環境の確認が実接続の前提。

## 2026-09-09 第17回：ops v0.4.1（Claude 実装、最新）— Codex レビュー V01〜V13 の修正

ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`：**696 件＝696 passed、skip/xfail なし。全件通過**（共通 162＋順序4 受入 116＋主系/レビュー 123＋ops 295）。

- 決定ログは行全体に秘密値マスク（V01）。送信中の例外・クラッシュは UNKNOWN へ回復（猶予 10 分、V02）。予算確認と claim は同一トランザクション、SENDING も月枠に計上、更新は自分の claim 条件付き（V03）。UNKNOWN 行は期限・STOP・後続失敗でも UNKNOWN のまま予算保持（V04/V05）。RECONCILE/RISK は登録日の JST 終日で失効（V06）。制御操作はイベント時刻で順序判定し遅着は IGNORED（監査 STOP_LATE/RESUME_LATE）、未来時刻は INVALID（V07/V10 採用）。イベントの形崩れは INVALID（V08）、非 ASCII 署名は 401（V09）、非 ASCII ホストのリンクは拒否（V13）。

## 2026-09-11 Codexによるv0.4.1追補

Codex単独開発方針で、judgesのネスト文字列キーのマスク・マスク後衝突時の値保持、notifyの範囲外timestamp拒否、UNKNOWN再試行の初回予約月保持を修正。Claude実装／独立レビュー済みとは扱わない。基準696件＋追加47件＝743 passed in 18.82s。詳細は ../build-codex/OPS_V041_RESUME.md。実LINE・実LLM未接続。

### 2026-09-11 永続通知queueの限定整合検査

Codex単独で、`Notifier.get_entry`、`flush`、`reconcile_sent`が各操作の対象とする保存済みoutbox行について、`kind`、現settingsのrecipient、標準`json.loads`で読める辞書かつ`type`を持つmessage、既存`content_hash = SHA256([kind, proposal_id, message])`の一致を副作用前に検査するよう補強した。`get_entry`の返却項目は従来どおりで、内部検査用hashは返さない。`flush`と`reconcile_sent`は選択対象全件を先に検査し、破損時にはclaim、alert、予算、queue state、台帳を変更しない。

この追補は既存hash契約を変更しない。key、attempts、state、保存日時はhash対象外であり、今回それらの完全性を保証したものではない。JSONは標準parserによる既存契約で、重複key拒否等のstrict JSON契約を追加していない。明示したkeysの対象外にある破損行はその操作で検査せず、空keysは無動作を維持する。検査後の非協調外部書込みやSQLite fileの外部差替えを完全には排除しない。実LINE接続は無効のままであり、この変更をClaudeの独立レビュー済みとは表記しない。後続の日時・state等の検証範囲調査とは分けた現時点の履歴である。

#### 履歴/日時の追加先行検証（同日・後続到達点）

前節のhash対象は維持したまま、attemptsのlist[dict]と各at、created_at、非NULLのexpires_at/claimed_atをaware ISO日時として事前検証する。get_entryは追加内部列を返却前に除き、公開形状を維持する。これらはhash対象へ組み込んだ意味ではなく、後段で必要な解析を処理前へ移したもの。正常なNULL・UNKNOWN・SENDINGの状態遷移は維持する。

reconcile_sentは全対象noticeを先行照会し、既知の後続欠損を最初の台帳更新前に拒否する。書込時もnoticeを再照会し、同一noticeの重複更新を避ける。外部writerの差替えや途中DB故障までの全件atomicityは保証しない。最終全体実測は主系build-codex/README.mdを参照。

#### 未知stateの事前拒否（同日・後続到達点）

合法な永続stateは既存のPENDING/SENDING/UNKNOWN/SENT/EXPIREDの5種とする。get_entryとflush/reconcileの選択確認で未知stateを拒否し、正常なUNCHANGEDへ補完しない。keys=Noneは全体に未知stateがないか確認し、明示keysはその範囲だけ確認する。空keysは無操作。合法terminal行の本文hashを新たに全件検査する変更ではなく、元の処理対象filterは維持する。状態遷移・schema/hashは変更していない。

#### retry keyと候補期限の保存照合（同日・採用）

保存行の`retry_key`は空でない文字列とし、各attemptの`retry_key`も空でない文字列かつ行の値と一致することを対象行の副作用前に検査する。状態別のattempt件数、resultの列挙、UUID形式は新しい契約としない。

NEW/EXITの候補行は`expires_at=NULL`を拒否し、台帳のProposalから参照した期限とJST正規化後の同一時点であることを照合する。RECONCILE/RISKの非候補行は従来どおり`expires_at=NULL`とする。期限判定は保存値をJSTへ正規化した後の日付から07:15または日次終了時刻を作り、別オフセットの同一時点を誤って前日・翌日扱いしない。

`get_entry`はこれらを参照照合してから従来と同じ公開形状を返し、送信、queue状態更新、台帳反映は行わない。主系はenqueue/deliver/reconcileはclock書込み前に対象全行を`get_entry`で照合する。初回PENDINGの空attempt履歴にはretry keyの過去値がないため、文字列としての有効性以上の過去照合はできない。行の`retry_key`と過去attemptの値を同時に整合するよう外部改変した場合の検出や所有者証明でもない。既存のcontent hash対象、DB schema、state別件数、UUID形式は変更しない。

#### direct queue操作の時刻下限（同日・限定採用）

`Notifier.flush`は選択されたactive行、`reconcile_sent`は選択されたSENT行について、全件の保存内容を検査した後、`now`が`created_at`、`updated_at`、またはいずれかのattemptの`at`より前なら副作用前に拒否する。これにより、後のfailureより前の時刻でretryしてattempt/updated_atを逆行させることや、保存済みSENT時刻より前に台帳へ照合記録を追加することを防ぐ。同時刻と、JSTへ正規化して同一時点となる別timezone表記は許容する。

空keysは無操作、指定外行は非検査である。`flush`の既存active filterと`reconcile_sent`の既存SENT filterは維持し、terminal行全体を新しい本文検査対象へ広げない。DB schema、content hash、state遷移は変更しない。このqueue履歴下限は、通知状態の業務時刻を通知作成時刻以後へ一律制限するLedger規則ではない。Q04との互換性から後者は引き続き不採用である。実LINEは使用せず、試験結果は主系READMEの後続記録を参照する。

#### transaction取消の二次障害（2026-09-11、Codex単独修正）

STOP、RESUME、enqueue、flush claim、Ledger._commitで、割込み系を含めた失敗後にbest-effort ROLLBACKを行い、取消の二次障害で元例外を隠さない。Ledgerはtransactionが残っている場合だけ取消を試みる。検証拒否の監査をCOMMITして業務エラーを返す既存動作は維持し、成功時のメモリ状態更新もCOMMIT後のままである。

送信callback例外をUNKNOWNとして確定する別処理、予算、通知状態、Q04は変えない。取消不能・COMMIT成否不明の自動修復を追加していない。新規の通知9件と台帳8件では、実ROLLBACKを実行した後に二次例外を注入するケースも含め、元例外保持・部分更新なし・接続再利用を確認した。実際に取消自体が不能な環境でも同じ復旧を保証するものではない。全体実測は[主系README](../build-codex/README.md)の最新節を参照。

## 2026-09-11 Claude 第18回：ops v0.4.1 の独立レビュー（Cowork）

ops のコードは未変更。`tests/test_v041_resume_review_claude.py` を 11 件追加し、**収集できた範囲で 306 件中 300 通過 / 6 失敗**。
失敗は環境要因 1 件（`aitrader` 未取得の `test_r20`）と本レビューの新規指摘 5 件（W01 が 4・W02 が 1）。
**指定の全体コマンドは未実行**（`build-codex/aitrader` を取得できず、`aitrader` 依存の 13 ファイルを収集していない）。
Codex 報告の 2189 件は本レビューでは未検証。

- **W01（A）**: 直接 `Notifier.stop()/resume()` は `event_at > now` を検査しないため、未来時刻の制御イベントが
  `_latest_control_event_at` を未来へ進め、以後の本物の STOP が STOP_LATE として無視される。`is_stopped()` は False のままで
  NEW 候補の送信が続く。webhook（`_apply_event`）と `stop_status._time()` はすでに未来時刻を拒否しており、
  書き手と読み手の判断が食い違う（読み手は `STATE_DB_INVALID` → UNKNOWN / `effective_stop=True`）。
- **W02（B）**: 同一候補・別 key・内容変更の `enqueue` が完全重複と同じ応答になり、訂正後の本文が黙って捨てられる。

詳細と再現は `Claude対応結果.md` 第18回。
