# Claude 独立確認 第21回: EXIT 生成 v6 実装（derive_holdings / build_exit_proposals）の重要差分

2026-09-28。実施者 Claude（Claude Code、`D:\work\ai-trader`、Windows 実機、モデルは Claude Fable 5.1。サブエージェント 0 体）。依頼は[Claude引き渡しプロンプト](Claude引き渡しプロンプト.md)の B（更新 2026-09-28T09:22 JST、マーカー付き）。契約は [EXIT_GENERATION_CONTRACT_DRAFT.md](EXIT_GENERATION_CONTRACT_DRAFT.md) の改訂 v6 までと Codex の採否・実装確定節。

読んだのは `aitrader/exit_holdings.py` 全文、`aitrader/packet.py` の追加差分（147 行、削除 0 行）、`tests/test_exit_generation_v6.py` の試験名とヘルパー・並行部分、`tests/fixtures/exit_v6_expected.json`、契約末尾の Codex 節。製品・既存試験・例・共通仕様は変更していない。追加は新規試験 1 ファイルのみ。

## 結論

**契約どおりの経路が実装されており、実装バグは 0 件。** 前提条件（INTENT → RUN_INCOMPLETE、`unconfirmed ⊆ candidate_ids`、EXTERNAL の双方向照合、seq 再読 3 回）、残高照合式、分割換算（約定 `at` 以後の SPLIT を時刻順に乗算）、取得日（最早の有効約定 `at` の JST 日付）、`exit_lots` の前方消費、紐づかない売却の「対象外残高 → 最古ロット」、数量（`min(満了ロット残, 保有−予約)` の単元切捨て）、指値（×0.995 を刻みで切り上げ）はすべて一致。固定例の `packet_hash` `3997d6f7…` は当方の独立再計算と一致。指摘は 3 件（中 1・低 2）で、いずれも**契約の穴か堅牢性**であり、既存 BUY 生成の試験 18 件は不変（再実測 1 点）。

| 確認点 | 結果 |
|---|---|
| packet.py の差分 | 追加のみ（147 行）。既存 BUY 生成・HASH_FIELDS・render_packet は不変。既存 `test_packet_local.py` 18 passed |
| 既存試験の期待値 | 変更なし（差分は新規 2 ファイルのみ） |
| 前提条件の順序 | INTENT 検査 → seq 前 → view/unconfirmed/proposal/notice → seq 後 → 欠落・未決・EXTERNAL 照合 → 導出。契約 v4〜v6 と一致 |
| 残高照合 | `snapshot×分割 + ΣBUY 約定×分割 − ΣSELL 約定×分割 == view.qty`。Decimal で累積し不一致は `LEDGER_INCONSISTENT` |
| 取得日・配分・消費順 | 一致（有効約定のみ、`reversed` 除外、消費は業務時刻順） |
| 数量・指値・ID・決定性 | 一致。刻み境界 5 帯の切り上げを独立に確認（1005 / 999 / 5010 / 5000 / 995） |
| 読取専用 | 導出は台帳 seq と journal のバイト列を変えない |

## 指摘

### R21-03（中・契約の穴）既知集合の外にある CREATED/APPROVED の SELL 通知を検出しない

再現条件: journal に登録されていない SELL 通知を `create_notice` だけした状態（CREATED。送信前）で `derive_holdings`。
期待（契約の意図）: 「未決通知が既知集合の外 → `LEDGER_INCONSISTENT`」。
実測: `reason_codes == []`。`Ledger.unconfirmed` は SENT/EXPIRED/EXTERNAL しか列挙しないため、CREATED/APPROVED の通知は見えない。ロットの `open_sell_notice` も false になる。**ただし** `reserved_shares` は台帳が銘柄合計で返すため、生成側は `held − reserved = 0` → `NO_SELLABLE_SHARES` で候補を出さない。二重売却は起きない（新規試験で固定）。
原因: 契約 v4 の D2 が「未決 = `unconfirmed`」と定義しており、実装はそれに忠実。
重要度: 中。T1（runner 外書込の禁止）の下では起きない状態だが、runner 自身の中断でも「INTENT 行あり」で止まるので実害は小さい。一方、**公開 API だけで検出できる**のに使っていない。
修正案（採否は Codex）: 銘柄ごとに `Σ notice(pid).reserved_shares（pid ∈ candidate_ids）== view.reserved_shares[code]` を前提条件に加える（D4）。不一致なら `LEDGER_INCONSISTENT`。CREATED/APPROVED/SENT のいずれの状態でも未決 SELL は予約株数を持つため、既知集合の外の未決 SELL を状態によらず検出できる。同様に `Σ notice.reserve == view.reserved_positions[code]` で未知の未決 BUY も検出できる。ops は変更しない。契約 v6 に D4 として追記するだけで実装は数行。

### R21-01（低・堅牢性）SPLIT 以外の調整記録が `adjustments` に混ざると未捕捉例外

再現条件: `adjustments=[{"kind": "DIVIDEND", "code": "7203", "amount": 500, "at": …}]`（`ratio` なし）。
実測: `decimal.InvalidOperation` が呼出側へ漏れる。
原因: `_scaled_decimal` が `kind` を見ず全要素に `ratio` を要求する。契約は「SPLIT 記録のみ」だが、呼出側が `Ledger.adjust` の記録を全件渡す運用は自然で、そこで落ちる。
修正案: `kind == 'SPLIT'` だけを適用し、他は無視する（または `ratio` 欠落を `LEDGER_INCONSISTENT` に写す）。契約に「`kind` を持つ場合は SPLIT のみ適用」と 1 行。

### R21-02（低・堅牢性）journal が無い・`candidates` 表が無いと未捕捉例外

再現条件: 存在しないパス、または表の無い SQLite。
実測: `sqlite3.OperationalError` が漏れる。
修正案: 理由コード `JOURNAL_UNREADABLE` を 1 つ足して `HoldingDerivationResult` で返すか、契約に「呼出側の責任で例外」と明記する。前者を推奨（他の前提条件と同じ扱いになる）。

### 記録のみ

- `snapshot.at` が timezone なしだと `LEDGER_INCONSISTENT` になる（入力不備の誤分類だが生成は止まる）。
- `snapshot.positions[].qty` が文字列 `"100"` でも `int()` で受理される（JSON 由来の素の値としては型を固定したい。低）。

## 追加した反証 23 件（`tests/test_exit_generation_claude_contract.py`）

Codex の 33 試験（反証 1〜45）と重複しない点だけ。

- 固定例の `packet_hash` を独立に再計算し fixture と一致
- 売り指値の刻み境界 5 帯（切り上げ）
- 売却可能株数の上限で前方ロットだけに配分される（`exit_lots` 1 件）
- `holding_days=1` の翌営業日満了／2 銘柄の連番と除外の交錯／決定性／`exit_lots` が hash に影響しない
- `observed_seq` の型（bool・負・文字列・float）／同銘柄で `open_sell_notice` が食い違う入力の拒否／入力不変と営業日不足
- 実 Ledger: 部分約定後に取消された SELL は約定分だけ消費／消費済みロットを参照する `exit_lots`（＋スナップショット外 EXTERNAL）は `LEDGER_INCONSISTENT`／未決の外部 BUY は `open_sell` にならない／APPROVED 候補に通知が無ければ `LEDGER_INCONSISTENT`、REJECTED なら許容／BUY 数量訂正で消費済みを下回ると `LEDGER_INCONSISTENT`（台帳は銘柄合計でしか守らない）／導出→生成→`create_notice` で予約 200 株、既知外の CREATED SELL は R21-03 の安全側不変条件、登録後は `OPEN_SELL_EXISTS`／導出が台帳・journal を変えない

実測: `23 passed / 0.33 秒`（初稿は 2 件が当方の期待値誤りで失敗、修正後に全通過）。Windows 11、Codex 同梱 CPython 3.12.14、`PYTHONPATH=ops;build-codex`、`-p no:cacheprovider`、basetemp は Dropbox 外。再実測は `test_packet_local.py`（18 passed）の 1 点のみ。Codex の 409 / 41 passed は引用のみで再実行していない。

## sha256（レビュー時点）

```text
e06bdbc1031a170d9c051b7338c1b2dc4011d29c06ef45e741eacb578cd7de69  aitrader/exit_holdings.py
259142dcc524ffc825c72353d31a7b1a373f5137e97da6e84f7f1da31f9ef0eb  aitrader/packet.py
1554bba00c50c49d8ca2dcfc59059b6e860dbf2657b3e0dd5be383e572f54183  tests/test_exit_generation_v6.py
f06cf71f6c8f2b8fe8ed9b5dbea349a214995fc6e8af7171ce1773ee1cc14f8b  tests/fixtures/exit_v6_expected.json
ea53ed21720c2b65731adc076d0a9dc44cb36f19d870c9f289cde404f0fc2bfc  tests/test_exit_generation_claude_contract.py（新規, 14,843 bytes, 2026-09-28T11:30:21+09:00）
```

## 範囲外

runner への接続（snapshot / adjustments の永続化）、保存 mode、再送、実データ、実通信。Codex の 33 試験の本文は並行部分以外読んでいない。

終了時刻: 2026-09-28 11:32 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、第21回 EXIT 生成 v6 実装の独立確認（バグ 0、反証 23 件全通過）の受領と、R21-03（reserved_shares 照合で既知外の未決 SELL を検出）の採否・修正、R21-01/02 の採否を行ってください。
```
