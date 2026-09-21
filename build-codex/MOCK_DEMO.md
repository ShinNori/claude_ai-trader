# 合成デモの実行方法

合成の銘柄・審査応答を使って、候補作成から通知キュー登録まで確認できます。実データ取得、Claude/Codexの起動、LINE送信、実際の注文は行いません。見張りの起動も不要です。

## PowerShellで実行する

以下を同じPowerShellで実行してください。保存先はDropbox外の一時フォルダに毎回新しく作ります。既存の保存先は受け付けません。

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = "$PWD\ops;$PWD\build-codex"
$demoHome = Join-Path $env:TEMP ('ai-trader-doc-demo-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.mock_demo --home $demoHome --scenario approve
```

標準では通知キューへ登録するだけです。成功した場合の模擬送信まで確認するには、最後のコマンドの末尾へ `--simulate-delivery` を付けます。再実行時は `$demoHome` を作る行から実行して新しい保存先を指定してください。

```powershell
$demoHome = Join-Path $env:TEMP ('ai-trader-doc-demo-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.mock_demo --home $demoHome --scenario approve --simulate-delivery
```

`--scenario` は次の4種類です。デモの日付・時刻は2026年9月8日07:10 JSTに固定されています。

| 指定 | 審査の条件 | 通知案と予約 |
|---|---|---|
| `approve` | 両者承認 | 買い候補1件、予約100,200円 |
| `reject` | Codex側の模擬応答が拒否 | 状態通知1件、予約0円 |
| `missing` | Claude側の模擬応答が欠落 | 審査未完了の状態通知1件、予約0円 |
| `timeout` | Codex側の模擬応答が時間超過 | 承認条件未達の状態通知1件、予約0円 |

## 結果を見る

```powershell
Get-Content -LiteralPath (Join-Path $demoHome 'demo_summary.json') -Encoding utf8 | ConvertFrom-Json | Select-Object scenario, delivery, reserved, real_sent_by_this_call
Write-Output $demoHome
```

`demo_summary.json` が全体結果です。標準実行の `delivery` は `NOT_SENT`、模擬送信指定時は `SIMULATION_ONLY` です。模擬送信時も `real_sent_by_this_call` は `false` です。

詳細は保存先の `runs\2026-09-08\demo-approve\` 以下にあります。他のシナリオでは末尾が `demo-reject` などになります。

| ファイル | 確認する内容 |
|---|---|
| `result.json` | 審査・ゲート判定と候補の状態 |
| `notification_plan.json` | 固定した通知文面 |
| `notification_queue.json` | 登録した時点の未送信記録 |
| `mock_delivery.json` | 模擬送信を指定した場合の結果。`entries` の `SENT` は模擬成功 |

模擬送信処理は登録時の `notification_queue.json` を更新しません。そのため上記デモの模擬成功後も、登録記録や固定文面に `NOT_SENT`・「模擬・未送信」が残ります。通知キュー登録APIを別途再実行した場合は、その時点の観測へ更新されることがあります。`mock_delivery.json` は最後にその処理が記録した模擬送信結果であり、その後の台帳状態まで常に反映する表示ではありません。いずれも実送信や発注の証拠にはなりません。

途中で模擬成功の保存と台帳への反映の間に処理が中断した場合、専用API `reconcile_prepared_mock` で当該runの台帳反映だけを復旧できます。再送・再登録はせず、復旧結果を `mock_reconciliation.json` に保存します。このデモCLIは復旧処理を自動実行しません。

## 実行確認

2026年9月11日08:42 JST、このPCで上記の承認例に `--simulate-delivery` を付けて実行し、終了コード0、`SIMULATION_ONLY`、実送信なしを確認しました。実行先は `C:\Users\s\AppData\Local\Temp\ai-trader-doc-demo-20260911-084226-957` です。一時フォルダのため長期保存は保証しません。

## 共通合成市場から候補を実生成するデモ

前述の `mock_demo` は手作りのProposalで審査・通知を確認します。`synthetic_pipeline` は共通の合成市場データ300銘柄を読み込み、実際の戦略と `packet_cli.generate` でProposalを生成して、そのまま両模擬審査・ゲート・通知準備・キュー登録へ渡します。生成後に数量やhashを変更しません。

PowerShellの準備も含めて、次を同じ画面で実行できます。保存先は毎回未作成のDropbox外フォルダです。

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = "$PWD\ops;$PWD\build-codex"
$pipelineHome = Join-Path $env:TEMP ('ai-trader-synthetic-pipeline-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.synthetic_pipeline --home $pipelineHome --seed 42
Get-Content -LiteralPath (Join-Path $pipelineHome 'synthetic_pipeline_summary.json') -Encoding utf8 | ConvertFrom-Json | Select-Object source, seed, generated_count, status, reservation, delivery
Write-Output $pipelineHome
```

基準日は2026年8月28日、執行日は8月31日07:10 JSTに固定しています。初期資金100万円・保有なし、銘柄別予算25万円です。イベント情報は全銘柄について「決算予定なし・信用規制なし」、単元は100株という**明示的な合成設定**です。現実の銘柄を確認した情報ではありません。これらを生成前に `synthetic_events.json`・`synthetic_lots.json` へ保存し、出典とhashを `synthetic_metadata.json` および市場DBのprovenanceに記録します。共通合成データ本体は変更しません。

このPCのseed42実測は7.36秒で、次の結果でした。

| 項目 | 結果 |
|---|---|
| 戦略が選んだ候補 | 20件 |
| 一単元に必要な金額が予算を超えて除外 | 15件 |
| 実生成されたProposal | 5件 |
| ゲート承認 | 2件 |
| 日次新規上限による拒否 | 3件 |
| 予約額 | 478,856円 |
| 通知キュー | 2件、すべてPENDING |
| 配信 | NOT_SENT、実送信なし |

15件の「一単元未満の予算のため候補から除外」は既存の数量計算規則による警告で、処理エラーではありません。候補を増やすための予算変更や最適化は行いません。seedやデータによって候補なし・全拒否・部分承認になる場合も、その結果を保持します。

生成原本は `generated_proposals.json`、全体結果は `synthetic_pipeline_summary.json`、個別の審査・ゲート・キューは `runs\2026-08-31\synthetic-pipeline\` に保存します。全候補の両審査APPROVEは合成応答であり、実LLMによる判断ではありません。このCLIには模擬送信オプションもなく、登録までで終了します。

これは合成市場で生成器から通知準備までつながることの確認です。候補価格の基準日一致などの入力検証は実装されていますが、実データの取得から鮮度確認までの運用、実イベント情報、実審査、LINE送信、実発注への対応が完成したという意味ではありません。最新の検証結果は [README](README.md) を参照してください。

## 模擬シグナルを画面用ファイルにする

上の合成市場デモを実行した同じPowerShellで、次を実行します。診断が完了と判定した準備履歴から、承認候補の銘柄・方向・株数・指値と見送り件数を表示します。却下候補の注文条件は表示しません。

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$reportHtml = & 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.mock_report --home $pipelineHome --run-id synthetic-pipeline --day 2026-08-31
if ($LASTEXITCODE -eq 0) {
    $reportPath = Join-Path $pipelineHome 'signal_preview.html'
    $reportHtml | Set-Content -LiteralPath $reportPath -Encoding utf8
    Write-Output $reportPath
}
```

出力されたHTMLを開いて確認できます。外部通信・送信・注文機能はありません。書込中のDBや原本不一致では、候補を表示せず照合が必要と表示します。これは現在有効な売買指示ではなく、常に模擬の過去記録です。配信状況は検証しないため、準備時点のNOT_SENTを現在の未送信状態と解釈しないでください。

2026年9月11日、共通合成市場から画面を生成し、承認2件・見送り3件を確認しました。独立表示試験7件も通過しています。実行時ファイルは一時フォルダにあり、長期保存は保証しません。

## 仮想時計で日次の異常ケースを確認する

`daily_rehearsal` は06:30の入力確認、06:50の実候補生成、07:00の模擬審査、07:15の締切を仮想時刻で接続します。実時刻を待つ機能や見張りは起動しません。新しい専用フォルダで一回実行し、通知のキュー登録まで行います。

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = "$PWD\ops;$PWD\build-codex"
$rehearsalHome = Join-Path $env:TEMP ('ai-trader-daily-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.daily_rehearsal --home $rehearsalHome --scenario normal --seed 42
Get-Content -LiteralPath (Join-Path $rehearsalHome 'daily_rehearsal.json') -Encoding utf8 | ConvertFrom-Json | Select-Object scenario, status, generated_count, reservation, delivery, virtual_clock
Write-Output $rehearsalHome
```

`--scenario` は `normal`、`data_missing`、`holiday`、`judge_missing`、`deadline`、`stop_before_review` から選べます。各回で新しい保存先を使います。欠損は専用DBへの故障注入で、共通データを変更しません。データ欠損は `DATA_INCOMPLETE`、確認済み休日は `NO_SESSION` と区別し、審査や予約へ進みません。審査欠落・締切では予約を作らず状態通知だけを準備します。STOPのケースはstub審査後にゲートでNEWを拒否し、STOPを残します。

同じ保存先の再実行は拒否します。途中で例外終了したフォルダを自動修復・削除する機能はありません。完了済み結果の再検証や途中再開の契約は [日次リハーサル設計](DAILY_REHEARSAL_PLAN.md) の次段階の提案であり、このCLIには実装していません。

`daily_progress.json`には最後に入った処理段階を保存します。`COMPLETED`でも`result_status`が`DATA_INCOMPLETE`なら入力不足で終了した意味です。`FAILED`は例外終了、`RUNNING`のままなら実行中または記録できない形で中断した可能性があります。例外本文は保存せず型名だけを残します。保存自体の失敗や電源断では最新記録が残らない場合があり、このファイルだけで再実行・予約解除を判断しないでください。

```powershell
Get-Content -LiteralPath (Join-Path $rehearsalHome 'daily_progress.json') -Encoding utf8 | ConvertFrom-Json | Select-Object status, current_stage, checkpoint_seq, result_status, failed_stage, error_type
```

## 完了した日次履歴を再実行せず確認する

日次リハーサルの完了索引`daily_receipt.json`がある保存先は、次のコマンドで読み取りだけの照合ができます。同じPowerShellで前節の`$rehearsalHome`を指定します。

```powershell
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.daily_receipt inspect --home $rehearsalHome
```

`VERIFIED_HISTORY`は保存時の履歴との一致を示します。現在有効なシグナルではなく、`current_signal`と`auto_resume`は常にfalseです。台帳・通知キュー・原本・進捗などに変更がある場合や、ファイルの追加・欠落がある場合は`NEEDS_RECONCILIATION`になり、保存済み要約を返しません。DBの作成や修復、再審査、通知再登録は行いません。

この索引の導入前に作ったhomeには後付けしません。既存homeを変更せず、必要な検証は新しい専用homeで実行します。確認後に作るHTMLなどは別フォルダへ保存してください。対象homeへ追加するとファイル集合が変わり、次の確認は保留になります。

## 停止状態を読み取って確認する

前節で日次リハーサルを実行した同じPowerShellで、その`$rehearsalHome`を確認します。ファイルの作成、停止解除、配信は行いません。

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = "$PWD\ops;$PWD\build-codex"
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.stop_diagnostics --state (Join-Path $rehearsalHome 'notification.sqlite') --now (Get-Date).ToString('o') --stop-file (Join-Path $rehearsalHome 'STOP')
```

`CLEAR`は調べた情報源に停止がない状態、`STOPPED`は停止を確認した状態です。`UNKNOWN`はDB欠損、稼働中のDBファイル、不正履歴、読取失敗などで確定できない状態です。UNKNOWNの場合、`known=false`、`effective_stop=true`、終了コード2を返します。正常に診断できたCLEAR/STOPPEDの終了コードは0です。

休日や取得失敗で通知DBをまだ作っていないhomeもUNKNOWNになります。この診断でDBを補完することはありません。別の停止ファイルも使っている場合は`--stop-file`を追加指定してください。指定していない停止ファイルや任意のcallbackの状態は調べません。CLEARは売買承認ではなく、今回のAPIは主系の予約前判定にはまだ接続していません。

2026年9月11日10:15 JST、このPCの既存模擬通知DBでCLEAR、DBのSHA256不変を確認しました。10:16 JSTには不存在DBでUNKNOWN・終了コード2・保存先未作成も確認しました。

## 管理STOP API（新規の模擬環境限定）

停止管理付き環境を選ぶ開発用APIを追加した。通常の日次CLIの既定動作は変わらない。[契約](STOP_INTEGRATION_PLAN.md)に従い、未作成かつDropbox外のhomeでinitialize_managed_mockを呼ぶ。settingsには明示的な模擬通知先を指定し、transportは常にstubである。

PowerShellで作業場所へ移動する場合:

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONPATH = "$PWD\ops;$PWD\build-codex"
$env:PYTHONDONTWRITEBYTECODE = '1'
```

Python側の入口はaitrader.managed_stopのinitialize_managed_mock(home, cash, positions, at, settings=settings)、inspect_managed_stop(home, now=now)、apply_managed_control(home, action='STOP'または'RESUME', now=now, settings=settings, reconciled_at=照合時刻)である。時刻にはタイムゾーンが必要。初期化は市場データを生成しない。候補をrunnerへ渡すには別途既存の市場DBと模擬Verdictが必要である。

RESUMEの結果がRESUMEDでもSTOPファイルが残れば実効停止は続く。UNKNOWNは新規予約を許可しない。既存homeを初期化し直したり、clockやflagを消して復旧してはいけない。現段階のAPI呼出は開発用で、実運用の操作画面や自動解除ではない。

## 停止管理付き日次リハーサル（明示選択）

新しく作る模擬環境でのみ、停止管理付きの日次経路を選択できる。下記は合成データと模擬判定だけを使用し、通知キュー登録まで行う。実送信しない。

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONPATH = "$PWD\ops;$PWD\build-codex"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$demoHome = Join-Path $env:TEMP ('ai-trader-managed-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.daily_rehearsal --home $demoHome --scenario normal --managed-stop
```

審査前の永続停止を再現する場合は、別の新規homeで `--scenario stop_before_review --managed-stop` を指定する。既存homeへ再実行しない。省略時の従来方式は維持する。

完了履歴の確認:

```powershell
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.daily_receipt inspect --home $demoHome
```

VERIFIED_HISTORYは保存時の履歴一致を示す。現在の承認や再開許可ではない。後からSTOP操作・模擬配信でファイルが変わると履歴照合は保留になる。[停止契約](STOP_INTEGRATION_PLAN.md)を参照。

## 状態表示（読取専用）

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONPATH = "$PWD\ops;$PWD\build-codex"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$observedAt = (Get-Date).ToString('o')
# $demoHome は既に実行した模擬homeを指定する
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.operations_status --home $demoHome --now $observedAt
$viewFile = Join-Path $env:TEMP 'ai-trader-operations-status.html'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.operations_view --home $demoHome --now $observedAt | Set-Content -LiteralPath $viewFile -Encoding utf8
```

HTMLは元のhomeの外へ保存する。元homeへ入れると封印済みファイル集合を変更し、履歴照合が保留になる。表示された予約金額は保存時点の模擬値である。STOP解除・予約解除・再実行を自動で行わない。legacy環境では永続停止の統合が未管理なので停止状態UNKNOWNと表示する。

## 模擬制御の明示CLI

[managed_controlの操作手順](MANAGED_CONTROL_CLI.md)を参照。これは既存管理APIの明示入口であり、自動再開や実制御認証の完成ではない。コマンド終了後は結果のstop_statusを読み、RESUMEDでもSTOPファイルが残る場合は停止継続と解釈する。
