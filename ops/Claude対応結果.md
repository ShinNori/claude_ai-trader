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
