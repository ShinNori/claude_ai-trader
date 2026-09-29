# Claude 独立確認 第25回: managed-v1 の明示的再開（D16 v2）実装の重要差分

2026-09-29。実施者 Claude（Claude Code、`D:\work\ai-trader`、Fable 5.1、サブエージェント 0 体）。依頼は[Claude引き渡しプロンプト](build-codex/Claude引き渡しプロンプト.md)の B（更新 2026-09-29T09:11 JST、依頼 hash `6c5fe10b…`）。契約は [RUNNER_RESUME_CONTRACT_DRAFT.md](RUNNER_RESUME_CONTRACT_DRAFT.md) の改訂 v2 と Codex の採否・実装確定節。

読んだのは `aitrader/runner_resume.py` 全文、`aitrader/runner.py` の差分（92 行追加・3 行削除。再開分岐のみ）、`Valuation.calculate`、`ops/aitrader_ops/gate.py` の NEW/EXIT 分岐（STOP・未確認の適用範囲）、`tests/test_runner_resume.py` のヘルパーと試験名（反証 1〜27）、README の実測記録。製品・既存試験・期待値は変更していない。追加は新規試験 1 ファイルのみ。

## 結論

**契約 v2 どおりに実装されており、重大な不整合は無い。実装バグ 0 件。** 指摘は 1 件（R25-01、低、既存試験の環境依存で再開経路とは無関係）。

| 確認点 | 結果 |
|---|---|
| 適用範囲 | `prior is None or policy is None` で従来の停止文言に戻る。managed-v1（`managed_stop_policy` あり）かつ INTENT 行がある場合だけ再開経路に入る。legacy home の既存 3 assert は不変 |
| 所有の証拠 E1〜E6 | `proposal`/`notice`/`policy` の公開 API だけで検査。E1 候補 hash、E2 `history[0]` が CREATED かつ `started_at ≤ t0 ≤ now`、E3 履歴長が状態と一致し APPROVED は `t0 ≤ t1 ≤ now`、E4 約定なし・UNCONFIRMED、E5 予約額が `reserve_amount`／株数が qty、E6 同 run の他 INTENT 候補（同銘柄）が通知を持たない。1 つでも欠ければ `OWNERSHIP_UNPROVEN` |
| 除外 view | `LedgerView` の写しで自分の `reserve`/`reserved_shares` だけを引く。負や不足は `RESERVATION_MISMATCH`。他候補の同銘柄予約は残る。**評価資産は現金と保有だけから計算されるため除外で膨らまない**（新規試験で固定） |
| seq 照合 | `seq_before_notice_read` を `notice` 読取の前に採取。C2 は `set_notice_state` 直前、C3 は journal トランザクション直前に照合。T1 依存の整合照合で、非協調 writer の競合窓は残る（契約どおり明記） |
| 書込点 | C2: 台帳 `NOTICE_STATE`（APPROVED）1 件 → journal「outbox INSERT＋candidates UPDATE＋runs.manifest UPDATE」の 1 トランザクション。C3: journal のみ。C2 の台帳書込後に journal が失敗すると C3 状態になり、次回は C3 として再開する（連鎖が閉じている） |
| BUY/SELL の停止条件 | `STOP_STATE_UNKNOWN`/`STOP_ACTIVE`/`UNRESOLVED_LEDGER` は BUY のみ。SELL は gate の EXIT 判定（売却可能株数）に委ねる。gate 側でも STOP・未確認は NEW 分岐内にあり整合 |
| 二承認 | `validate_verdicts` は同 run/mock/同候補 hash/APPROVE/執行日受信/`now` 以前/締切前を要求。片方欠落は `REVIEW_INCOMPLETE`、REJECT や不正は `REVIEW_INVALID` |
| 記録 | C2 は元判定を `resume.original_gate` に退避して `gate` を再評価結果で置換、C3 は元 `gate` を保持。`manifest.resumes` は `runs.manifest` に保存され `started_at` は不変。再開拒否は `failure.json` の `status=SYSTEM_ERROR`・`detail='NEEDS_RECONCILIATION: <code>'` |
| 入力不変 | 再開時に判定や引数を変えると既存の `request_hash` 検査で「同じ run_id の入力変更はできません」となり、何も書かない（新規試験で固定） |

## 指摘

### R25-01（低・既存試験の環境依存）`test_managed_stop.py::test_existing_home_and_dropbox_target_are_untouched` が作業フォルダの位置に依存する

再現条件: リポジトリが Dropbox 外（`D:\work\ai-trader`）にある状態で実行。
実測: Codex の関連実測で 1 failed。テストは `repo_root/'__managed_stop_forbidden__'` を渡し「パスに Dropbox を含むので拒否される」ことを期待しているが、リポジトリが Dropbox 外へ移ったため拒否されない。
影響: 再開経路とは無関係。ただし今後の Windows 実測で常に 1 failed が混ざり、実装の失敗と区別しにくい。
修正案（Codex の試験）: 拒否対象のパスを `tmp_path/'Dropbox'/'__managed_stop_forbidden__'` のように**明示的に Dropbox を含む一時パス**で作る。製品側の Dropbox 拒否ガードは変えない。

### 記録のみ

- REJECT 判定を含む再開は `REVIEW_INVALID` になる。通常 run では REJECT は gate の不許可であって「無効」ではないので語の使い分けが違うが、再開では入力不変の検査（`request_hash`）が先に効くため、実際にこの経路に入るのは判定ヘルパーを直接呼ぶ場合だけ。実害なし。

## 追加した反証 6 件（`tests/test_runner_resume_claude_contract.py`）

Codex の 31 件（反証 1〜27）と重複しない角度。

- 判定を差し替えた再実行は `request_hash` 検査で拒否され、台帳・journal に何も書かない。判定ヘルパー単体では REJECT が `REVIEW_INVALID`
- 除外 view で `Valuation.calculate` の結果（equity・daily_pnl・peak）が元 view と同一。`available` だけが予約額分戻る
- C2 再開後、`resume.original_gate` が INTENT 保存時の判定と一致し、`gate` と別オブジェクト（deepcopy）。journal の保存値も同じ。`manifest.started_at` 不変
- 別 run_id は中断候補を引き継げない（既存検査）。書込なし
- 保留行（未知 proposal の CSV 約定）がある状態で、SELL の C2 は再開され、BUY の C2 は `UNRESOLVED_LEDGER` で拒否。拒否時の `failure.json` の形式
- C2 再開で台帳イベントはちょうど 1 件増え、同 run の再実行は完了結果の参照のみ（冪等。outbox 1 件）

実測: `6 passed / 1.82 秒`（初稿 2 件は当方の設計誤り: 再開で判定や引数を変えると入力不変検査に当たることを見落としていた。修正後に全通過）。Windows 11、Codex 同梱 CPython 3.12.14、`PYTHONPATH=ops;build-codex`、`-p no:cacheprovider`、basetemp は Dropbox 外。Codex の 31 件・関連 318 件は再実行していない（再実測 0 点）。

## sha256（レビュー時点）

```text
8651db9f6b069a30706f78a93bf52f9428b20af763abde1fcf1c78983233f5ea  aitrader/runner_resume.py
2df7f92c0c13ef5a2b2070a769f0bf7e1b1e3b6dc889f3e9af50744b7f64c3e5  aitrader/runner.py
60643d92a2b3dfe0f117d7aea463c15e91504d503f399a49a46c91da2ae7d2de  tests/test_runner_resume.py
cc1397c8c888c28922cfb77e47633a1e5badc8278a88bbe707f8eef340e81ad5  tests/test_runner_resume_claude_contract.py（新規, 9,089 bytes, 2026-09-29T09:15:35+09:00）
```

## 範囲外

Tier B（台帳 receipt）、翌日予約の扱い、自動再開・予約の自動解放、legacy home、実通信、Codex の 31 試験の本文精読（ヘルパーと名前のみ）。

終了時刻: 2026-09-29 09:17 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、Codex引き渡しプロンプト.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、第25回 再開経路の独立確認（バグ 0、反証 6 件全通過）の受領と、R25-01（Dropbox 配置に依存する既存試験の修正）を行ってください。
```
