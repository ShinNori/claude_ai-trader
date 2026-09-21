# managed mock STOP/RESUME CLI 操作契約

2026-09-11。対象は、`initialize_managed_mock` または日次 `--managed-stop` で明示作成した既存version 2 mock homeだけである。CLIは既存 `apply_managed_control` の入口であり、実通信、通知配信、注文、予約解除、STOPファイル操作、修復、見張りを行わない。

## 1. 必須入力と任意入力

CLIは `python -m aitrader.managed_control` とし、次を受け取る。

| 引数 | 必須 | 契約 |
|---|---:|---|
| `--home` | 必須 | Dropbox外にある既存managed version 2 mock home。新規作成、初期化、移行、修復はしない |
| `--action` | 必須 | `STOP` または `RESUME` の完全一致 |
| `--now` | 必須 | 操作を観測するtimezone付きISO 8601日時。実行PCの現在時刻を暗黙採用しない |
| `--settings` | 必須 | 初期化時と内容が完全一致するUTF-8 JSON object。固定settings hashを照合する |
| `--reconciled-at` | RESUME時のみ任意 | 既存Notifier契約に渡すtimezone付き照合時刻。省略時は照合不足により適用されない場合がある |
| `--event-at` | 任意 | 制御イベントの発生時刻。省略時は `--now`。未来値、managed clockより前の操作に使える値ではない |
| `--event-id` | 任意 | 既存制御履歴へ渡す明示ID。再送・遅着の結果は既存Notifier契約に従う |

settingsファイルのpath自体はhome外でよい。CLIはsettingsファイルをhomeへコピーしない。`stop_file`を含む場合、その値は初期化時と同じhome内絶対pathでなければ固定hashまたはpolicy検査に失敗する。recipient等の設定を都合よく削除・変更したファイルは使用できない。

## 2. 適用結果と終了コード

成功時も標準出力は機械可読JSON一件に限定し、`mode=mock`、action、result、観測時刻、限定した停止診断、`real_sent=false`を返す。秘密値、settings本文、絶対path、例外tracebackは出力しない。

| exit | result・状況 | 意味 |
|---:|---|---|
| `0` | `STOPPED` | STOP操作が既存制御契約で適用された |
| `0` | `RESUMED` | RESUME操作が既存制御契約で適用された。すべての停止源が消えた意味ではない |
| `2` | `RECONCILIATION_REQUIRED` | RESUMEに必要な照合条件が足りず、解除は適用されなかった |
| `2` | `IGNORED` | 遅着・既処理等として監査されたが現在状態は変更されなかった。正常な遅着監査であっても「操作適用成功」ではない |
| `2` | 入力・policy・clock・STOP状態の拒否 | action/日時/settings/homeが不正、固定policy不一致、未来・逆行時刻、状態UNKNOWN、初期化中、DB欠損・sidecar・読取失敗等 |

exit 2を自動再試行や自動RESUMEの合図にしない。標準出力のresultと停止診断を保存して人が確認する。UNKNOWNは停止なしへ補完せず、制御DBを作り直さない。

## 3. PowerShell準備例

次はpathと時刻を利用者が明示確認してから使う例である。`2026-09-11T...+09:00` は仮想リハーサル時刻の例で、実時計を読み取った値ではない。

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'

$MockHome = 'C:\mock-operations\managed-day-2026-09-11'
$SettingsPath = 'C:\mock-operations-config\managed-settings.json'
$Python = 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PYTHONPATH = "$PWD\ops;$PWD\build-codex;$PWD\common\tests\phase2"
```

初期化時に使ったsettingsと完全に同じJSONをhome外へUTF-8 BOMなしで準備する。次の内容は例であり、既存homeの固定settingsに合わせて置き換える。

```powershell
$SettingsJson = @'
{
  "line": {
    "allowed_user_id": "synthetic-managed-user",
    "monthly_budget": 10
  }
}
'@
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($SettingsPath, $SettingsJson, $Utf8NoBom)
```

この準備は制御を実行しない。settings JSONは、文字列表現ではなく解析後の内容が初期化時の固定settingsと一致する必要がある。

## 4. 明示STOP例

```powershell
$ObservedAt = '2026-09-11T07:05:00+09:00'  # 仮想リハーサル時刻
& $Python -m aitrader.managed_control `
  --home $MockHome `
  --action STOP `
  --now $ObservedAt `
  --settings $SettingsPath `
  --event-id 'operator-stop-20260911-0705'
$ExitCode = $LASTEXITCODE
```

実運用上の現在時刻として操作する場合は、別途確認したtimezone付き時刻を`--now`へ明示する。仮想時刻と実時計を同じ記録内で混用しない。STOP適用後も既存予約を取消・解放せず、完了済みrunを書き換えない。

## 5. 明示RESUME例

```powershell
$ObservedAt = '2026-09-11T07:10:00+09:00'   # 仮想リハーサル時刻
$ReconciledAt = '2026-09-11T07:10:00+09:00'
& $Python -m aitrader.managed_control `
  --home $MockHome `
  --action RESUME `
  --now $ObservedAt `
  --reconciled-at $ReconciledAt `
  --settings $SettingsPath `
  --event-id 'operator-resume-20260911-0710'
$ExitCode = $LASTEXITCODE
```

`--reconciled-at`は「未確認状態を照合した」という呼出側の明示値であり、CLIが照合を実施・証明するものではない。未来値や最新STOPより前の値で解除を成立させない。RESUMEが`RESUMED`でも、home/STOPまたは固定settingsの別STOPファイルが残れば実効停止は継続する。CLIはそれらを削除しない。

## 6. 時計と失敗時の境界

CLIは副作用前にmanaged clockを`--now`まで原子的置換する既存API契約を使う。RESUMEが`RECONCILIATION_REQUIRED`、またはNotifier操作後の例外となっても、進んだclockを巻き戻さない場合がある。この記録は電源断時の耐久保証ではない。次回に古い`--now`を使って成功へ戻そうとせず、現物を読取診断する。

制御前のSTOP状態がUNKNOWNなら適用を拒否する。通知DB、台帳、marker、clock、初期化flag、sidecar、固定settingsの問題をCLIが修復しない。STOP/RESUMEはNEWの許可、二審査、期限、余力、保有、予約、未確認約定を代替せず、実通信や模擬配信も開始しない。

## 深いJSONの拒否（2026-09-11）

設定・marker・clockのJSONが深すぎて解析できない場合も、終了2と固定エラーを返す。制御・台帳・通知・時計を更新せず、入力本文やtracebackを表示しない。初回操作では時計読取前に空の協調ロックDBが作られる場合があり、home内のファイル作成が一切ないという保証ではない。
