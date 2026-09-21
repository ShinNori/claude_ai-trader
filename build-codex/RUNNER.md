# 模擬日次実行器の使い方と限界

実装：`aitrader/runner.py`。入力は生成済みProposalと模擬Verdict、出力は予約済みAPPROVED台帳とローカルのNOT_SENT通知案。実AI・LINE・注文の接続はない。ops v0.2を兄弟ディレクトリからimportできるPython環境が必要。

## 入力と実行例

Pythonのモジュール検索パスへプロジェクトのbuild-codex/とops/を追加する。既存の主系requirementsとopsパッケージを使用する。以下はAPIの接続例で、実行には別途検証済みの市場DB・イベント情報・単元情報が必要。価格取得を行う例ではない。

```python
from pathlib import Path
from datetime import date, datetime
from aitrader.packet_cli import generate
from aitrader.runner import (
    initialize_mock, normalize_proposal, mock_verdicts, run, Valuation,
)
from aitrader_ops.models import JST

home = Path.home() / '.ai-trader-mock'   # Dropbox外・模擬専用
# 初回のみ。既存ledger.sqliteがあれば明示拒否する。
initialize_mock(home, 1_000_000, [], datetime(2026, 9, 7, 16, 30, tzinfo=JST))
# ここまでに同homeのmarket.duckdbへ検証済みデータを準備する。
ps = [normalize_proposal(row) for row in generate(
    home, 'margin_bucket_long', date(2026, 9, 7),
    events_path=home/'events.json', lots_path=home/'lots.json',
)]
now = datetime(2026, 9, 8, 7, 10, tzinfo=JST)
run_id = 'mock-20260908-01'
votes = mock_verdicts(ps, run_id, now)  # 明示した模擬データのみ
result = run(home, run_id, date(2026, 9, 8), ps, votes, now,
             Valuation(day_start_equity=1_000_000, previous_peak=1_000_000))
```

generateのdictでもCLI由来JSONのdictでもnormalize_proposalで受けられる。型復元後にhash不一致なら拒否する。runは実行時刻を引数として受け、07:00より前を拒否、07:15以上はREVIEW_INCOMPLETEにする。現在時刻による自動起動は行わない。

模擬受信記録はjudgeごとに一件、run_id・proposal_id・hashを固定。modelとcli_versionがmock以外の入力は不正とする。欠損、INVALID、旧run、未来受信、締切後受信は候補を承認しない。模擬APPROVEを実AI失敗時の代用品として自動生成する経路はない。

## 台帳と保存データ

- `mock-runner.json`：initialize_mockが作る専用実行先の印。既存実台帳を採用しない。
- `ledger.sqlite`：模擬台帳。新規候補はCREATED→APPROVEDまで。SENTにはしない。
- `runner-lock.sqlite`：実行器同士の直列化用ロック。
- `orchestration.sqlite`：run・候補の処理記録とoutbox。日次枠はrunを跨いで共有。
- `runs/<営業日>/<run_id>/`：manifest.json、proposals.json、verdicts.json、gate_results.json、result.json。異常時はfailure.json。

同run・同入力の再実行は保存結果を返し、ファイルが失われていれば再出力する。これは当時の結果の参照であり、締切後に改めて注文可能になったことを意味しない。同runの入力変更は拒否。候補IDが同じでevents等を含む内容が変わった場合も拒否する。

前候補の予約を反映してから次候補のviewを取得する。日次枠はBUYのINTENT/APPROVEDを数え、模擬モードでは送信済み数0と別に送信待ちの枠を検査する。runの候補順はproposal_idの昇順。

途中で台帳だけに通知が残った場合は予約を保持したまま停止する。自動取消・再作成はしない。台帳とoutboxを跨ぐ完全な自動復旧は未実装で、専用の照合APIが必要。単一実行器のロックを使用するが、直接Ledgerへ書く別プロセスまではロックしない。mock台帳を別の書込元と共有しないこと。

## 評価値の契約

市場DBの前営業日終値で `equity = 現金 + Σ保有株数×終値` を最後に円へhalf-upする。保有銘柄の終値が一つでも欠けたら停止する。予約は現金からまだ引かれていないため、評価資産から再度控除しない。

Valuationにはday_start_equity、previous_peak、net_external_flow（入金−出金）を渡す。値は整数円、基準資産と高値は正。日次損益は `equity − net_external_flow − day_start_equity`。DDは `adjusted_equity = equity − net_external_flow` と `max(previous_peak, adjusted_equity)` で比較する。入金を利益扱いせず、出金を損失扱いしない。

ゲートの集中度は実額equityを使うため、DDの入出金調整後スケールと混ぜない。ゲート側DDは同額のequity/peakを渡して中立化し、実行器が調整後DDを別途必須検査する。過去高値を当日基準スケールへ調整する処理は入力側の責任で、日跨ぎの自動高値管理は未実装。集中度の分子はopsの簿価契約を維持しており、完全な時価集中度ではない。

## 検証

`tests/test_runner.py`：**20 passed in 3.59s**。予約競合、日次枠のrun跨ぎ、片側INVALID、07:15前後3境界、同run再実行・成果物、入力変更拒否、片側欠損、旧run応答、DBの休日反映、カレンダー欠損、STOP、入金調整後の2%損失、正常0候補、既存台帳拒否、台帳だけ残ったケース、CLI JSON入力、作成後停止と再開拒否、評価価格欠損。

最初の予約競合テストでは出金を損失と誤って扱う入力になっていたため、自前テストの前提を「現金15万円＋保有時価85万円」に修正して予約だけの競合を検証した。受入テストの期待値やopsは変更していない。

実データ取得、実CLI、実通知、完全なクラッシュ自動復旧、日次ジョブの自動スケジュールは未実装。再々レビューで見つかったopsの11失敗を修正済みとは扱わない。

## 2026-09-09 精算金入力契約

DIVIDENDのPOST_EXIT_SETTLEMENTは投資由来の受取であり、Valuation.net_external_flow（利用者の外部入金−出金）に含めない。noteからの自動集計は未実装で、入力側が取得済み明細を照合して区別する。opsのcutover保留TRADE/CSVはpending_rowsに残り、runnerの既存の未解決判定で新規候補を停止する。provenanceなしの既存台帳へcutoverが自動設定されるわけではない。

## 2026-09-11 審査モジュールとの模擬接続

aitrader.review_runner.run_reviewed_mock を追加。生成済みProposalと明示された両judgeのstub記録を受け、同一render_packetをops.judges.reviewへ渡し、検証済みVerdictを既存runへ渡す。引数はrunのhome/run_id/execution_day/proposals/now/valuationに加え、responses、started_at、market_context。responsesは {proposal_id: {judge: {record: dict, received_at: aware datetime}}}。recordの形式は順序4仕様案のstdout/final_message/exit_code/elapsed_seconds。

モデル・CLI版はmock固定、transportはstub固定。欠けたjudgeを自動承認で補わない。未知judge・候補ID・未来受信時刻はログ・予約前に拒否。判定ログはDropbox外home/runs/<day>/<run_id>/reviewsへ保存し、failureとパケットを監査できる。ログ保存失敗ならrunへ進まない。台帳は従来のAPPROVEDまで、通知案はNOT_SENTのまま。

judge単体は07:15ちょうどを許すが、主系runnerは07:15以上を未完了とする既存の厳しい境界を維持。再呼出しは審査ログを追記するが、同じrun入力なら台帳予約は増えない。実CLI・通知器接続・市場取得の自動起動・完全なクラッシュ復旧は本追加の範囲外。market_contextと生の応答は審査ログに残るが、runnerの実行同一性は既存のProposalと正規化Verdict等に基づく。

追加test_review_runner.py 11件通過（6.41秒）。承認/拒否/棄権、欠損、timeout、malformed、hash不一致、未知judge、未来受信、締切、重複予約をオフライン検証。既存含む全体754 passed in 20.14s、skip/xfailなし。次工程は通知器との模擬結合と、台帳・outboxを跨ぐ復旧契約の整理。両見張りは無効化を維持し、Claudeへの公開なし。

## 2026-09-11 08:13 JST — 3並列レビュー統合

主担当＋サブエージェント3体（この環境の同時4枠）で実施。審査接続レビュー、通知結合設計、追加反証を分担した。

発見3点：判定reasonの秘密値がverdicts.jsonに再露出、同runで参照情報・審査開始時刻を変更できる、07:00前に完了した審査を受け入れる。修正は成果物保存時の再帰マスク（判定値やhash計算は変更しない）、審査前の入力hash耐久保存と専用ロック、開始時刻07:00下限。

review-inputs.sqliteはrun_idにpacket全文・生record・開始時刻・評価値等のhashを紐付け、変更を審査ログ追記前に拒否する。生の秘密値を入力台帳へ保存しない。review-lock.sqliteは主系接続呼出し同士を直列化。失敗時も入力紐付けは残り、変更入力での暗黙再開は拒否。既存runnerを直接呼ぶ別プロセスとの共通ロックではない。

追加反証21件（test_review_runner_edges.py）通過12.01秒。全ファイル確定後の全体 **775 passed in 22.77s**、skip/xfailなし。全通信・外部プロセス禁止fixtureで検証。途中の771件は追加4ケース確定前の集計で、最終数ではない。

通知結合の次工程は [NOTIFY_INTEGRATION_PLAN.md](NOTIFY_INTEGRATION_PLAN.md)。未決契約を含む設計のみで、通知器の結合や完全自動復旧を実装済みとは扱わない。Claude独立レビューではなくCodex内の並列検証。両見張りはDisabledを維持、公開・実通信なし。引き渡し不要。
