# バックテスト保存結果の読取診断契約

## 目的と位置づけ

`inspect_backtest_results(folder)`は、指定したバックテスト結果directoryを変更せず、4成果物と公開処理の残骸を限定観測するAPIである。成果物publisher、CLI export、standalone reportが復元不能時などに残したlock、stage、backupを見つけても、削除、解除、採用、復元、再公開を行わない。

この診断は保存fileの観測であり、売買承認、現在シグナル、実データ適格性、成果物生成receiptまたは安全な自動再開許可ではない。概要copy先に`summary.json`と`report.html`だけがある場合は、4成果物診断上の`INCOMPLETE`である。完全なバックテスト結果を調べるときは、`trades.csv`と`equity.csv`も保存するDropbox外のruntime result directoryを指定する。

## 公開APIと共通フラグ

```python
inspect_backtest_results(folder) -> dict
```

すべての返却状態で次を固定する。

```text
read_only: true
ready_for_live: false
automatic_resume_allowed: false
generation_verified: false
observation_atomic: false
```

`observation_unchanged=true`は、この呼出しが行った限定的な反復観測で対象が同じだったことだけを示す。OS上の外部swapを排除したこと、返却後も同じであること、4fileが一つの生成runから出たことを意味しない。

## 状態

| status | reason | 確認できたこと | 表示しない・実行しないこと |
|---|---|---|---|
| `OBSERVED` | `FOUR_ARTIFACTS_OBSERVED` | `trades.csv`、`equity.csv`、`summary.json`、`report.html`の4件を通常fileとして反復読取でき、各sizeとSHA-256が観測内で一致し、既知の公開残骸を観測しなかった | 同一世代、内容・CSV整合、summary形式、計算正当性、現在有効、再開可能とは表示しない |
| `INCOMPLETE` | `REQUIRED_ARTIFACT_MISSING` | 既知残骸は無いが、4成果物の一部が欠損している | 観測できた一部fileのhashを返さず、二file概要copyを完全結果へ格上げしない |
| `REVIEW_REQUIRED` | `PUBLICATION_RESIDUE_PRESENT` | 対象名に対応するpublish lock、stage、backup、export-stage、export-backupの少なくとも一つが存在し、限定した再観測中はその集合が変化しなかった | lockが稼働中かstaleか、backupが旧正本か、どの処理がownerかを推測しない。成果物を要約せず、自動削除・解除・復元しない |
| `UNKNOWN` | 固定reason | folder欠損、安全に読めないpath、観測途中の変更などにより上記分類を安全に確定できなかった | 部分fileのhash、途中で見た残骸、成果物内容を返さず、安全・欠損・停止なしへ補完しない |

## 読取境界

- raw absolute pathの既存親component、結果directory、4成果物、既知残骸について、通常file/directoryの種別とsymlink・junction・Windows reparseを検査する。link先が同じdirectory内でも許可しない。結果directoryのdev/ino/mode identityを初回に保持し、通常・残骸の両経路で返却前に親guardとidentityを再確認する。
- 4成果物は1件64 MiBを上限に、open前の`lstat`、open handleの`fstat`、読取後の`lstat`とsize・mtime・identityを照合し、SHA-256を計算する。同じ4件を二度観測し、不一致では`UNKNOWN`とする。
- 親directoryの列挙は5000 entryを上限とし、既知の対象名だけを分類する。残骸directoryの内部には入らず、内容を正しい世代の根拠にしない。
- 残骸が一つでもあれば4成果物の内容観測より先に`REVIEW_REQUIRED`とし、残骸数だけを返す。pathや内部file、ownerらしい値は返さない。
- Dropbox内の概要copyもread-only観測自体は拒否しない。ただし4件不足なら`INCOMPLETE`であり、runtime結果として扱わない。
- DB、ネットワーク、実AI、通知、注文、writer lock取得を行わず、fileやdirectoryを作成しない。

## 観測の限界

- 4つのbyteが同時点の一括snapshotであること、publisherが生成した正規の組合せであることを証明するreceiptは無い。各fileを順に読むため、反復観測の間で変更して元のbyteへ戻る操作も完全には検出できない。
- lockの存在だけでは処理中、異常終了、staleを区別できない。時刻や名称から自動解除を判断しない。
- backupやstageの存在・件数だけでは復元元、owner、生成完了、採用可否を証明しない。手動照合前に移動・削除・再利用しない。
- OS ACL、外部process、lock非協調writer、検査直後のpath差替えを排他しない。`fsync`、電源断耐久性、返却後の不変も保証しない。

## CLI

```powershell
python -m aitrader.backtest_inspection_cli --results PATH
```

CLIは診断dictだけをJSONで標準出力する。`OBSERVED`だけ終了code 0、`INCOMPLETE`、`REVIEW_REQUIRED`、`UNKNOWN`は終了code 2とする。操作ボタン、lock解除、復元、再生成、外部通信は行わない。終了code 0も売買承認や運用準備完了を意味しない。

## 検証状況

診断moduleの独立試験18件とCLI試験5件が通過した。さらにDropbox外のTempに置いた正常4成果物へ実CLIを実行し、終了code 0、`OBSERVED`、`read_only=true`、`ready_for_live=false`、`automatic_resume_allowed=false`、`generation_verified=false`、`observation_atomic=false`を確認した。

診断moduleとCLIの追加は、直前に記録された全体`1642 passed / 9 skipped`の後である。この全体件数へ診断試験や後続runner試験を足して「全体通過」とは表記しない。共通8件を含む最終対象100件の実行結果は、完了した実測記録と別に照合する。

## PowerShellでの移動と実行例

次は今回作成したTemp上の試験用4成果物を読む例であり、実市場の成績ではない。Tempが削除済みなら、保存してある完全な結果folderへ `--results` を変更する。

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONPATH = "$PWD\ops;$PWD\build-codex"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.backtest_inspection_cli --results 'C:\Users\s\AppData\Local\Temp\ai-trader-process-lock-20260911\test_result_lock_is_exclusive_0\result'
```

最終対象試験（共通受入8件を含む）は100 passed、17.44秒。この診断を追加する前の全体1642件とは検証範囲を分けて扱う。

## 公開後の後片付けエラーが出た場合

`ArtifactCleanupError`または`ExportCleanupError`は、新しい成果物の公開後に後片付けが完了しなかったことを示す。通常の公開失敗や「旧結果へ復元済み」とは区別する。旧backupは部分的に削除済みの可能性がある。

1. 同じ出力先へ再実行せず、エラーと対象folderを記録する。
2. 上記の読取CLIで対象folderを検査する。保持されたpublish lock等が安定して観測できれば`REVIEW_REQUIRED`、読取不能や観測中変化なら`UNKNOWN`になる。
3. `REVIEW_REQUIRED`は稼働中・異常終了・staleを判別しない。成果物内容を読まず、残存件数と固定flagを返す。終了code 2はこの状況では想定された停止である。
4. lock・stage・backupを自動削除しない。内容やowner、旧世代の完全性を確認できていない状態で、手動の削除・復元コマンドも実行しない。

この手順は状況確認までであり、再開許可を与えない。共有先のsummary/reportだけを正常に書き出した場合も、4成果物検査では`INCOMPLETE`になる。2file共有出力を4fileの完全結果と誤認しない。

初期の全体1642件／対象100件という上の記録は履歴である。診断本体とcleanup補強を含む後続全体1885件の結果は[README](README.md)に保存している。

診断JSONには元の例外型は保存されない。後から`REVIEW_REQUIRED`だけを見てcleanup失敗と断定せず、元のエラー記録と区別して扱う。
