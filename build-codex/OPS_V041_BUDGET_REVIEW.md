# v0.4.1 月予算・停止中通知の反証（Codex）

2026-09-11 04:29〜04:32 JST。Codex並列レビュー担当が実施。Claudeによる独立レビューではない。ops実装は変更していない。

追加ファイル: `tests/test_ops_v041_budget_review.py`。追加20ケース、最終実測 **8 passed / 12 failed in 0.64s**、pytest終了コード1。skip/xfailなし。失敗12ケースは同根のA（実装不具合）で、最終B（テスト前提誤り）は0。

## 結果

| ID | ケース | 件数 | 結果 |
|---|---|---:|---|
| B01 | 前月UNKNOWN、翌月STOP中のRECONCILE/RISK retry、success/timeout/failure、UTC入力 | 6 | A: 旧月枠を保持しない |
| B02 | 旧月UNKNOWN retry後、新月RECONCILE/RISKを送信 | 2 | A: 新月の通知が誤ってBUDGET_BLOCKED |
| B03 | 当月SENT/UNKNOWN/SENDINGで予算1を占有、STOP中のRECONCILE/RISK | 6 | 通過。STOP対象外通知も予算を超えない |
| B04 | 前月の10分超SENDINGを翌月UNKNOWNへ回収してretry | 2 | A: 旧月枠を保持しない |
| B05 | 前月PENDINGの日次通知を失効、新月通知だけ送信 | 2 | 通過 |
| B06 | 旧月UNKNOWNの翌月retryで送信関数が例外 | 2 | A: 例外経路も旧月枠を保持しない |

初回18ケース実測では8通過・10失敗（0.59秒）のうち8件にテスト前提誤りがあった。enqueue戻り値にretry_keyがあるとの誤認を修正し、flush戻り値／障害注入用SQLite照会で取得した。修正後18ケースは8通過・10失敗（0.60秒）で、すべて月枠の反証に到達。続いて例外経路2件を追加した上記20ケースが最終結果。初回のBを実装不具合として数えていない。

## 不具合と修正案

仕様案v0.2 §3.2「UNKNOWNは枠を保持」「同一retryの成功は二重加算しない」および§8の回収時の月予算保持に対して、`Notifier.flush`はclaim時にCOALESCEで保持したmonthを、送信後 `_finish` 呼出しで `_month(now)` に書き換える。例外経路も同様。9月30日23:59:59のUNKNOWNを10月1日00:00:00にretryすると9月消費は1から0、10月は0から1に変わる。新月の照合・警告が送れなくなる。

提案: UNKNOWN（SENDINGから回収したものを含む）なら予約済み `row['month']` を通常完了と例外の両経路で維持する。元月が存在しない場合だけ現在月を補完する。新規PENDINGの明確な失敗は従来通り枠を戻し、成功／成否不明はその送信月を確保する。本番LINEの実課金照合を実施したとの意味ではなく、現行stubの予約保持契約の修正。

## 実測条件

ops/で同梱Python、`PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`、`--basetemp C:\Users\s\AppData\Local\Temp\ai-trader-budget-review`。実ネットワークとsubprocess起動をfixtureで拒否、Notifierはstubのみ。SENDINGは別送信者の耐久claimをSQLiteへ障害注入して検証。接続はfixture終了時にclose。

親担当へAの修正・全体実測を依頼済み。Claudeへの引き渡し不要。

## 2026-09-11 Codex統合後追記

親担当が予約月保持を通常完了・例外の両経路に実装。20ケースを含む全体743件が18.82秒で通過、終了コード0。上記12件の失敗は修正前の履歴。現在未修正ではない。詳細は OPS_V041_RESUME.md。
