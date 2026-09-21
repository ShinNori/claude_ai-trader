# V2仮レスポンスのファイル検査

この操作は保存済みJSONのローカル形状だけを検査する。認証・取得・DB作成・シグナル生成・送信は行わない。個人向けV2の公開資料との差は[J-Quants公開資料レビュー](JQUANTS_PUBLIC_DOC_REVIEW.md)を参照する。

## PowerShellで実行する

次は架空の営業日データをTempに作って検査する例。実際の営業日や売買判断を示すものではない。検査CLI自体はファイルを変更しない。

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONPATH = "$PWD\build-codex;$PWD\ops"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$fixturePath = Join-Path $env:TEMP ('ai-trader-v2-fixture-' + [guid]::NewGuid().ToString() + '.json')
'{"data":[{"Date":"2026-08-31","HolDiv":"1"}]}' | Set-Content -LiteralPath $fixturePath -Encoding utf8
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.jquants_v2_inspection_cli --dataset calendar --input $fixturePath
```

別PCではPythonの実行パスをそのPCの環境へ合わせる。`--dataset`は`prices / margin / master / topix / calendar`。`--input`にはUTF-8の通常JSONファイルを指定する。上限1MiB、link/reparse・重複キー・非有限数値・読取中に検出した変化は拒否する。

## 出力の意味

終了0はローカル形状の通過、終了2は検査不能または不適合。結果は`contract=v2-shape-only`、dataset、row_count、has_more、read_only、ready_for_live、fixture_only、current_signalだけを返す。行の内容やページングキーは出力しない。エラーは固定文で、入力の値やパスを表示しない。

`ready_for_live=false`、`current_signal=false`を常に維持する。空配列や数値NULLも形状としては通る。`has_more=true`は次ページの存在を示す文字列が入力にあったという意味だけで、次ページを取得・確認しない。

## 制限

公式の全列・プラン・実レスポンスへの適合確認ではない。公表完了時点、価格調整、単元、上場日、期間・銘柄の充足、経済的妥当性、ページ間の重複や完全性は判定しない。未知の追加列は許容し、数値から補正値を合成しない。

検査中のファイル観測は非協調プロセスの変更を完全に原子化しない。検査を通ったことを既存取込・来歴fixture・売買承認へ自動接続しない。
