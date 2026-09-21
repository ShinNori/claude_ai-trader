# 市場DBの読取検査

2026-09-11。既存市場DBの保存状態を確認するための検査で、売買シグナルを生成しない。実データ取得・通知・発注・自動修復は行わない。

## PowerShellで実行

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONPATH = "$PWD\build-codex"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.market_inspection_cli --home 'C:\Users\s\AppData\Local\Temp\ai-trader-market-demo-20260911-1243' --as-of 2026-08-31
```

上記homeは今回作った最小の人工データ例であり、共通合成データ全体や実口座を表さない。他の既存DBを確認する場合はhomeと基準日を変更する。homeにmarket.duckdbが必要。ファイルを作るために検査を実行することはできない。

## 結果の意味

| 項目 | 解釈 |
|---|---|
| INSPECTED / 終了コード0 | 限定した検査を完了。警告が残っていても0となる。運用可能の意味ではない |
| UNKNOWN / 終了コード2 | 読取・構造・観測を確認できず、部分集計を表示しない |
| source | 保存された出所の限定分類。synthetic / jquants_legacy_unverified / UNKNOWN |
| tables | 5表の全保存行件数と日付範囲。SAVED_ALL_ROWSは基準日以前だけの件数ではない |
| as_of | 呼出側が指定した検査基準日。実時計の証明ではない |
| observation_unchanged | 読取前後でDBが一致。原子的なsnapshotを得た保証ではない |
| current_signal / ready_for_live | 常にfalse |

価格・銘柄・注文内容・秘密設定を出力しない。表の期間が揃っていても、必要銘柄が全てあること、公開時点が正確なこと、元データの完全性は証明しない。

## 主な警告

| 警告 | 確認する内容 |
|---|---|
| REQUIRED_TABLE_EMPTY / REQUIRED_VALUE_MISSING | 空の必須表・欠損値 |
| NONFINITE_NUMERIC_PRESENT / NONPOSITIVE_PRICE_PRESENT | 無限値や非正の価格 |
| OHLC_RANGE_INVALID | 始値・終値・高値・安値の大小関係 |
| FUTURE_MARKET_ROWS | 基準日より先の市場データが保存されている |
| AS_OF_PRICE_MISSING | 基準日当日の価格が無い |
| CALENDAR_AS_OF_MISSING / CALENDAR_NEXT_SESSION_MISSING | 基準日・次営業日のカレンダー不足 |
| PROVENANCE_INCOMPLETE | 出所の記録が不足・矛盾している |
| PUBLICATION_ESTIMATE_HISTORY_UNVERIFIED | 過去の推定公表日の有無は保存flagだけで断定できない |
| ADJUSTMENT_BASIS_UNVERIFIED / LOT_SIZE_UNVERIFIED / EVENT_INPUT_UNVERIFIED | 価格調整・単元・イベント原本はこの検査だけでは確認できない |

未検証警告を消すためのデータ補完は行わない。今回の人工例はseedを保存していないのでPROVENANCE_INCOMPLETEも表示する。実データ適格性については[残契約](DATA_INPUT_READINESS.md)を参照する。

## 読取の境界

Dropbox内・親/対象リンク・reparse・sidecar・非通常DBを拒否する。DuckDBのread_onlyと外部アクセス/拡張自動読込の無効化を接続時に指定する。必須6表のBASE TABLE・列型・主キーを検査してから集計する。同名VIEWを実行しない。全DBの前後hash/識別情報/サイズ/更新時刻を照合し、変化はUNKNOWNとする。

外部プロセスによる全ての同時変更を排他しているわけではない。DBは閉じた状態で確認すること。検査出力はstdoutのみ。保存する場合は元homeの外へ保存する。
