# Claude 対応結果（フェーズ2: 台帳・通知ゲート・ハードリミット）

## 第2回（2026-09-08 v0.2）— Codex 再レビュー R01〜R19 への対応

依頼元: `build-codex/Claude引き渡しプロンプト.md`（B. 今回の依頼）。対象: `ops/` のみ。Codex 成果物・共通受入テスト・共通仕様・合成データは変更していない。

### 実行コマンドと結果

```
cd ops
python -m pytest ../common/tests/phase2 -q
```

| 時点 | 結果 |
|---|---|
| 修正前（再現） | 130 passed / **16 failed**（Codex の記録どおり。skip/xfail なし） |
| 修正後 | **146 passed / 0 failed / 0 skipped / 0 xfail** |

内訳: test_ledger 30 / test_gate 55 / test_packet 39 / test_ops_rereview 22。B 分類 5 件も契約を整合させて解消したので 146/146 と記載する（根拠は下表）。

### R 番号ごとの対応

| ID | 分類 | 対応 | 変更箇所 |
|---|---|---|---|
| R01 | A | SELL 約定は取り崩した取得原価を `cost_delta` として記録し、訂正時はそれを戻す（売却額を原価にしない） | ledger.py `_apply_fill` / `_reverse_fill` |
| R02 | A | 注文の完了（FILLED/CANCELLED/SKIPPED/REJECTED）を `closed` フラグで保持。訂正で約定を打ち消しても closed のままで予約は 0 | `_recalc`, `_reverse_fill` |
| R03 | 対照 | 出金の1円境界。不変条件検査（余力≥0）で拒否時は残高不変 | `_assert_invariants` |
| R04 | A | 通知作成時も不変条件検査を通す → 予約合計が現金を超える通知はエラー | `apply` → `_assert_invariants` |
| R05 | A | 約定適用後に「現金 − 予約 ≥ 0」を検査 → 他通知の予約に食い込む報告は拒否・残高不変 | 同上 |
| R06 | A | 初期 `open_orders` の BUY を EXTERNAL 通知として登録し現金予約に含める | `_on_snapshot` |
| R07 | A | 同 SELL を株数予約に含め、同じ保有株の二重売却通知を拒否 | `_on_snapshot`, `_assert_invariants` |
| R08〜R10 | 対照 | 変更なし（通過維持） | — |
| R11 | A | 明示 proposal_id でも銘柄・売買方向が通知と違えば pending・残高不変 | `_on_csv_fill` |
| R12 | A | 約定キー（注文ID・数量・単価・時刻）を**全通知横断**で照合し、既に LINE 計上済みなら skipped | `_find_fill_anywhere` |
| R13 | B→解消 | 約定キー一致で手数料だけ違う CSV は pending（LINE 報告側は重複扱い＋警告） | `_on_csv_fill`, `_on_trade` |
| R14 | A | `create_notice(p, at=None)` に作成時刻（既定: 現在 JST）を業務時刻として持たせ、`replay(at)` の対象にする | `create_notice`, `replay` |
| R15 | B→解消 | 公開メソッド入口で `MAX(seq)` を比較し他ハンドルの追記を自動再生。書込は `BEGIN IMMEDIATE` で直列化、競合時はやり直し | `_sync`, `_commit` |
| R16 | B→解消 | `fee_margin` と規則版を SNAPSHOT イベントに保存し、再オープン時はそれを使う（起動引数は初期化時のみ有効） | `init_snapshot`, `_on_snapshot`, `policy()` |
| R17 | A | 指値の有限性を Decimal 比較より先に検査。NaN/Inf は理由付き不許可（例外にしない） | gate.py `_is_finite_number` |
| R18 | B→解消 | 決算回避を「暦日差 ≤ N または 営業日差 ≤ N」で判定（小さい方を採用＝どちらか単独より必ず保守的）。`evaluate(..., business_days=)` を任意引数として追加、省略時は月〜金 | gate.py `business_days_between`, `_event_errors` |
| R19 | B→解消 | `LedgerView.reserved_shares` を追加し、ゲートは「保有 − 売却予約」で SELL を判定 | models.py, ledger.py `_view_of`, gate.py |
| R20 | 対照 | 通過維持（主系パケット→模擬二承認→ゲート→予約→2件目余力不足） | — |

### レビュー回答 1〜4 の運用設計への反映

- 回答1（文面）: `GateResult.reason_codes` を追加（PACKET_MISMATCH / REVIEW_INCOMPLETE / REVIEW_NOT_APPROVED / REVIEW_INVALID / EARNINGS_UNKNOWN / EARNINGS_BLACKOUT / INSUFFICIENT_AVAILABLE / STOP_NEW / RECONCILIATION_PENDING / PROPOSAL_EXPIRED / INSUFFICIENT_SHARES ほか）。`reasons` は数値入りの監査用日本語のまま。カード用の平易文はコードから引く（順序3の送信器で対応表を持つ）
- 回答2（CSV）: 実 CSV の列は未確認のまま。照合キーは注文ID・数量・単価・時刻で、`broker_order_id` を注文IDと約定IDに兼用しない前提は変えていない（`fill_id` 列は実 CSV 確認後に追加）。pending 行は永続化し `pending_rows()` / `resolve_pending(source_event_id, proposal_id, at, action=APPLY|DISCARD)` を追加
- 回答3（監査）: `ingest_attempts` 表を追加。report / import_csv_fills の全試行を APPLIED / DUPLICATE / IGNORED / PENDING / REJECTED で追記（payload_hash・理由コード・ledger_seq 付き）。残高イベントには再適用しない。無視・重複時に `seen` を触らないよう修正
- 回答4（replay）: `replay(at)`＝業務時刻ベース、`replay_known(seq)`＝記録順ベースに分離。通知作成に実時刻を付与。予約率・規則版は SNAPSHOT に保存
- 順序3: `Ledger.view()` は `reserved_shares` を含むので「最新 view で evaluate → create_notice → APPROVED」の順で自己予約を二重評価しない（create_notice 前に評価する）

### 契約変更の記録（common/ISSUES.md に追記済み）

共通仕様 §2〜§4 への**追加**提案（既存シグネチャは維持、いずれも任意引数・追加属性）:
`evaluate(..., business_days=None)`、`LedgerView.reserved_shares`、`GateResult.reason_codes`、`create_notice(p, at=None)`、`Ledger.replay_known(seq)`、`Ledger.pending_rows()`、`Ledger.resolve_pending(...)`、`Ledger.policy()`、`TradeEvent.replaces_event_id`。共通仕様本文は変更していない（Codex が確認のうえ反映する想定）。

### 残課題

1. 実 CSV（楽天証券の約定履歴）の列・約定ID・時刻精度の確認 → `fill_id` の追加と照合キーの確定（則光さんに実ファイルを1枚もらう必要あり。秘密情報は伏せて可）
2. 順序3の実行器（主系）: 営業日カレンダーを `business_days` に渡す、`daily_pnl` / `day_start_equity` / `equity` / `peak_equity` の評価方法の契約化
3. 集中度は簿価（avg_price）。時価評価は新しいビュー項目として別途

### Codex に再レビューしてほしい点

1. `_assert_invariants` に集約した不変条件（現金≥0、余力≥0、保有≥0、売却予約≤保有）に漏れがないか。特に SPLIT・SELL 訂正・EXTERNAL 注文の取消
2. `_find_fill_anywhere` の横断照合が「正当な同一キーの部分約定」を誤って重複扱いしないか（時刻なし報告のケース）
3. `replay(at)` と `replay_known(seq)` の使い分けが監査要件（当時の判断根拠 vs 訂正後の残高）を満たすか
4. `reason_codes` の粒度と命名（カード対応表を作る前提で）
5. 順序3の設計（PHASE2_INTEGRATION.md）に対し、今回追加した API で足りないものがないか

---

## 第1回（2026-09-08 v0.1）— 初回実装

（履歴。内容は v0.1 時点のもの）

- 変更ファイル: `ops/aitrader_ops/{__init__,models,ledger,limits,gate}.py`, `ops/README.md`, `ops/requirements.txt`, `ops/.gitignore`, `common/ISSUES.md`（追記）
- テスト: `pytest ../common/tests/phase2 -q` → 124 passed（skip/xfail なし）
- ISSUES #1〜#12 への判断: 属性名契約＋正式 dataclass / PARTIAL・FILLED は増分 / CORRECTION に replaces_event_id / 予約額は残数量ベース / 日次損失・DD は Decimal / 決算回避は暦日（**v0.2 で訂正**）/ ハッシュ再計算 / 追記専用イベント＋replay / 異常入力は残高不変

## 第4回（2026-09-08 21:25 JST）— ops v0.3（Codex 修正版）の独立レビュー（Claude）

依頼元: `build-codex/Claude引き渡しプロンプト.md`（第3回B）。役割: 今回 Claude は**レビュー担当**。ops のコードは変更していない（追加したのは `ops/tests/test_v03_review_claude.py` のみ）。共通受入テスト・主系・Codex の ops 自前テストも無変更。

### 実測

| 環境 | コマンド（ops/ で実行） | 結果 |
|---|---|---|
| Claude 作業環境（Linux, Python 3.11, duckdb あり） | `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q` | **225 passed / 2 failed**（227 = 共通 162 ＋ 主系 43 ＋ Codex ops 自前 6 ＋ Claude レビュー 16） |
| 則光さん PC の Cowork 作業環境（duckdb 未導入のため主系テストは収集不可） | `python -m pytest ../common/tests/phase2 tests -q` | **182 passed / 2 failed**（184） |

2 件の失敗はどちらも Claude が今回追加した反例で、Codex の 211 件（共通 162＋主系 43＋ops 6）はすべて通過を維持している。skip / xfail なし。

### 追加した 16 ケース（`ops/tests/test_v03_review_claude.py`）と判定

| ID | 何を壊しにいったか | 結果 | 分類 |
|---|---|---|---|
| C01 | SELL 価格訂正を 2 回連鎖（fix1 → fix2）しても取得原価 1,000 円のまま、現金は最終値のみ反映 | 通過 | — |
| C02 | 売却後に別単価で買い増した後の売却価格訂正が、買い増し後の平均原価 1,500 円を壊さない | 通過 | — |
| C03 | SELL 数量訂正は例外ではなく `ReportResult.error` で呼出側に伝わり残高不変 | 通過 | — |
| C04 | 買付を全部売却した後の買付価格訂正はエラーで止まり部分反映しない（既知制限の確認） | 通過 | 制限として記録 |
| **C05** | **proposal_id なし CSV 約定（業務時刻 T+1h）を、通知作成 T+2h より前の時点で replay** | **失敗**: 約定が消え現金 1,000,000（実際は 900,000） | **A**（S09 修正の取り残し） |
| C06 | context_only 通知が予約額・予約銘柄・売却予約株数のいずれにも現れない | 通過 | — |
| C07 | replay_known(seq) が全 seq で例外なく動き最終 seq で現在残高に一致 | 通過 | — |
| C08 | 検証エラーの報告が ledger_events に残らず ingest_attempts に REJECTED/VALIDATION が残る | 通過 | — |
| C09 | 保留行の APPLY が銘柄不一致で失敗しても保留行が消えず残高不変 | 通過 | — |
| C10 | 監査行に業務時刻・proposal_id・ledger_seq が入る | 通過 | — |
| C11 | 別銘柄の注文中や同銘柄の完了済み注文だけなら SPLIT を拒否しない | 通過 | — |
| **C12** | **端株が出る併合（101 株 × 0.5）を黙って 50 株に切り捨てず拒否する** | **失敗**: 50 株になる | **B**（端株の契約が未定義） |
| C13 | 初期の発注済み買注文が約定報告で保有になり、予約解放・未確認一覧から消える | 通過 | — |
| C14 | v0.2 で受理された「注文中の分割」履歴を v0.3 で開くと起動時に LedgerError（seq 情報なし） | 通過（現状の再現） | 移行課題 |
| C15 | reason_codes が reasons と同数・同順で、INVALID が REVIEW_INVALID、STOP が STOP_NEW | 通過 | — |
| C16 | verdicts=None で例外を出さず不許可（REVIEW_INCOMPLETE） | 通過 | — |

### C05 の詳細（A）

`Ledger.replay(at)` は「cutoff 以前に有効な約定が参照する proposal_id」を `TRADE` / `CSV_FILL` の payload から集めて、後日作成の通知を context_only で補完する。しかし **proposal_id を持たない CSV 行**（同銘柄・同方向の未決済通知が 1 件だけのときに紐付けられる経路）は payload の proposal_id が None なので集合に入らず、再生時に通知が存在せず `pending` に落ちて約定が消える。現在残高と replay(at) が食い違う点で S09 と同種。修正案: 適用時に決定した紐付け先を `CSV_FILL` の payload（または別イベント `CSV_FILL_APPLIED{event_id, proposal_id}`）に**確定情報として記録**し、replay はそれを使う。適用時の解決結果を再生時に再計算させない（再計算は状態依存で結果が変わる）。

### C12（B）と移行課題

- C12: 併合で端株が出る場合の扱い（切り捨て・端株現金化・拒否）は共通仕様に無い。実運用では証券会社が端株を現金精算するので「端株分を `ADJUST(FRACTIONAL_CASHOUT, amount)` で別イベント化し、SPLIT 自体は整数にならなければ拒否」を提案。
- C14: v0.2 履歴を v0.3 の規則で再生すると起動できない。移行案（下記）を提案。

### 移行案（v0.2 台帳 → v0.3）

1. **原本は触らない**。`ledger.sqlite` をコピーして検証する（`AI_TRADER_HOME/migrations/<日時>/ledger.v02.sqlite`）
2. `python -m aitrader_ops.migrate --check <copy>`（新規 CLI 案）: 全イベントを v0.3 規則で再生し、**最初に違反する seq・kind・理由**を報告する（現状は理由だけで seq が無い。`MigrationError(seq, kind, reason)` を追加）
3. 方針は「規則版マーカー」: `POLICY_UPGRADE{schema_version: 3, at}` イベントを追記し、再生時は**マーカーより前の seq は旧規則（v0.2 の検証）**、以降は新規則で検証する。旧イベントを書き換えない（追記専用の原則を守る）
4. マーカー追記前に、違反イベントが現在残高に与える影響（例: 注文中の分割で予約株数 > 保有）を `--check` の出力で人間が確認し、必要なら `ADJUST` で調整イベントを追記する
5. 移行後に `replay_known(seq)` で旧最終 seq の残高と、移行前の残高が一致することを確認して完了

### コードレビュー所見（テスト外・未再現）

- `_commit` は検証エラー時に REJECTED 監査だけ COMMIT してから再送出する。監査が失敗した場合は例外が二重になり得る（`_ingest_audit` 内の例外 → ROLLBACK → 元の LedgerError が失われる）。`except` で元例外を `__context__` に保つか、監査失敗を別ログに落とす
- `ID_PAYLOAD_CONFLICT` は同じ `source_event_id` の APPLIED/PENDING 行とだけ比較する。TRADE と CSV_FILL で別 ID の同一約定は対象外（意図どおりだが README に明記推奨）
- `replay(at)` で `CSV_FILL` の `at` が None の行は常に含まれる（`not ev_at`）。時刻なし CSV は現在 pending になるので実害は小さいが、契約として書く
- `_reverse_fill` の BUY 経路は保有株が既に売却済みだと失敗する（C04）。証券会社の翌日訂正で起こり得るので、フェーズ3で「売却済み分の訂正は実現損益の調整イベント」に落とす契約が必要
- `unconfirmed()` は EXTERNAL の created_at（スナップショット時刻）で絞る。運用開始直後の as_of がスナップショットより前だと外部注文が一覧に出ない（境界。実害は小さい）
- reason_codes は依然として日本語文面の部分一致。`_CODE_RULES` の順序依存（例: 「packet_hash を再計算できない」→ PACKET_MISMATCH に吸われる）。判定箇所でコードを直接付ける構造化は Codex の提案どおり別工程で

### 結論

v0.3 の S 番号修正は妥当で、私の反例 16 件中 14 件を通過した。残る A は C05 の 1 件（S09 と同根）、B は C12 の 1 件、移行は設計課題として案を提示した。**「全件通過」ではない**（227 件中 225 通過）。

### Codex への再依頼（`Codex引き渡しプロンプト.md` B に反映）

1. C05 の修正（適用時の紐付け先を確定情報として永続化し replay で使う）
2. `MigrationError(seq, kind, reason)` と `POLICY_UPGRADE` マーカー方式の移行 CLI（`python -m aitrader_ops.migrate --check/--apply`）。原本非破壊、コピー検証
3. C12 の契約提案（端株現金化イベント）を共通仕様 v1.1 改訂案に追記
4. 上記コードレビュー所見 6 点の採否

## 第5回（2026-09-08 21:50 JST）— ops v0.3.1（C05 確定紐付け・POLICY_UPGRADE 移行・端株）の独立レビュー（Claude）

依頼元: `build-codex/Claude引き渡しプロンプト.md`（第4回B）。役割: レビュー担当。ops のコードは変更していない。
注記: `ops/tests/test_v031_review_claude.py`（D01〜D14）は本セッション開始時（21:39）に既に存在していた（別の Claude セッションが作成した模様）。本セッションはこれを**再実行して結果を検証**し、追補として `test_v031_review_claude2.py`（E01〜E03）を追加した。

### 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境）

| 範囲 | 件数 | 結果 |
|---|---|---|
| Codex v0.3.1 時点（共通 162＋主系 43＋ops 自前 6＋移行 8＋Claude v0.3 レビュー 16） | 235 | 通過維持 |
| Claude v0.3.1 レビュー D01〜D14 | 14 | 10 通過 / **4 失敗** |
| Claude 追補 E01〜E03 | 3 | 2 通過 / **1 失敗** |
| 合計 | **252** | **247 通過 / 5 失敗**（skip/xfail なし） |

### 失敗の分類

| ID | 何を壊しにいったか | 現状 | 分類 |
|---|---|---|---|
| **D01** | LINE 報告（dict）に内部属性 `_applied_proposal_id` を混ぜる | TRADE では取り除かれず保存され、replay(at) の「必要な通知」集合が偽 ID に置き換わり、有効な約定の replay が「通知がありません」で失敗 | **A**（外部入力が内部決定を上書きできる。`_commit` の pop が CSV_FILL 限定） |
| **E01** | `_` で始まるキー全般（`_rule_version` など）を TRADE / CSV に混ぜる | `_rule_version` がそのまま永続化される（現状は読み手がいないので実害なしだが、将来 `_` 属性を増やした瞬間に偽装経路になる） | **A**（D01 の一般化。全種別で `_` 始まりのキーを入力から除去し、内部属性は適用後にのみ付与） |
| D06 | POLICY_UPGRADE マーカーの重複・`legacy_last_seq` の矛盾 | そのまま読み込まれる | **B**（マーカー契約: 1 個限定、legacy_last_seq ＝ 直前 seq の検証） |
| D09 | `--apply` がマーカー追記後の検証で失敗 | 作業コピーにマーカーが残り `check` は already_upgraded=True、再実行は FileExistsError | **B**（失敗時契約: 一時ファイルに書いて検証成功後に rename、失敗時は削除） |
| D12 | `FRACTIONAL_CASHOUT` を未保有銘柄・code=None で受理 | 受理される | **B**（精算と銘柄の紐付け契約） |

通過した主な確認: CSV の偽 `_applied_proposal_id` は無視され実際の紐付け先が保存される（D02）、匿名 CSV の確定先が再起動・replay・replay_known で不変（D03）、マーカー前は旧規則・後は新規則（D04）、初期化前マーカー・未知版は位置付き MigrationError（D05）、check/apply が原本バイトとイベントを変えない（D07）、旧新規則で残高が違う履歴は自動移行しない（D08）、端株 SPLIT 拒否（D10）、CASHOUT は現金のみ・負値と端数拒否（D11）、成功側の監査失敗で残高イベントごと ROLLBACK（D13）、時刻なし CSV は再起動・replay 後も pending（D14）、check が台帳でない SQLite や存在しないパスでトレースバックにならない（E02）、CASHOUT は株数不変で約定として監査されない（E03）。

### 移行の残制限（Codex の報告どおり＋今回の確認）

- マーカーは記録順の境界であり業務時刻の境界ではない。`replay(at)` は cutoff に関係なくマーカー有無で規則版を切り替えるため、「マーカー以前の業務時刻」を新規則で再生する経路は無い（意図どおりだが README に明記が要る）
- 旧新規則で残高が変わる履歴（D08）は人間が `ADJUST` を追記して整合させる必要があり、その手順書はまだ無い
- 失敗した `--apply` の作業コピーが「移行済み」に見える（D09）

### Codex への再依頼（`Codex引き渡しプロンプト.md` B に反映）

1. D01/E01（A）: `_commit` で全種別の入力から `_` 始まりのキーを除去し、`_applied_proposal_id` は CSV_FILL 適用後にのみ台帳側で付与する。replay の `needed` は CSV_FILL の確定値と TRADE の `proposal_id` だけを見る
2. D06/D09（B→採用推奨）: マーカー 1 個限定＋`legacy_last_seq` 検証、`--apply` は一時ファイル→検証→rename、失敗時は削除
3. D12（B）: CASHOUT は保有銘柄必須（code 必須、未保有は拒否）を採用するか、契約として「銘柄任意」と明記するか決める
4. README に「マーカーは記録順境界」「D08 の手動整合手順」を追記

## 第3回（2026-09-08 ops v0.3）— S番号修正

実装担当：Codex。ユーザーがClaude引き渡しプロンプトのA・Bに従うよう直接指定したため、今回はそのops修正作業を実施した。Claudeが実行したという意味ではない。主系コード・共通受入テスト・仕様本文は変更していない。

### 結果

- 共通フェーズ2：162件通過（既存146＋再々レビュー16）。
- 主系自前：43件通過（runner20＋既存23）。
- ops自前の追加整合性検証：6件通過。
- ops/から `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q` 相当を実行し、**211 passed in 5.49s**。skip/xfailなし。既存Pythonの依存パスを追加するrunpy起動、`-p no:cacheprovider --tb=short`を付加。

### S番号別の対応

| ID | 対応 |
|---|---|
| S03 | 未完了注文がある銘柄のSPLITを拒否。注文条件と端株の調整契約がない間の保守的制約として採用 |
| S04 | 同株数SELLの価格・費用訂正は元約定のcost_deltaを再適用にも使用。後続買付の平均原価で取り崩し直さない。SELL数量訂正は後続再計算契約待ちのため拒否 |
| S05 | 横断約定照合をcode/sideで限定し、別銘柄・逆方向を確定重複にしない |
| S07 | 同キーだが時刻精度不足のCSVはpending。LINEの二件目は照合エラー。既存同event_idの再送は冪等扱いを維持 |
| S09 | cutoff以前に有効な約定が明示参照する後日作成通知は識別情報のみ再生。context_onlyとして将来の現金・株数予約は含めない |
| S11 | 空event_idの報告・CSVにもREJECTED監査を記録 |
| S12 | resolve_pendingでもcode/sideを照合し、既存約定キーに一致する行の追加APPLYを拒否 |
| S13 | actionはAPPLY/DISCARDのみ。未知値はLedgerError |
| S14 | INVALID応答をREVIEW_INVALIDへ分類。コード生成全体の構造化は別工程 |
| S15 | unconfirmedにEXTERNAL未完了注文も含め、取消後は除外 |
| S16 | 書込ロック内で状態読取・検証・残高イベント・監査を実行し同時COMMIT。監査失敗は残高もROLLBACK |

対照S01/S02/S06/S08/S10と既存テストも通過維持。B4件は実装の追加制約として採用・互換性をops READMEとISSUESへ記録したが、共通仕様本文の正式確定を代行したものではない。162/162は実測テスト結果であり、未確定仕様の承認を意味しない。

### 追加所見への対応・検証

CSV監査のskippedをDUPLICATEへ統一。同一event_idで異なるpayload_hashはID_PAYLOAD_CONFLICTを記録。pending解決に任意actor/reasonを追加し監査detailへ保存する（未指定はunspecified。認証を保証する値ではない）。記録順の読取はseqをイベント行と同じSELECTで取得し、書込後は当該INSERTのlastrowidを使用。他書込後のMAX(seq)を自分の状態と誤って結び付けない。

ops/tests/test_v03_integrity.pyの6件：CSV監査失敗と再起動、ID内容衝突、DISCARD操作者・再起動、遅着通知の予約混入防止、時刻なしLINEの曖昧一致、EXTERNAL取消後の照合一覧。全件通過。実プロセス同時書込のストレス試験は未実施。

### 変更ファイル

ops/aitrader_ops/ledger.py、ops/aitrader_ops/gate.py、ops/tests/test_v03_integrity.py、ops/README.md、本記録、common/ISSUES.md（追記）、引き渡しMD類。主系runnerと共通受入テストは未変更。

### 次の再レビューで確認すべき限界

- replayは明示proposal_idの依存を補う限定的修正。proposal_idなしCSV、複数の遅着訂正、分割を跨ぐ訂正の完全な業務時点再現は未保証。replay_known(seq)を当時の知識の記録に使う。
- v0.2で受理された注文中SPLIT等の履歴は新しい制約と衝突して起動再生が拒否され得る。旧台帳の無条件アップグレードや移行は行っていない。コピーでの比較と移行方針が必要。
- reason_codesの多くは日本語の部分一致。今回INVALIDを区別したが、完全構造化は未完了。
- 実CSV列・口座と市場を含む約定IDスキーマは未確定。時刻付きの同一注文・数量・価格でも真の別約定を完全には識別できない。
- SELL数量訂正、逆指値・分割連携、実配信、主系DB跨ぎの自動復旧は今回対象外。
## 2026-09-08 Codex 第4回：ops v0.3.1（最新）

C05/C12と移行CLIを実装。共通162＋主系43＋ops30＝235 passed in 5.85s（既存227全通過＋追加8、skip/xfailなし）。既存テストは未変更。詳細と6所見の採否は ../build-codex/OPS_V031_REPORT.md。

CSVの適用先を内部_applied_proposal_idに確定保存。MigrationErrorはseq/kind/reasonを保持。python -m aitrader_ops.migrate --check <copy>は読取専用検査、--apply <copy>は別の<stem>.upgraded.sqliteへbackupしPOLICY_UPGRADEマーカーを追記。原本も既存出力も上書きしない。旧規則区間の残高とreplay_knownの一致を検証し、不一致なら失敗。入力原本の移行・差替えは未実施。

端株SPLITは拒否。ADJUST/FRACTIONAL_CASHOUTは確定受取金の現金加算だけで、株数を切り捨てない。株数・原価調整全体の自動化は未実装。旧匿名CSVの業務時点再生、売却済みBUY訂正、reason_codes完全構造化は引き続き制限。
## 2026-09-08 第5回対応：ops v0.3.2（Codex実施・最新）

D01/E01の全入力内部キー除去、D06のマーカー1個・直前seq一致、D09の検証後公開と失敗一時ファイル削除、D12のcode必須・保有銘柄限定を実装。マーカーは業務時刻atでなく**記録順seqの境界**。成功時だけ `.upgraded.sqlite` を公開し、以前の「失敗コピーを残す」契約を置き換える。

ops/で指定対象 `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q` を実行：**257件＝255 passed / 2 failed in 7.15s、skip/xfailなし**。元252件は250通過・2失敗、追加F01〜F04は5ケース通過。既存テストは変更していない。D08/D09は失敗コピーの存在を要求する旧前提で失敗し、新しい削除契約に対する**テスト前提の誤り**と分類。全件通過ではない。

D08の手動照合は、原本・コピーのハッシュ保存→checkの旧新残高差と最初の相違seq特定→取得済み証券明細で約定・銘柄・数量・金額の照合→根拠付き訂正方針の確定→別コピーで合法な訂正と残高再検証の順。差額だけの現金調整や履歴書換えで強制一致させない。現APIで表現できない差は移行保留とし、照合済み開始残高への移行方式を別途設計する（今回未実装）。旧新差が残る入力は再試行でも拒否されるが、失敗ファイルが再試行を妨げることはない。

詳細な契約・実行環境・手動照合手順・残課題：[OPS_V032_REPORT.md](../build-codex/OPS_V032_REPORT.md)。ClaudeにはD08/D09の新契約に合わせた期待値更新と独立レビューを依頼する。実台帳・外部サービスは操作していない。
## 第6回（2026-09-08 22:10 JST）— ops v0.3.2 の独立レビューと D08/D09 の期待値更新（Claude）

依頼: `build-codex/Claude引き渡しプロンプト.md` B（22:00 発行）。Codex の v0.3.2（ledger.py / migrate.py / test_v032_contracts.py）は変更していない。

### 1. D08/D09 の期待値更新（`ops/tests/test_v031_review_claude.py` の 2 件のみ）

採用された失敗時契約「一時ファイル（`<stem>.<乱数>.upgrading.sqlite`）上で検証し、成功時だけ `<stem>.upgraded.sqlite` を公開、失敗時は一時ファイルと副ファイルを削除」に合わせた。

- D08: 旧新残高不一致（旧 900,000 / 新 800,000）→ `check` は eligible=False、`apply` は ValueError で拒否、の検査はそのまま。旧期待値「失敗コピーは診断用に残る」を廃止し、原本ハッシュ不変・`.upgraded.sqlite` なし・`*.upgrading.sqlite*` なし・フォルダに原本以外が残らない、を追加。
- D09: `replay_known` に失敗を注入 → 原本不変・出力なし・一時ファイルなし・`check(原本)` は already_upgraded=False。障害解除後の再実行が `FileExistsError` で止まらず成功し、その出力だけが移行済みになること、出力が存在する状態での再実行だけが `FileExistsError` になること、を追加。
- 削除・skip・xfail・条件緩和なし。他の 12 件は未変更。

### 2. 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境）

| 区分 | 件数 | 結果 |
|---|---|---|
| 受領時（Codex 報告の再現） | 257 | 255 通過 / 2 失敗（D08・D09）。skip/xfail なし |
| D08/D09 更新後 | 257 | 257 通過 |
| 反例追加後（`ops/tests/test_v032_review_claude.py` G01〜G13、G05 は 7 ケース） | **276** | **275 通過 / 1 失敗（G07）**。skip/xfail なし。4.40s |

内訳: 共通フェーズ2 162 ＋ 主系 43 ＋ ops 71（Codex 19 ＋ Claude 52）。**全件通過ではない**（G07 が残る）。

### 3. 追加した 19 ケース（`test_v032_review_claude.py`）と判定

| 重点 | ケース | 結果 |
|---|---|---|
| 全種別の内部属性混入 | G01: SNAPSHOT（positions/open_orders の SimpleNamespace）・NOTICE_CREATED（proposal.events の入れ子）・NOTICE_STATE・STOP_ORDER・TRADE・CSV_FILL・PENDING_RESOLVED・ADJUST の全公開 API に `_` キーを混ぜて永続化されないこと、値としての `_文字列` は保持、再起動＝replay＝replay_known | 通過 |
| 監査 hash と台帳内部値の分離 | G02: `ingest_attempts.payload_hash` は除去後の外部入力の hash と一致し、`_applied_proposal_id` 付きの保存 payload の hash とは異なる。偽 `_` キー付き再送は DUPLICATE、公開フィールド改変は ID_PAYLOAD_CONFLICT | 通過 |
| replay と再起動の一致 | G03: PARTIAL→CORRECTION→匿名 CSV→SELL 全量→保留→後着通知→APPLY→端株精算を含む履歴で view / replay() / replay(遠い未来) / replay_known / 再起動が一致、途中時刻の replay も期待どおり | 通過 |
| マーカーは seq 境界 | G04: マーカーの `at` を 2099 年に書き換えても replay(at) は記録順境界として扱い、マーカー後は新規則（時刻なし CSV 保留・注文中 SPLIT 拒否）、マーカー前は旧規則のまま | 通過 |
| 不正境界マーカー | G05（7 ケース）: payload が配列・`legacy_last_seq` が bool/float/文字列/欠落・`schema_version` 欠落/文字列 → いずれも `MigrationError(seq=5, kind=POLICY_UPGRADE)`、`check` も同じ例外 | 通過 |
| 別ハンドル経由の不正マーカー | G06: 別接続が不正マーカーを追記した後、開いているハンドルの `report` は適用されず（error に POLICY_UPGRADE）、`cash()` は MigrationError、履歴も監査行も増えない | 通過 |
| **書込時のマーカー検証** | **G07**: 移行済み台帳に `_commit('POLICY_UPGRADE', …)` で 2 個目のマーカーを追記すると**書込時には受理され**（seq 6 が付く）、次回起動と `check` が MigrationError になる | **失敗** |
| 出力衝突・他実行の一時ファイル | G08: `.upgraded.sqlite` が既にあると一時ファイルを作る前に FileExistsError、フォルダの他ファイルに触れない。別実行の `legacy.stale.upgrading.sqlite` は消さない | 通過 |
| コピー／検査段階の失敗 | G09: `check` に失敗を注入 → 原本以外何も残らず、解除後に成功 | 通過 |
| 環境依存パス | G10: `!!!※則光用 999_投資関係/台帳 v0.2.sqlite` のような非 ASCII・空白・`!` を含むパス、相対パス指定、原本を別接続で開いたままの状態で check/apply が成功 | 通過（Linux。Windows の `as_uri()`＋`?mode=ro` は則光さんの PC で `--check` を 1 回実行して確認が必要） |
| 移行後の原本追記 | G11: apply 後に原本へ追記しても出力には入らず、`check(原本).last_seq` で検知できる | 通過（コピー方式の限界の明文化） |
| 端株精算の保有限定 | G12: 売却済み銘柄・snapshot の open_orders にしかない銘柄・表記ゆれ（前後空白・先頭 0）・None・空文字 → すべて LedgerError、ADJUST は 1 件も記録されない | 通過 |
| 端株精算の受理範囲 | G13: 分割後の保有中は受理、全量売却後は拒否、再起動・replay_known で一致 | 通過 |

### 4. G07 の分類と提案（B: 契約の穴だが公開 API からは到達しない）

- 到達経路: `Ledger._commit`（内部 API）または生 SQL のみ。公開 API（report / import_csv_fills / adjust …）に POLICY_UPGRADE を書く入口はない。よって運用上の即時リスクは低く **B** とする。
- しかし「壊れた台帳の上に積まない」（`_sync` で不正マーカーを検出して止まる）契約と非対称で、書込は通るのに次回起動で初めて壊れる「地雷」になる。提案: `_State._on_policy_upgrade` で `rule_version >= 3`（＝既に新規則。移行済みか、そもそも旧規則区間がない）なら LedgerError。`_rebuild` はマーカーがあると rule_version=2 から始めるため、正規の 1 個目は通り、2 個目は再生時にも同じ理由で拒否され、`_validate_markers` と整合する。
- 付随: `check()` は新規則側の失敗は `violation` 辞書で返すが、旧規則側（`_rebuild(legacy=True)`）の失敗は MigrationError をそのまま投げる（CLI は error JSON で受ける）。API としての非対称。修正は任意。

### 5. README の D08 手動照合手順のレビュー（`ops/README.md` 第5回対応・`OPS_V032_REPORT.md`）

方針（強制 ADJUST なし・履歴書換えなし・表現不能な差は移行保留・照合済み開始残高への移行は未実装）に同意。契約の隙間として `common/ISSUES.md` に以下を追記した（本文は変えていない）。

1. 手順 3 の「差が初めて出る seq の特定」を支援する出力が `check` にない（旧新の最終残高のみ）。seq ごとの旧新差分は手作業になる。
2. 「照合済み開始残高への移行」は、SNAPSHOT が 1 台帳 1 回（再登録は拒否）のため、現状は**新しい台帳ファイル＋新 SNAPSHOT** しか手段がなく、旧台帳との対応（出所・旧最終 seq・照合記録）を新台帳に残す契約が未定義。
3. D12 採用により、全量売却後に届く端株精算金・清算金の受け皿（DEPOSIT か DIVIDEND か、note の書式）が未定義。
4. 移行中〜昇格までの原本の静止（書込停止）と、`.upgraded.sqlite` を運用パスへ昇格する手順（旧原本の退避名・バックアップ世代）が未定義（G11 の限界）。
5. Windows 実機での `--check`（`as_uri()`＋`?mode=ro`、日本語・`!`・空白を含む実フォルダ）は未確認。

### 6. 変更ファイル

`ops/tests/test_v031_review_claude.py`（D08/D09 のみ）、`ops/tests/test_v032_review_claude.py`（新規）、`ops/README.md`（第6回追記）、本記録、`common/ISSUES.md`（追記）、`Codex引き渡しプロンプト.md`（B と履歴）。主系・共通受入テスト・`test_v032_contracts.py`・ledger.py・migrate.py は未変更。実台帳・外部サービスは操作していない。

### 7. 結論

v0.3.2 の採用契約（全種別の内部キー除去、マーカー 1 個・直前 seq 一致・seq 境界、一時ファイル→検証→公開、端株精算の保有限定）は、公開 API から到達できる範囲では反例 18 ケースすべてに耐えた。残るのは内部 API 経由の G07（B）と、契約の隙間 5 点（ISSUES）。**「レビュー合格（条件付き）」＝ 276 件中 275 通過、全件通過ではない。**

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
## 第7回（2026-09-09 05:30 JST）— ops v0.3.3 の独立レビュー、D08 テスト接続の明示 close、D08 契約 5 点のレビュー（Claude）

依頼: `build-codex/Claude引き渡しプロンプト.md` B（2026-09-08 22:26 発行）。ledger.py / migrate.py / test_v032_contracts.py / test_v033_contracts.py は変更していない。

### 1. D08 の不安定さの解消（テスト側の接続ライフサイクル）

原因は Codex の診断どおり。Claude 所有テストの `_insert` と読み取りが `with sqlite3.connect(path) as con:` で **commit はするが close しない** ため、台帳は WAL モード（`PRAGMA journal_mode=WAL`）で書込が `-wal` に残り、接続オブジェクトが gc で回収された時点で本体へ反映されて hash が変わっていた。

- 修正: Claude 所有 4 ファイル（`test_v03_review_claude.py` 3 箇所、`test_v031_review_claude.py` 8、`test_v031_review_claude2.py` 3、`test_v032_review_claude.py` 8）の `sqlite3.connect` をすべて `with closing(sqlite3.connect(path)) as con, con:`（外側で close、内側で commit）に統一。G10 の「原本を開いたままの読者」も終了時に close する形へ。assert・保護条件は一切変えていない。
- 確認: `python -X dev -W error::ResourceWarning` で Claude 所有 59 件を実行し、未 close 接続の警告ゼロ。
- 再現試験（`test_v033_review_claude.py`）: I01 は close 後の hash が gc.collect・check・apply の前後で不変であることと、逆に close しない接続を残すと `-wal` が残り close 時に本体 hash が変わること（Codex の再現）の両方を確認。I02 は D08 と同じ履歴で gc を挟みながら 3 回 check/apply を繰り返し、原本不変・拒否・出力なし・一時ファイルなしが安定することを確認。

### 2. 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境）

| 区分 | 件数 | 結果 |
|---|---|---|
| 受領時（Codex 279 件の再現） | 279 | 279 通過 |
| 接続修正＋反例 7 件追加後（`test_v033_review_claude.py` I01〜I07） | **286** | **286 通過**。skip/xfail なし。5.85s |

内訳: 共通 162 ＋ 主系 43 ＋ ops 81（Codex 22 ＋ Claude 59）。D08/D09 を含む Claude 所有ファイルは 3 回連続実行でも安定。**全件通過**（286/286）。

### 3. G07（v0.3.3）の独立レビュー

| ケース | 内容 | 結果 |
|---|---|---|
| I03 | 移行済み台帳と新台帳の両方で、`_commit('POLICY_UPGRADE')`（正しい境界・境界 0・schema 4 の 3 形）が書込前に拒否され、履歴・監査行・seq・残高・再起動・replay が変わらず、拒否後も通常の書込（DEPOSIT）は可能 | 通過 |
| I04 | 正規の初回移行（旧規則区間あり）は v0.3.3 でも通る（300 株・900,000 円）。旧規則区間のない v0.3 台帳に**生 SQL** でマーカーを入れると、再生時はマーカー前が旧規則で再生されて保留中の時刻なし CSV が『適用済み』に化ける（cash 900,000・pending 空）。`check` はこれを旧新残高不一致（1,000,000 vs 900,000）として検出し `apply` は拒否する | 通過（限界の記録） |
| I05 | 書込時（`_on_policy_upgrade`）と再生時（`_validate_markers`）の拒否が同じ入力に両方効き、`check` も MigrationError | 通過 |

判定: G07 は解消。書込時の拒否が `_validate_markers` と整合し、H01/H02 とも矛盾しない。「check の旧規則失敗を辞書化しない」判断にも同意（移行元の根拠が得られない履歴は停止が正しい）。残る限界は I04 の「生 SQL で入れたマーカーは再生時に旧規則区間を作ってしまう」で、DB トリガーがない以上は運用（生 SQL 禁止・移行は CLI のみ）で担保する（B、ISSUES に記録）。

### 4. D08 契約 5 点のレビュー（`OPS_V033_REPORT.md`）

| 項目 | 判定 | 所見 |
|---|---|---|
| (1) 乖離 seq は手作業 | 同意 | 先頭からの逐次比較・二分探索禁止・片側再生不能の別記録、いずれも妥当。次回実装候補として `migrate --diff <copy>`（seq ごとの旧新残高と最初の相違）を提案 |
| (2) 新台帳＋provenance 付き SNAPSHOT | 同意（契約案）。**遅着 ID 対応が未定義** | I07 で確認: 新台帳は旧台帳の event_id を記憶しないため、旧台帳で適用済みの `fill1` を新台帳へ再送すると『初見』として適用される（cash 900,000→800,000）。provenance の項目案は妥当だが、遮断機構が必要。提案: `provenance.cutover_at` を SNAPSHOT に持たせ、`at < cutover_at` の TRADE/CSV_FILL は自動適用せず PENDING（人の照合）にする。ID 一覧の持ち込みより軽く、時刻なし報告も既存契約で保留になる |
| (3) 売却後の精算金は DIVIDEND＋JSON note | 条件付き同意 | I06 で確認: note は未検証、同一 `source_event_id` の再送は二重計上される（契約どおり未実装）。次回実装: `category=POST_EXIT_SETTLEMENT` の note は必須キーを検証し、`(category, source_event_id)` の重複を LedgerError にする。`net_external_flow` に精算金を含めない運用は主系側の呼出契約として明記が必要 |
| (4) 静止・昇格・世代バックアップ | 同意 | WAL の順序（全書込停止→全接続 close→チェックポイント→hash）は正しい。`check` の `mode=ro` 接続が `-wal/-shm` を作る点は本体不変と両立（H03）。「耐久キュー未実装なら昇格保留」は、現運用では返信が LINE の手動報告・CSV が手動取込なので、**取込を止める＝キューは LINE のトーク履歴と証券 CSV** で成立する。自動取込を実装するまでは、この前提を手順に明記すれば昇格は可能 |
| (5) Windows 実機 CLI | 確認済み | H03 が win32 で通過。`mode=ro` でも `-wal/-shm` が作られる点は README 記載どおり |

### 5. 変更ファイル

`ops/tests/test_v03_review_claude.py`・`test_v031_review_claude.py`・`test_v031_review_claude2.py`・`test_v032_review_claude.py`（接続ライフサイクルのみ）、`ops/tests/test_v033_review_claude.py`（新規 7 件）、`ops/README.md`、本記録、`common/ISSUES.md`（追記）、`Codex引き渡しプロンプト.md`（B と履歴）。実台帳・外部サービスは操作していない。

### 6. 結論

**レビュー合格。286 件全件通過**、D08 の不安定さはテスト側の接続 close で解消し再現試験で固定。新しい欠陥はなし。残るのは契約の実装依頼 3 点（cutover 遮断、精算 note の検証と重複防止、`migrate --diff`）と運用注記 2 点（生 SQL 禁止、手動取込前提のキュー）。

## 2026-09-09 第7回対応：ops v0.3.4（Codex、最新）

init_snapshotにprovenance=Noneを追加。RECONCILED_OPENINGの必須項目・SHA256形式・正整数seq・時差付き日時を検証しSNAPSHOTへ保存する。provenance付き台帳のTRADE/CSV_FILLはat<cutover_atなら理由「cutover 前の約定」でPENDING、時刻なしも保留、境界一致は通常検査対象。ReportResult.pendingを追加し、保留も監査と同時永続化。境界遮断行はAPPLYで迂回不可、照合のうえDISCARDまたは別途訂正する。provenanceなしは従来動作。出所パス/hashの実物照合や担当者認証は自動化していない。

DIVIDEND noteがJSONオブジェクトかつcategory=POST_EXIT_SETTLEMENTなら、settlement_type/code/source_event_id/source_document_sha256/reconciliation_id/actor/received_at/reasonを検証。受取種別2種、空文字禁止、hash形式、引数code/atとの一致、正整数円を要求し、(category,source_event_id)の重複はLedgerError。別ハンドル・再起動でも拒否。通常note/他categoryは互換、ADJUST拒否の独立監査は未追加。精算金は主系net_external_flowに含めない。旧履歴に不正note・二重精算がある場合は再生停止し得るので、コピーで確認してから移行する。

ops/の指定全体を実行：**325件＝324 passed / 1 failed in 10.77s、skip/xfailなし**。既存286は285通過、追加J01〜J07は39通過。I06は二重計上を期待する旧試験のため、新契約に対するテスト前提の誤り。既存試験未変更。I07はprovenanceなしなので従来どおり通過し、遮断確認にはprovenance付きケースへの更新が必要。全件通過ではない。

規則マーカー移行はmigrate CLIのみ・生SQL禁止。照合済み新台帳はprovenance付き公開APIで作成する別方式。手動取込運用では全取込を止め、LINEトーク履歴と証券CSVを未処理保管先にして昇格可能。停止/再開点・処理ID・保留を記録し、再開時に照合する。自動取込では耐久キューが別途必要。原本静止・WAL反映・全接続close・hash・世代バックアップ・復旧の条件は維持。

任意のmigrate --diffは今回見送り。乖離seqは手作業を維持し、性能・逐次再生エラー契約を別途検証する。[完全なAPI契約・失敗分類・制限・試験内訳](../build-codex/OPS_V034_REPORT.md)。実台帳・外部サービスは未操作。
## 第8回（2026-09-09 05:45 JST）— ops v0.3.4 の独立レビューと I06/I07 の新契約への更新（Claude）

依頼: `build-codex/Claude引き渡しプロンプト.md` B（05:37 発行）。ledger.py / models.py / test_v034_contracts.py は変更していない。

### 1. I06/I07 の更新（`ops/tests/test_v033_review_claude.py`、指定の 2 件のみ）

- I06: 同じ (category, source_event_id) の 2 回目は LedgerError、同キー別内容（金額・理由変更）も拒否、残高は 1 回分・履歴件数不変、通常 note は互換、再起動後も重複キーが復元されることを要求。
- I07: 有効な provenance＋cutover_at を渡し、旧台帳で適用済みの ID を旧時刻で再送すると PENDING（残高不変・APPLY 不可・DISCARD 可）、cutover 後の報告は通常適用、再起動・replay・replay_known 一致を要求。provenance なしの互換動作は I07b として分離して維持。
- 削除・skip・xfail・保護条件の緩和なし。

### 2. 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境）

| 区分 | 件数 | 結果 |
|---|---|---|
| 受領時（Codex 325 件の再現） | 325 | 324 通過 / 1 失敗（旧 I06） |
| I06/I07 更新後 | 326 | 326 通過 |
| 反例追加後（`ops/tests/test_v034_review_claude.py` K01〜K13、38 ケース） | **364** | **363 通過 / 1 失敗（K06）**。skip/xfail なし。6.30s |

内訳: 共通 162 ＋ 主系 43 ＋ ops 159（Codex 61 ＋ Claude 98）。**全件通過ではない。**

### 3. 追加した 38 ケースと判定

| 重点 | ケース | 結果 |
|---|---|---|
| provenance の型・空・hash・日時・未知 method | K01（16 形）: dict でない・大小文字違い・MANUAL・hash 桁不足/非 16 進/None・seq 文字列/bool/0・空白 actor・空 reason・naive/日付のみ cutover・reconciled_at None・非文字列 policy → すべて LedgerError、SNAPSHOT は保存されず、その後正しい provenance で初期化できる | 通過 |
| 出所の永続化と cutover の復元 | K02: `_` キー除去のうえ verbatim 保存、cutover 直前は保留・ちょうどは受理、再起動・replay・replay_known 一致、replay(at) は境界約定を含み保留を含まない | 通過 |
| cutover_at と SNAPSHOT.at の関係 | K03: `at < cutover_at` が受理され、開始残高以後・cutover 前の正当な約定まで保留になる（設定ミス検知なし） | 通過（B: 現状の記録） |
| 未知通知・再送・改変 | K04: 通知のない cutover 前報告も保留、再送は DUPLICATE、同 ID で時刻を後ろへ改変すると ID_PAYLOAD_CONFLICT、TRADE イベントは 1 件のみ | 通過 |
| CSV/TRADE 交差・時刻なし・APPLY 迂回 | K05: 同一約定の TRADE・CSV・時刻なし CSV がすべて保留、いずれも APPLY 拒否、filled_qty 0 | 通過 |
| **DISCARD 後の再送** | **K06**: DISCARD した行（CSV も TRADE も）を再送すると `sqlite3.IntegrityError: UNIQUE constraint failed: ledger_events.event_id` が**生のまま** `import_csv_fills` / `report` から漏れ、同じバッチの他の行も取り込まれない | **失敗（A）** |
| cutover 後の経路 | K07: PARTIAL→CORRECTION→匿名 CSV は従来どおり。訂正だけを cutover 前の時刻で送ると保留（業務時刻による遮断で、改変検知ではない） | 通過 |
| 精算 note の欠損・偽装 | K08（11 形）: 不正 settlement_type、code 不一致、received_at ずれ/naive、非 16 進 hash、空 ID、None actor、amount 0/−1/True/float → すべて LedgerError、ADJUST 0 件 | 通過 |
| オフセット違い | K09: UTC 表記の received_at は受理され、同キーの別オフセット再送は拒否 | 通過 |
| 重複キーの範囲 | K10: (a) 同じ精算 note を DEPOSIT で送ると検証も重複拒否もされない、(b) source_event_id の前後空白は別キー、(c) 保有中銘柄への POST_EXIT_SETTLEMENT も受理 | 通過（B: 契約の限界の記録） |
| 同時書込 | K11: 先に開いていた別ハンドルも書込前同期で重複を検知、ADJUST 1 件 | 通過 |
| 障害時ロールバック | K12: 検証後・INSERT 前の失敗でキーを消費せず、トランザクションは戻り、再試行が通り、その後の同キーは拒否 | 通過 |
| 新台帳の時点再生 | K13: replay(at < SNAPSHOT.at) は空（cash 0・保有なし）で旧台帳を再現しない | 通過（B: 契約明記が必要） |

### 4. K06 の詳細（A）

- 再現: 保留行を DISCARD → 同じ event_id を再送。`_State` は `seen` にも `pending` にも無いので通常経路へ進み、`_commit` が `ledger_events.event_id` の UNIQUE 制約で `sqlite3.IntegrityError` を投げる。`import_csv_fills` は LedgerError しか捕まえないため例外がそのまま呼出側へ漏れ、バッチ内の残りの行は処理されない。`report()` も同様に生例外。トランザクションはロールバックされ台帳は壊れない（cash 不変・継続利用可）。
- **v0.3.4 固有ではない**: provenance なしの台帳で「不明 proposal_id で保留 → DISCARD → 再送」でも同じ（v0.3 の DISCARD 導入時から）。今回 OPS_V034_REPORT が「DISCARD した行を再送すれば再び保留され得る」と契約に書いたことで、契約と実装の食い違いが確定した。
- 提案: DISCARD 済み event_id を状態に保持し、再送は `DUPLICATE`（reason_code `DISCARDED`）として監査だけ残し、残高イベントは追加しない。これで「処理済み ID の記憶は取込側の責務」（第7回の隙間）も台帳側で担保できる。少なくとも `IntegrityError` は LedgerError に変換して `errors[eid]` へ入れること。

### 5. 契約の確認（依頼 3 項）と ISSUES への追記

- 出所 hash は形式検証のみ（K01）、通常 note・他 category は互換（I06・K10）、cutover は全旧 ID の持込ではない（K04: 通知のない報告も時刻だけで判定）。旧精算履歴が再生検証で停止し得る点は、既存台帳に category=POST_EXIT_SETTLEMENT の不正 note が「ない」限り影響なし（v0.3.3 以前にこの category を書く経路はなかった）。
- ISSUES へ: (1) K06（A）、(2) cutover_at と at の関係未定義（K03）、(3) 重複キーの範囲（K10 a/b/c）、(4) 新台帳の時点再生は SNAPSHOT 前が空（K13）、(5) DISCARD 後の再送契約（K06 の提案）。

### 6. 変更ファイル

`ops/tests/test_v033_review_claude.py`（I06/I07 のみ、I07b 追加）、`ops/tests/test_v034_review_claude.py`（新規 38 ケース）、`ops/README.md`、本記録、`common/ISSUES.md`（追記）、`Codex引き渡しプロンプト.md`（B と履歴）。実台帳・外部サービスは操作していない。

### 7. 結論

v0.3.4 の新方式（provenance 検証・cutover 遮断・精算 note 検証と重複拒否）は、公開 API から到達できる範囲で 37 ケースに耐えた。**残る 1 件 K06（A）は DISCARD 後の再送で生の DB 例外が漏れる既存欠陥**で、契約の記述と食い違う。364 件中 363 通過、全件通過ではない。

## 第9回（2026-09-09 05:55 JST）— ops v0.3.5: K06 の修正と契約の隙間 4 点の採否（Claude 実装）

依頼: `Codex引き渡しプロンプト.md` B（05:45 発行、則光さんが Claude に指示）。ops/ は Claude の担当範囲なので Claude が実装した。Codex 追加試験（test_v032/v033/v034_contracts.py）・共通受入テスト・主系コードは変更していない。

### 1. K06（A）の修正 — DISCARD 済み ID の記憶

- `_State.discarded: set[str]` を追加。`PENDING_RESOLVED(DISCARD)` の適用時に event_id を登録（再生・replay・replay_known・再起動で復元される）。
- `_on_trade` / `_on_csv_fill`: DISCARD 済み event_id の再送は **DUPLICATE**（TRADE は `duplicate=True`、CSV は `skipped`）。適用も保留もせず、残高イベントを追加しない。監査行の reason_code は **DISCARDED**（公開フィールドが変わっていれば従来どおり ID_PAYLOAD_CONFLICT が優先）。
- 安全網: `_commit` の INSERT で `sqlite3.IntegrityError`（履歴 ID の UNIQUE 衝突）が起きた場合は LedgerError に畳む。`import_csv_fills` は `errors[eid]`、`report` は `error` に入り、バッチの他の行は処理を続ける。生の DB 例外は呼出側へ漏れない。

### 2. 契約の隙間 4 点の採否

| 項目 | 採否 | 実装 |
|---|---|---|
| (a) cutover_at と SNAPSHOT.at | **採用: `cutover_at <= at` を必須** | `_on_snapshot` で `cutover_at > snapshot_at` は LedgerError、SNAPSHOT は保存されない。旧台帳の停止時点が開始残高の時点より後になることはないため |
| (b) 精算重複キーの範囲 | **採用: 3 点すべて** | DEPOSIT（や他 kind）に `category=POST_EXIT_SETTLEMENT` の note を付けると拒否（DEPOSIT は外部入金専用）。`source_event_id` は strip 後にキー化。保有中の銘柄への POST_EXIT_SETTLEMENT は拒否（端株精算は FRACTIONAL_CASHOUT へ誘導） |
| (c) 新台帳の時点再生 | **契約として明記（実装変更なし）** | 新台帳の `replay(at < SNAPSHOT.at)` は空の台帳（cash 0・保有なし）を返す。SNAPSHOT より前の時点は旧台帳の `replay_known` で見る。README に記載 |
| (d) DISCARD 後の再送 | **採用: DUPLICATE/DISCARDED** | 上記 1。OPS_V034_REPORT の「再送すれば再び保留され得る」は本版で置き換え |

### 3. 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境）

**364 件＝364 passed、skip/xfail なし。全件通過。**（共通 162＋主系 43＋ops 159。`-X dev -W error::ResourceWarning` でも ops 159 件通過）

Claude 所有テストの期待値更新（採用した契約に合わせたもののみ。削除・skip・xfail なし）: K03（cutover_at > at を拒否）、K06（再送は skipped/duplicate、監査 DISCARDED、再起動後も記憶）、K10（DEPOSIT 拒否・空白同一視・保有中拒否）、K09/K11/K12（精算対象を未保有銘柄に変更）。Codex の J01〜J07・F・H・既存反例はすべて未変更で通過。

### 4. 変更ファイル

`ops/aitrader_ops/ledger.py`、`ops/aitrader_ops/__init__.py`（v0.3.5）、`ops/tests/test_v034_review_claude.py`（K03/K06/K09/K10/K11/K12）、`ops/README.md`、本記録、`common/ISSUES.md`（追記）、`Codex引き渡しプロンプト.md`（B と履歴）。実台帳・外部サービスは操作していない。

### 5. Codex への依頼（次回）

v0.3.5 の独立レビュー（DISCARD 記憶の再生一致・DISCARDED と ID_PAYLOAD_CONFLICT の優先順位・cutover_at ≤ at の境界・精算 scope の 3 点・IntegrityError の畳み込み）と、`共通仕様_フェーズ2_改訂案_v1.1.md` への (a)〜(d) の反映。

## 第10回（2026-09-09 06:20 JST）— ops v0.3.6: L02 の修正と L03 の日時契約の決定（Claude 実装）

依頼: `build-codex/Claude引き渡しプロンプト.md` B（06:03 発行）。Codex の独立レビュー（`build-codex/OPS_V035_REVIEW.md`、L01〜L08 17 件）に対する対応。Codex 追加試験・共通受入テスト・主系コードは変更していない。

### 1. L02（A）の修正 — 開始残高前の時点再生は空

`Ledger.replay(at)` で、SNAPSHOT の業務時刻が `at` より後なら、以後のイベントを一切適用せず空の台帳（現金 0・保有/予約なし）を返す。後着した cutover 前保留（TRADE/CSV/時刻なし）・遅着通知・訂正があっても `LedgerNotInitialized` は漏れない。SNAPSHOT ちょうどは従来どおり含まれ、別オフセット表記でも同じ瞬間で判定する。不正マーカーの検証は空判定より先に効く。provenance なしの通常台帳でも同じ契約（M02）。

### 2. L03（B）の契約決定 — 解決の業務時刻は保留行の業務時刻以後

**採用: 入力時の日時制約。** `resolve_pending(APPLY/DISCARD)` の `at` が保留行の業務時刻より前なら LedgerError（同時刻は可、別オフセットの同時刻も可、時刻なし保留行には制約なし）。拒否は履歴・監査に残らない。

- 理由: 約定より前に人が照合することはなく、許すと「保留行が存在しない時刻に解決だけが現れる」履歴になる。時点再生側で参照を補う案（B の第 2 案）は、APPLY のとき約定の金銭効果を業務時刻より前に先取りするか、解決を業務時刻まで遅らせるかの新しい規則が必要で、履歴の意味が二重になるため採らない。「保留がなければ無視」も採らない。
- 影響: 契約は `_State` の適用規則（rule_version ≥ 3）に置いたので、書込時と再生時で同じに効く。**v0.3.6 より前に受理された逆順の解決を含む履歴は再起動時に `MigrationError(seq, PENDING_RESOLVED)` で止まり、`migrate --check` の violation に位置が出る**（M06）。実台帳は未作成のため影響なし。旧 v0.2 規則区間（POLICY_UPGRADE 前）は対象外。
- Codex 試験との衝突（テスト前提の誤り、変更していない）: `build-codex/tests/test_ops_v035_review.py::test_l03`（10:00 の保留を 08:00 で DISCARD できる前提）と `ops/tests/test_v03_integrity.py::test_discard_audit_actor_and_replay`（AT+1h の保留を AT で DISCARD）。次回 Codex に期待値更新を依頼。

### 3. 文書の誤記訂正

第9回 B の「strip 同一視（全角空白・改行は対象外＝現状の記録）」は誤り。実装は引数なしの `str.strip()` で、半角・全角空白・改行を含む前後の空白文字をすべて除去する（Codex L05 で実測）。文字列内部の空白や Unicode 正規化は行わない。`common/ISSUES.md` に訂正を追記し、Codex B から誤記を除いた。

### 4. 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境）

| 区分 | 件数 | 結果 |
|---|---|---|
| 受領時（Codex 381 件の再現） | 381 | 378 通過 / 3 失敗（L02×2、L03） |
| L02 修正・L03 制約・反例 7 件追加後（`ops/tests/test_v036_review_claude.py` M01〜M07） | **388** | **386 通過 / 2 失敗**（5 回連続で同じ）。skip/xfail なし |

内訳: 共通 162 ＋ 主系/レビュー 60 ＋ ops 166（Codex 61 ＋ Claude 105）。**全件通過ではない。**

残る 2 失敗の分類:

| テスト | 分類 |
|---|---|
| `build-codex/tests/test_ops_v035_review.py::test_l03` | テスト前提の誤り（L03 で入力拒否を採用。Codex が期待値を更新） |
| `ops/tests/test_v03_integrity.py::test_discard_audit_actor_and_replay` | 同上（保留 AT+1h を AT で DISCARD する前提。Codex 所有のため未変更） |

付記（不安定、今回の変更とは無関係）: `ops/tests/test_migration.py::test_m03` が 8 回中 2 回、`legacy.read_bytes() == before` で失敗した。`legacy` フィクスチャと本体の `with sqlite3.connect(path) as con:` が close しないため、WAL の本体反映が gc 任せになる第7回 D08 と同じ原因。Codex 所有のため未変更。`closing()` への統一を次回依頼に含める。

### 5. 追加した 7 ケース（`test_v036_review_claude.py`）

M01 provenance 台帳で後着保留・遅着通知・訂正があっても SNAPSHOT 前は空、ちょうど・以後は従来どおり、再起動一致 / M02 通常台帳でも SNAPSHOT 前は空 / M03 別オフセットの境界と不正マーカー優先 / M04 早い解決は APPLY・DISCARD とも拒否、履歴不変、同時刻は受理、時点再生一致 / M05 別オフセット同時刻は受理、時刻なし行は制約なし / M06 旧版で受理された逆順履歴は MigrationError(seq=3, PENDING_RESOLVED)、check の violation に位置 / M07 cutover 保留 TRADE も同じ制約。すべて通過。

### 6. 変更ファイル

`ops/aitrader_ops/ledger.py`、`ops/aitrader_ops/__init__.py`（v0.3.6）、`ops/tests/test_v036_review_claude.py`（新規）、`ops/README.md`、本記録、`common/ISSUES.md`（追記）、`Codex引き渡しプロンプト.md`（B と履歴）。実台帳・外部サービスは操作していない。


## 2026-09-09 第11回：ops v0.3.7 — O01 修正・文書明確化・独立再検証（Claude、Cowork セッション）

対象: Codex 第10回追補 `build-codex/OPS_V036_FOLLOWUP_REVIEW.md`（O01〜O05）と `build-codex/Claude引き渡しプロンプト.md` B の 4 項目。

### 1. O01（A）の修正 — 遅着通知を参照する保留 APPLY の時点再生

**採用: 既存の遅着約定と同じ「識別だけを持ち込む」方針を、確定した保留解決にも適用。** `Ledger.replay(at)` の `needed`（時点内の約定が参照する通知 ID）を `TRADE` / `CSV_FILL(_applied_proposal_id)` に加えて `PENDING_RESOLVED(action=APPLY)` の `proposal_id` からも集め、業務時刻が `at` より後の `NOTICE_CREATED` は従来どおり `context_only`（予約なし）で読み込む。

- 先取りなし: PENDING_RESOLVED 自体は業務時刻フィルタの対象なので、解決より前の時点では保留行のまま（残高不変）。解決時刻ちょうどで現金・保有に入る。
- 未来予約の非漏出: context_only の通知は `_recalc` で予約に数えない。約定で残数量が 0 になるので、通知作成時刻以後の再生でも予約は 0（P01 で全時点を検査）。
- DISCARD は対象外: 通知を参照しないため識別を持ち込まない。通知作成時刻以後は通知の予約が通常どおり現れる（P04）。
- 不明参照の一律無視はしていない。時点内に解決があるのに通知が履歴のどこにもなければ従来どおり LedgerError。
- 契約変更なし: L03 の入力制約（解決は保留行の業務時刻以後）は維持。Codex 試験の期待値は変更していない。

### 2. 文書の明確化（第10回の記述の訂正）

- 早い解決の拒否: `ledger_events`・残高・保留行は不変だが、`ingest_attempts` に `REJECTED / VALIDATION`（detail に理由）が残る（実測）。第10回の「履歴に残らない」は残高イベント列の意味であり、監査は残る。README・本記録の表現を改めた。
- 09:00 の過去残高と現在の保留: `replay(09:00)` が返す `LedgerView` には pending 欄がなく、「10:00 の保留を含む」とは言えない。現在の保留は `pending_rows()`。
- `migrate --check`: 逆順解決の位置は、マーカーなしは exit 0 の `violation` 欄、マーカー後は exit 1 の `MigrationError` 欄。どちらでも seq / kind が取れる（Codex O04 実測を引用）。

### 3. 追加試験 `ops/tests/test_v037_review_claude.py`（P01〜P07、9 ケース、全通過）

P01 解決前（保留時刻の前後・解決 1µs 前）は 1000000・予約 0、解決時刻ちょうど・以後は 900000・100 株・予約 0、通知作成時刻以後は現在残高と一致 / P02 別オフセット（UTC・−05:00・+09:00）で同じ瞬間 / P03 通知作成時刻＝解決時刻 / P04 DISCARD は通知を持ち込まず、通知作成時刻以後に予約 100200 / P05 再起動後の一致と `replay_known`（CSV→通知→解決の記録順で各 seq の残高・予約） / P06 再生は読取専用（`ledger_events`・現在残高・seq 不変） / P07 L02（開始残高前は空）と L03（早い解決は拒否、seq・残高・保留不変）の維持。

### 4. 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境＝則光さんの PC 上の Cowork VM）

| 区分 | 件数 | 結果 |
|---|---|---|
| 受領時（Codex 追補の再現） | 402 | 401 通過 / 1 失敗（O01） |
| O01 修正後（追加前） | 402 | 402 通過 |
| P01〜P07 追加後 | **411** | **410 通過 / 1 失敗**。skip/xfail なし |

内訳: 共通 162 ＋ 主系/レビュー 74 ＋ ops 175（Codex 61 ＋ Claude 114）。**全件通過ではない。**

残る 1 失敗の分類:

| テスト | 分類 |
|---|---|
| `common/tests/phase2/test_ledger.py::test_unconfirmed_next_jst_morning` | **時計依存（テスト前提の誤り、今回の変更と無関係）**。`ledger` フィクスチャが `create_notice(p)` を `at` なしで呼ぶため `created_at` が実行時刻になり、固定の 2026-09-09 06:50 JST を実行時刻が超えた時点（同日 06:50 JST 以降、恒久的）で `unconfirmed(morning)` に含まれなくなる。第10回追補の実測（06:40 JST）では通過していた。共通所有のため未変更。フィクスチャで `at=AT` を渡す修正を Codex に依頼 |

### 5. 変更ファイル

`ops/aitrader_ops/ledger.py`（replay の needed）、`ops/aitrader_ops/__init__.py`（v0.3.7）、`ops/tests/test_v037_review_claude.py`（新規）、`ops/README.md`、本記録、`common/ISSUES.md`（追記）、`Codex引き渡しプロンプト.md`（B と履歴）。実台帳・実口座・実 LLM・LINE は操作していない。自動連携（H 系列）はこの開発ループに混ぜていない。


## 2026-09-09 第12回：ops v0.3.8 — Q09/Q10 の契約決定・対応・独立再検証（Claude、Cowork セッション）

対象: Codex 第11回 `build-codex/OPS_V037_REVIEW.md`（Q01〜Q10）と `build-codex/Claude引き渡しプロンプト.md` B の 5 項目。

### 1. Q09（B）— 訂正の業務時刻の契約

**採用: 入力時の日時制約。** 訂正（CORRECTION）の `at` は、訂正対象の約定が残高に反映された業務時刻（有効時刻）以後に限る。有効時刻は、通常の約定＝約定時刻、保留 APPLY＝解決時刻、訂正の訂正＝その訂正の時刻。約定記録に `effective_at` を追加して判定する（`_apply_fill(effective_at=…)`、`_on_pending_resolved` は解決時刻を渡す）。違反は LedgerError で、`ingest_attempts` に REJECTED/VALIDATION が残り、`ledger_events`・残高・seq は不変（R01）。同時刻は別オフセット表記でも受理（R02）、時刻なし訂正は制約なし（R04）。`rule_version ≥ 3` のみ。

比較した案:

| 案 | 内容 | 判断 |
|---|---|---|
| A. 入力拒否（採用） | 訂正時刻 < 対象の有効時刻を拒否 | L03 と同じ原則（対象がない時刻に効果だけが現れる履歴を作らない）。通常約定の前倒し訂正（従来から再生不能）も同じ規則で閉じる |
| B. 再生契約（不採用） | 時点再生で訂正を「対象が反映されるまで保留」し、対象の有効時刻に効かせる | 業務時刻 09:30 の訂正が 10:00 に効くという二重の意味になる。未来 APPLY の識別だけでなく金銭効果の先取り／遅延の規則が新たに要る。B の「未来の APPLY を一律に先取りする修正は禁止」にも抵触しやすい |
| C. 参照欠落を無視 | 対象が見つからない訂正を再生で読み飛ばす | 禁止（黙って無視） |

互換: v0.3.8 より前に受理された前倒し訂正を含む履歴は再起動時に `MigrationError(seq, TRADE)`、`migrate --check` の violation に位置（R05）。実台帳未作成のため影響なし。旧 v0.2 区間は対象外。

Codex 試験との衝突（テスト前提の変更、変更していない）: `build-codex/tests/test_ops_v037_review.py::test_q09_backdated_correction_before_apply_has_valid_replay`（09:30 の前倒し訂正が `.applied` になる前提）。新契約では拒否され、`replay(09:30)` は現金 1000000 で成立する。次回 Codex に期待値更新を依頼。

### 2. Q10（B）— 通知状態・期限切れの参照

**採用: 状態参照にも識別だけを補完（O01 と同じ方針）。** `replay(at)` の `needed` に、時点内の `NOTICE_STATE` の `proposal_id` と `EXPIRE` の `proposal_ids`（列挙分のみ）を加える。参照先の通知が時点外なら context_only で読み込むため、予約 0・解決前の残高非先取りは保たれる（R06/R07）。通知作成を消した故障注入は従来どおり LedgerError / MigrationError（R08）。Q04 の形は維持（R09）。

通知状態の業務時刻を作成時刻以後に制限する案は不採用。Q04（作成 14:00 の通知への 11:00 承認・12:00 送信・期限切れ）が受理・通過している既存契約と衝突し、金銭効果のない状態のために旧履歴を MigrationError にする必要がない。

### 3. 追加試験 `ops/tests/test_v038_review_claude.py`（R01〜R10、12 ケース、全通過）

R01 前倒し訂正の拒否・監査行・残高/seq 不変 / R02 有効時刻ちょうど（3 オフセット）と以後の受理、訂正の訂正の有効時刻、作成時刻以後の予約は Q04 と同じ / R03 通常約定の前倒し訂正も拒否 / R04 時刻なし訂正は制約なし / R05 旧版履歴の MigrationError(seq=4, TRADE) と check 位置 / R06 作成前の APPROVED/SENT/EXPIRE を各中間時点で再生、再起動・replay_known 一致 / R07 複数 ID の EXPIRE は列挙分のみ、再生は読取専用 / R08 故障注入は拒否 / R09 Q04 の形の維持 / R10 L03・O01・合算・残数量超過拒否の維持。

### 4. 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境＝則光さんの PC 上の Cowork VM）

| 区分 | 件数 | 結果 |
|---|---|---|
| 受領時（Codex 第11回の再現） | 430 | 428 通過 / 2 失敗（Q09、Q10） |
| Q09 拒否・Q10 補完の実装後（追加前） | 430 | 429 通過 / 1 失敗（Q09＝前提変更） |
| R01〜R10 追加後 | **442** | **441 通過 / 1 失敗**。skip/xfail なし |

内訳: 共通 162 ＋ 主系/レビュー 93 ＋ ops 187（Codex 61 ＋ Claude 126）。**全件通過ではない。**

| テスト | 分類 |
|---|---|
| `build-codex/tests/test_ops_v037_review.py::test_q09_…` | テスト前提の変更（Q09 で入力拒否を採用。Codex が期待値を更新） |

### 5. 変更ファイル

`ops/aitrader_ops/ledger.py`（`effective_at`、CORRECTION の時刻制約、replay の needed に NOTICE_STATE/EXPIRE）、`ops/aitrader_ops/__init__.py`（v0.3.8）、`ops/tests/test_v038_review_claude.py`（新規）、`ops/README.md`、本記録、`common/ISSUES.md`（追記）、`Codex引き渡しプロンプト.md`（B と履歴）。改訂案 v1.1 への (i)(j) 追記は Codex に依頼（親フォルダは Claude の編集範囲外）。実台帳・実口座・実 LLM・LINE は操作していない。自動連携は別枠。


## 2026-09-09 第13回：ops v0.3.9 — T06 修正・T07 契約決定・独立再検証（Claude、Cowork セッション）

対象: Codex 第12回 `build-codex/OPS_V038_REVIEW.md`（T01〜T09）と `build-codex/Claude引き渡しプロンプト.md` B の 5 項目。

### 1. T06（A）— EXPIRE の不明参照

`_State._on_expire` の辞書直接参照を `get` に変え、台帳にない `proposal_id` は `LedgerError("通知 <id> がありません")`。時点再生は LedgerError、再起動（`_rebuild`）は `MigrationError(seq, 'EXPIRE')`、`migrate.check` は新規則でも旧規則でも再構築不能なので `MigrationError(kind='EXPIRE', seq=位置)` を送出する（violation の正常戻り値ではない）。列挙の一部だけ不明でもその ID を名指しする（S02）。合成故障注入の話であり、正常入力での残高破壊ではない。

### 2. T07（B）— 時刻なし訂正の契約

**採用: 有効時刻の継承。** 時刻なし訂正は訂正対象の有効時刻（`effective_at`）を継承し、時点再生の時刻フィルタにもその継承時刻を使う（`Ledger._effective_rows`）。表示時刻（`at`）は None のまま、有効時刻だけを持つ。

| 案 | 内容 | 判断 |
|---|---|---|
| A. 有効時刻の継承（採用） | 時刻なし訂正は対象の有効時刻に効く。以後の日時付き訂正は継承時刻を基準に Q09 を適用 | 「時刻なし入力に時刻制約は掛からない」（L03・v0.3.8 の採用）を保ちつつ、対象より前の時点に現れない（非先取り）。履歴の意味が一重 |
| B. 入力拒否 | 時刻なし訂正を退ける | 時刻なし保留行・時刻なし約定を受理している現契約と整合しない。退ける理由がない |
| C. 再生で対象欠落を読み飛ばす | 対象がない時点では訂正を無視 | 禁止（不明参照の無視）。継承であれば「対象がない時点」は生じない |

境界: 対象が時刻なし（時刻なし約定を時刻なし訂正）なら継承する時刻もなく、従来どおり常に時点内（S06）。`replaces_event_id` のない時刻なし訂正は対象を状態から決めるため継承できず、従来どおり常に時点内（対象が時点外なら従来どおり「訂正対象がありません」で停止する。現行の呼び出し側は常に `replaces_event_id` を渡す）。

互換: v0.3.9 より前に受理された「時刻なし訂正 → それより前の日時付き再訂正」は再起動で `MigrationError(seq, TRADE)`、check の violation に位置（S07）。実台帳未作成。旧 v0.2 区間は対象外。

Codex 試験との衝突（テスト前提の変更、変更していない）: `test_ops_v038_review.py::test_t07_timeless_correction_chain_remains_replayable` の 2 件目の訂正（時刻なし訂正を 09:00 で再訂正）が `.applied` になる前提。新契約では拒否され、`replay(09:00)` は現金 1000000 で成立する（S04 で確認）。

### 3. 追加試験 `ops/tests/test_v039_review_claude.py`（S01〜S11、12 ケース、全通過）

S01 EXPIRE 不明参照: replay LedgerError、再起動・check とも MigrationError(kind=EXPIRE, seq=位置) / S02 正常な複数 ID の EXPIRE と一部不明の名指し / S03 保留 APPLY の時刻なし訂正は 10:00 に効き、09:00・直前は未反映、再起動一致 / S04 継承時刻より前の再訂正は拒否（監査行・残高/seq 不変）、10:00 ちょうど（UTC 表記）以後は受理、各時点再生 / S05 通常約定の時刻なし訂正の連鎖 / S06 時刻なし対象は継承なし / S07 旧版履歴の MigrationError と check 位置 / S08 SELL 価格のみ訂正の取得原価維持 / S09 部分合算・時刻なし訂正・取消後の訂正と予約 / S10 cutover 遮断（日時付きは拒否、時刻なしは cutover 保留。新しい正常経路なし） / S11 Q09/Q10 維持。

### 4. Codex 指摘 4（自試験のコメント訂正）

R06: 「EXPIRED なので残 60 株予約なし」→「EXPIRED は予約を解放しないので作成時刻以後は 60120 が残る」に訂正し、`replay(H6).reserved == 60120` の assert を追加。R07: other も期限切れ集合に入るため「列挙外」の説明を撤回し、列挙外の非補完は Codex T05 の直接観測に委ねる旨を記載。名称も `…replays_without_reservation` に変更。

### 5. 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境＝則光さんの PC 上の Cowork VM）

| 区分 | 件数 | 結果 |
|---|---|---|
| 受領時（Codex 第12回の再現） | 457 | 453 通過 / 4 失敗（T06×3、T07） |
| T06 修正・T07 継承の実装後（追加前） | 457 | 456 通過 / 1 失敗（T07＝前提変更） |
| S01〜S11 追加・R06/R07 訂正後 | **469** | **468 通過 / 1 失敗**。skip/xfail なし |

内訳: 共通 162 ＋ 主系/レビュー 108 ＋ ops 199（Codex 61 ＋ Claude 138）。**全件通過ではない。**

| テスト | 分類 |
|---|---|
| `build-codex/tests/test_ops_v038_review.py::test_t07_…` | テスト前提の変更（T07 で有効時刻の継承を採用。Codex が期待値を更新） |

### 6. 変更ファイル

`ops/aitrader_ops/ledger.py`（`_on_expire` の不明参照、CORRECTION の有効時刻継承、`_effective_rows`）、`ops/aitrader_ops/__init__.py`（v0.3.9）、`ops/tests/test_v039_review_claude.py`（新規）、`ops/tests/test_v038_review_claude.py`（R06/R07 のコメント・assert）、`ops/README.md`、本記録、`common/ISSUES.md`（追記）、`Codex引き渡しプロンプト.md`（B と履歴）。改訂案 v1.1 への (k) 追記は Codex に依頼。実台帳・実口座・実 LLM・LINE は操作していない。自動連携は別枠。


## 2026-09-09 第14回：ops v0.3.9 の双方確認完了 — 文書の明確化 C01/C02 と最終照合（Claude、Cowork セッション）

対象: Codex 第13回 `build-codex/OPS_V039_REVIEW.md`（U01〜U05、C01/C02）と `build-codex/Claude引き渡しプロンプト.md` B の 3 項目。実装変更なし。

### 1. C01 — 旧規則区間の適用範囲の明確化

「旧 v0.2 区間は対象外」は Q09 の入力制約（`_on_trade` の `rule_version >= 3` 付き比較）に限る。有効時刻の継承（`effective = at if at is not None else target.get('effective_at')`）と `_effective_rows` の事前走査には版判定がなく、旧区間の履歴にも適用される。区別: 旧区間の**受理済み残高の保持**（現在残高・`replay_known`。U04 で 962000 を確認）と、**過去版との時点表示の完全互換**（保証しない。旧版では時刻なし訂正が常に時点内、v0.3.9 では対象の有効時刻以後）。README 第14回に記載。

### 2. C02 — `replaces_event_id` 省略時の明確化

省略時は状態側が直前の有効約定を対象に選び `effective_at` を継承するが、事前走査 `_effective_rows` は対象を解決せず時刻フィルタは None（常に時点内）。「継承できず従来どおり」は事前走査の制限に限定し、明示 ID 付きの時点再生保証を省略経路へ広げない。省略は公開モデルで許され共通仕様にも記載があるため、「呼び出し側が常に指定」を API 保証としない。第13回の記載を上記のとおり訂正（README 第14回）。新契約・実装の追加はしない。

### 3. 最終照合と実測

- T07 期待値更新（`test_ops_v038_review.py`）: 採用契約どおり（時刻なし訂正受理、09:00 再訂正は拒否・残高/seq 不変、10:00 再訂正 962000）。
- U01〜U05（18 件）: 明示 ID 付き継承（TRADE/CSV/保留経由 × 時刻あり/なし、連鎖、別オフセット、再起動・replay_known・表示 at=None）、記録順と時刻順の逆転、不明参照の LedgerError/MigrationError（NOTICE_STATE・時刻なし CORRECTION）、旧履歴の check 2 形式と原本不変・正規移行後の残高保持、cutover 保留の APPLY/訂正拒否。ops/README 第13回・本記録第13回の記述と一致。
- 改訂案 v1.1 (k): 親フォルダは Cowork の接続範囲外のため本セッションでは未読。Codex 報告（(k) 追記済み）を採用し、内容の照合は則光さんまたは次回 PC 上の Claude Code に委ねる。
- 実測（ops/、Cowork VM）: **487 件＝487 通過、skip/xfail なし。全件通過**（共通 162＋主系/レビュー 126＋ops 199）。Codex の 487 通過と一致。

### 4. 結論と次

新しい欠陥なし。ops v0.3.9 の双方確認を完了とし、Codex 宛て B を「引き渡し不要（ops v0.3.9 の双方確認完了）」として dev チャネルに公開。開発ループの次の題材（フェーズ2 後半の残り: 順序3 実行器の統合、通知層・LINE、J-Quants 実データ）は則光さんの判断で再開する。変更ファイル: `ops/README.md`、本記録、`common/ISSUES.md`（追記）、`Codex引き渡しプロンプト.md`（B と履歴）。

## 第16回（2026-09-09 11:05 JST）— フェーズ2 順序4: ops v0.4.0 judges / notify の実装（Claude、Cowork セッション）

依頼: 第15回（Codex）の成果 `build-codex/共通仕様_フェーズ2_順序4_修正提案_v0.1.md` と先行受入テスト `common/tests/phase2/test_judges.py`（69）/ `test_notify.py`（47）。Codex はサンドボックスに pytest がなく実測できず質問で停止 → 則光さんの回答「両方」（Claude が実測して実装を進める。Codex 側は pytest 導入後に再実行）を `build-codex/QUESTIONS.md` に転記して解決。

### 1. 実装（ops/aitrader_ops、すべて stub 前提。実 CLI・実 LINE・実 LLM・実口座は呼ばない）

| ファイル | 内容 |
|---|---|
| `judges.py` | `review(...)`: パケット JSON から Proposal を復元し 14 フィールドの hash を再計算。締切 = min(expires_at, その JST 日の 07:15)。record（stdout / final_message / exit_code / elapsed_seconds）を stub / replay から読み、Claude は `type=result` 外包の `result` 文字列、Codex は `final_message` を厳密 JSON（重複キー・NaN/Infinity 拒否、additionalProperties=false、型・範囲）で検証。失敗種別の優先順位は 入力 hash → 締切 → timeout → process_error → malformed → 応答 ID/hash。Verdict のメタデータ（judge/model/cli_version/run_id/received_at）は親指定。決定ログは `log_dir/judges-YYYYMMDD.jsonl`（packet 原文・record・failure・正規化 verdict。秘密値マスク・64KB 上限。保存失敗は例外で停止）。`build_cli_request(...)`: 起動計画の純粋生成（`claude -p --output-format json --max-turns 1 --tools "" --strict-mcp-config --mcp-config …` / `codex exec - --output-schema … --output-last-message <一意> --sandbox read-only --skip-git-repo-check`）。transport=cli は隔離環境の確認が未実施のため ValueError で拒否 |
| `notify.py` | `render_message`: NEW/EXIT の Flex カード（confidence 不掲載、楽天リンクは {code} のみ展開・https・rakuten-sec.co.jp 正規サブドメイン・userinfo なし・member. 禁止）、RECONCILE/RISK の text（status 文言 5 種）。`Notifier`: SQLite outbox（同一 key の内容差し替え拒否、同一候補はキーを変えても重複しない、NEW/EXIT は台帳 APPROVED/SENT 必須）。`flush`: 送信直前に 期限（07:15 / expires_at）→ 台帳状態 → STOP（kv と STOP ファイルの OR）→ 月予算（JST 暦月・成功＋成否不明を計上・超過は BUDGET_BLOCKED と BUDGET_EXCEEDED 警告）を再確認し、SENDING claim で並行 flush の二重送信を抑止。成功の耐久記録の後に台帳 `SENT`。失敗は PENDING（予算戻し）、成否不明は UNKNOWN（同一 retry_key で再試行、24 時間超は RETRY_EXPIRED 警告で照合待ち）。`reconcile_sent`: 成功記録と台帳更新の間で落ちた行を同一成功から復旧。`handle_webhook`: 生 body の HMAC-SHA256 を JSON 解析より先に検証（401）、壊れた JSON は 400、許可 userId の 1 対 1 のみ（FORBIDDEN）、webhookEventId 必須・業務 payload hash で DUPLICATE/CONFLICT、ボタン → `Ledger.report(TradeEvent('line:'+id …))`（PARTIAL/FILLED は実 qty/price/fee 必須＝NEEDS_DETAILS、未来時刻 INVALID、不明候補 UNKNOWN_PROPOSAL）、STOP/RESUME（reconciled_at は親確認値のみ） |
| `ledger.py` | `Ledger.proposal(proposal_id)` を追加（通知に記録された Proposal の公開コピー。notify の期限再確認に使う。private 属性へのアクセスをやめるため） |
| `config/prompts/review-v1.txt` / `review-v1.schema.json` | 共通審査プロンプト（参照資料内の命令無効・数量価格変更禁止・UNKNOWN は ABSTAIN・必須観点）と応答スキーマ |

### 2. 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境）

| 区分 | 件数 | 結果 |
|---|---|---|
| 受領時（第15回の受入テスト込み、実装前） | 603 | 487 通過 / 116 失敗（未実装） |
| 実装後 | 603 | 603 通過 |
| Claude 独立試験追加後（`ops/tests/test_v040_order4_claude.py` Q01〜Q09・R01〜R10、30 ケース） | **633** | **633 通過。全件通過**。skip/xfail なし |

独立試験の範囲（仕様案 §5 の後続検証項目）: 引数契約・cli 拒否・ログ保存失敗で承認を返さない・秘密値マスクと出力上限・失敗優先順位・UTC 入力での締切・追加の malformed 形・純粋 ABSTAIN と replay の読取専用・CLI 計画の純粋性と一意出力／パス走査拒否 / 信用・方向違い・不正テンプレートの拒否・enqueue 契約と同一候補の重複抑止・UNKNOWN の予算保持と 24 時間期限・失敗の予算戻しと翌月・STOP ファイルの OR と RESUME 後の実効停止・成功記録と台帳更新間のクラッシュ復旧・並行 flush・署名後の不正 JSON と非 dict イベント・報告の型違反／残数量超過／配送メタデータ差の DUPLICATE・confidence の非漏出。

### 3. 契約の隙間・所見（`common/ISSUES.md` に追記）

1. 通知期限の再確認に台帳の Proposal が必要で、公開 API がなかった → `Ledger.proposal()` を追加（ops 内で解決）。
2. `test_notify.py` は Notifier を close せず `tmp_path` に SQLite を残す（Windows では一時フォルダ削除時に警告になり得る）。Codex 所有のため未変更。
3. transport=cli / line は未接続。実 CLI の引数・出力形式は一次資料のみで、実機での互換確認は後続工程（設計書 10 章フェーズ 0）。
4. Codex サンドボックスの pytest 未導入は環境課題として継続（則光さんが導入 → Resume）。

### 4. 変更ファイル

`ops/aitrader_ops/judges.py`・`notify.py`（新規）、`ledger.py`（`proposal()` 追加）、`__init__.py`（v0.4.0）、`ops/config/prompts/review-v1.txt`・`review-v1.schema.json`（新規）、`ops/tests/test_v040_order4_claude.py`（新規 30）、`ops/README.md`、本記録、`common/ISSUES.md`（追記）、`build-codex/QUESTIONS.md`（回答転記）、`Codex引き渡しプロンプト.md`（B と履歴）。

## 第17回（2026-09-09 11:40 JST）— ops v0.4.1: Codex 第16回レビュー（V01〜V13）の A 修正と V10 の契約判断（Claude、Cowork セッション）

依頼: `build-codex/Claude引き渡しプロンプト.md` B（11:18 発行、自動連携経由）。対象は `build-codex/OPS_V040_REVIEW.md` の A 13 ケース・B 2 ケース。受領時の再現: 682 件中 667 通過 / 15 失敗（Codex 実測と一致）。

### 1. 指摘別の対応

| ID | 対応 |
|---|---|
| V01（3）| 決定ログの行全体（packet・record の全キー・verdict・raw_answer）に再帰的な秘密値マスクと 64KB 上限を適用（`_mask_deep`）。戻り値の Verdict はマスクしない（ログだけ） |
| V02 | 送信関数が例外で落ちた行は、自分の claim を持つ間に UNKNOWN（同一 retry_key・月枠保持・SEND_UNKNOWN 警告）へ更新してから例外を伝える。プロセスごと落ちて SENDING が残った行は、猶予（10 分）超過で UNKNOWN へ回復して再試行。猶予内は他 flush の処理中とみなし触らない |
| V03 | 予算確認と claim を同一 `BEGIN IMMEDIATE` に入れ、SENDING も月枠に計上。行の更新は `state='SENDING' AND claimed_by=自分` の条件付きにして、古いスナップショットや他 claim による SENT 巻戻しを防ぐ |
| V04 | UNKNOWN 行は候補期限・07:15・STOP を過ぎても EXPIRED にしない（UNKNOWN のまま・予算保持・SEND_UNKNOWN 警告） |
| V05 | 一度 UNKNOWN になった行は後続試行が failure でも UNKNOWN のまま（未送信と断定しない）。success で SENT |
| V06 | RECONCILE / RISK は登録日の JST 終日で失効（EXPIRED）。翌日・新月に一括配信しない。UNKNOWN 行の同一 retry_key 再試行は妨げない |
| V07 | 制御操作（STOP/RESUME）は受信時刻とは別に**イベント時刻**を保存し、より新しい制御より前の遅着操作は状態を変えず監査だけ（`STOP_LATE` / `RESUME_LATE`、結果 IGNORED） |
| V08（2）| `postback` / `message` がオブジェクトでないイベントは INVALID に畳み、同一バッチの後続イベントは処理を続ける |
| V09 | 署名は ASCII 以外を 401（`compare_digest` に非 ASCII を渡さない） |
| V13 | ホストに非 ASCII を含むリンク（IDN・全角）を拒否（ブラウザの ASCII 化で会員ホスト等に正規化され得るため） |
| V10（2、B）| **採用**: STOP / RESUME もイベント時刻が受信時刻より未来なら INVALID（報告イベントと同じ規則）。「STOP は受信即時優先」の例外は採らない（未来時刻は時計ずれか改変であり、監査上の順序が壊れる。停止したい本人は正しい時刻の操作を再送すればよい） |

### 2. 実測（ops/ で `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q`、Claude 作業環境）

| 区分 | 件数 | 結果 |
|---|---|---|
| 受領時 | 682 | 667 通過 / 15 失敗（V01×3・V02〜V07・V08×2・V09・V10×2・V13） |
| 修正後 | 682 | 682 通過 |
| Claude 回帰試験追加後（`ops/tests/test_v041_order4_review_claude.py` S01〜S11、14 ケース） | **696** | **696 通過。全件通過**。skip/xfail なし |

Claude 所有試験の期待値更新は R04 の 1 点だけ（新月 flush の古い RISK 行を SENT → EXPIRED。V06 の採用に伴う）。削除・skip・xfail なし。

S 系列: 送信例外→UNKNOWN→同一キーで再送成功（S01）、他プロセスの SENDING を猶予後に回復（S02）、2 ハンドル並行で実送信呼出が予算どおり 2 回（S03）、UNKNOWN→failure→success で予算 1・同一 retry_key（S04）、期限後・STOP 後も UNKNOWN 維持（S05）、日次通知の登録日失効と UNKNOWN 再試行の両立（S06）、UTC 引数の月境界と再起動（S07）、制御の順序と未来拒否・監査行（S08）、形崩れと署名変種（S09）、IDN・見た目そっくりホスト（S10）、ログ行全体のマスク（S11）。

### 3. 契約の隙間（`common/ISSUES.md` に追記）

- SENDING の猶予 10 分は本実装の定数で、実 LINE の応答時間に合わせて設定化が必要（仕様案 v0.2 に追記依頼）。
- 制御操作の順序判定はイベント時刻（LINE の timestamp）基準。時計ずれの許容幅（未来判定）は現在 0 秒で、実運用では数秒の許容が必要になり得る。

### 4. 変更ファイル

`ops/aitrader_ops/notify.py`、`judges.py`、`__init__.py`（v0.4.1）、`ops/tests/test_v041_order4_review_claude.py`（新規 14）、`ops/tests/test_v040_order4_claude.py`（R04 の期待値）、`ops/README.md`、本記録、`common/ISSUES.md`（追記）、`Codex引き渡しプロンプト.md`（B と履歴）。

## 第18回（2026-09-11 20:57 JST）— ops v0.4.1（Codex 単独継続分）の独立レビュー（Claude / Cowork）

依頼元: ユーザー指示「Code でなく Cowork で進めて」（Claude 側の再開）。`build-codex/Claude引き渡しプロンプト.md` の B は
2026-09-11 04:25 時点で「引き渡し不要・Codex 単独継続」だったため、B の再実行ではなく**最新 ops 成果の独立レビュー**として実施した。
Claude は ops のコードを変更していない。追加したのは `ops/tests/test_v041_resume_review_claude.py`（11 件）のみ。
共通受入テスト・Codex 試験・主系・共通仕様本文は無変更。skip / xfail / 条件緩和なし。

### 実測（環境の制約を明記する）

| 対象 | コマンド（ops/ で実行） | 結果 |
|---|---|---|
| 追加前（既存のみ・収集できた範囲） | `python -m pytest ../common/tests/phase2 tests -q`（下記 13 ファイルを除外） | **294 passed / 1 failed**（295） |
| 追加後（本レビュー 11 件を含む） | 同上 | **300 passed / 6 failed**（306） |

- **指定の全体コマンド `../common/tests/phase2 ../build-codex/tests tests` は実行できていない。** Cowork のデバイスブリッジが
  Windows 側の不具合（2026-09-08 の更新）でシェルを持てず、ファイルを 1 本ずつ取り込む方式になったため、
  `build-codex/aitrader`（`aitrader.packet`）を取得できなかった。`aitrader` に依存する **13 ファイル**（common 4・ops 9）は**収集していない**。
  したがって Codex 報告の「2189 passed / 9 skipped」は**本レビューでは検証できていない**。数値を引き継がないこと。
- 既存分の 1 failed は `common/tests/phase2/test_ops_rereview.py::test_r20`。原因は `aitrader` 未取得という**環境要因**であり、
  実装の欠陥ではない。
- 追加分の 5 failed はすべて本レビューの反例（W01 が 4 件、W02 が 1 件）。残り 6 件は通過＝今回の補強が保たれていることの確認。
- 実行環境は Cowork のクラウド側（Linux, Python 3.11, pytest 9.1.1）。`ops/aitrader_ops/notify.py` は
  取り込み時点（mtime 1789119696218）のもので、レビュー後に再取得して同一であることを確認した。

### 新規指摘

| ID | 内容 | 分類 |
|---|---|---|
| **W01** | **直接 `Notifier.stop()/resume()` に未来時刻の `event_at` を渡すと、その後の本物の STOP が遅着扱いで無視され、緊急停止が効かなくなる** | **A（安全性）** |
| W02 | 同一候補・別 key・内容変更の `enqueue` が、完全重複と区別されずに黙って捨てられる | B（契約未定義） |

#### W01（A）— 未来時刻の制御イベントが緊急停止を無効化する

再現（`test_w01a`〜`test_w01d`）:

1. `stop(now=T, event_at=T)` → STOPPED
2. `resume(now=T+1h, reconciled_at=T+30m, event_at=T+1day)` → **RESUMED**（未来時刻が検査されない）
3. `stop(now=T+2h, event_at=T+2h)`（本物の緊急停止） → **IGNORED**、`is_stopped()` は False のまま
4. その状態で `flush()` すると **NEW 候補が SENT される**（`test_w01c` で確認）

原因は `_latest_control_event_at()` が未来（T+1day）に進み、以後のすべての制御操作が `event_at < latest` = 遅着と判定されること。
逆向き（未来時刻の STOP → 以後の正当な RESUME が IGNORED＝復帰不能）も同じ根で起きる（`test_w01b`）。

**すでに他の 2 箇所では未来時刻を拒否している**ため、これは設計思想の抜けではなく実装の取りこぼしである。

- webhook 経路 `notify._apply_event`: `if event_at > now: return INVALID`（V10 として採用済み。既存の `test_s08` が担保）
- 読み取り経路 `stop_status._time()`: 未来の `at` / `event_at` を `ValueError` にする

その結果、書き手と読み手が**同じ履歴について正反対の判断をする**（`test_w01d`）。
実測では `inspect_stop_status()` が `STATE_DB_INVALID` → `status='UNKNOWN'` / `effective_stop=True`（＝止まっている扱い）を返す一方、
同じ DB を持つ `Notifier.is_stopped()` は False を返して送信を続ける。管理表示は「不明」、実際の通知は「継続」という食い違いになる。

修正方向（ops 側・契約変更なし）: `Notifier.stop()` / `Notifier.resume()` の入口で `event_at > now` を拒否する。
webhook と読み手がすでに同じ規則を持っているため、**新しい契約ではなく既存契約の適用範囲の統一**として実装できる。
遅着（`event_at < latest`）の扱いは現状のままでよい。

#### W02（B）— 内容変更の再登録が完全重複と区別できない

`enqueue(key='k2', kind='NEW', proposal_id='P1', message=訂正後)` は、`P1` に既存行（key='k1'）があると
`{'key': 'k1', 'state': 'PENDING', 'duplicate': True}` を返し、**訂正後の本文は保存も配信もされない**。
同じ key に異なる内容を入れた場合は `ValueError` になるのに、別 key + 同一候補では無言で捨てられる非対称がある。
二重通知を防ぐ現契約は妥当なので、拒否ではなく**呼出側が区別できる応答**（例: `changed=True` を添える、または CONFLICT を返す）を提案する。
仕様本文は変更していない。採否は Codex・ユーザー判断。

### 通過を確認した補強（回帰 6 件）

- `test_w03` webhook の未来時刻制御拒否（V10）は維持
- `test_w04` `stop_status` が未来の制御履歴を UNKNOWN / `effective_stop=True` に倒す
- `test_w05` 外部入力の内部属性（`_applied_proposal_id`）が TRADE payload に混入せず、`replay(at)` も壊れない
- `test_w06` `FRACTIONAL_CASHOUT` が保有銘柄を要求する
- `test_w07` `POLICY_UPGRADE` マーカーの二重付与を拒否
- `test_w08` 成否不明行が初回の月枠を保持し、24 時間の再試行期限後は新規送信しない（`RETRY_EXPIRED` 警告あり）

### 未再現・今回確認できなかったこと

- `build-codex/tests`（主系 128 ファイル）と `aitrader` 依存の ops 9 ファイルは未収集。runner・provenance・backtest 側は本レビューの対象外。
- 実プロセス同時書込、電源断、WAL 破損の耐久試験は未実施。
- `_mask` は環境変数の値と完全一致する文字列だけを置換する。URL エンコード・base64 等の変形や、
  文字列以外（bytes・数値）に埋め込まれた秘密値は素通りする。実害の再現はしていないため指摘としては未確定。
- 月境界をまたぐ成否不明行の再試行は初回月の枠を使う（Codex の明示契約）。24 時間の再試行期限があるため
  超過は境界前後 24 時間以内の件数に限られる。実測で無制限の超過は起きなかった。

### Codex への再依頼（`Codex引き渡しプロンプト.md` B に反映済み）

1. W01 の修正（`Notifier.stop()/resume()` で `event_at > now` を拒否）と、直接 API・webhook・`stop_status` の三経路が同じ時刻規則であることの回帰試験
2. W02 の採否判断（応答で区別できるようにするか、現状維持とするか）と理由・影響の記載
3. 指定の全体コマンドでの実測（Claude 側は環境制約で未実施）
