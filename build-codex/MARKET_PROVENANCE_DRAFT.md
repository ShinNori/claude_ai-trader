# 市場データ来歴 v1 実装案（未採用）

**この文書は未採用のproposalである。製品コード、既存DB、共通仕様を変更する許可ではない。** 対象は新規homeへ投入する`legacy-v1-unverified`固定fixtureだけであり、J-Quantsの現在仕様への適合、外部通信、実データ利用、売買準備完了を示さない。導入後も`ready_for_live=false`とする。

このproposal全体は未採用のままである。専用`provenance_fixture` modeとcanonical run-row JSON履歴を新規homeだけで検証する限定試作は、別文書[来歴付き固定fixtureの隔離技術試作](PROVENANCE_FIXTURE.md)を参照する。

## 固定する方針

- 新規home専用とし、旧schemaを自動移行しない。主要5表`listed`、`prices_daily`、`margin_weekly`、`index_daily`、`calendar`のschemaは変えない。
- 取得runと現在行の来歴を別表へ保存する。証拠の無い旧行を推測して補完しない。
- Adjustment Open/High/Low/Closeは4項目全有または全無だけを許可し、部分欠損が1行でもあればbatch全体を変更前に拒否する。
- OHLCは現行どおり全有なら調整値、全無ならraw値を使う。volume、turnover、factorの値と選択規則は変更せず、実際に選んだbasisだけを記録する。相互の調整基準は未検証と表示する。
- 信用残の公表日sourceを行ごとに`EXPLICIT`または`DATE_PLUS_4`で記録する。`DATE_PLUS_4`は研究用途だけに許容し、実データ適格性には使わない。
- 一回の成功transactionで主要5表、run履歴、run別行hash、現在行来歴を確定する。失敗は全rollbackし、DBへFAILED runを残さず元例外を返す。失敗をCOMPLETEDとして保存しない。

## schema version 1

### provenance marker

既存`provenance`へ次を保存する。型は文字列だが、値は完全一致で検査する。

| key | value |
|---|---|
| `market_provenance_schema` | `1` |
| `market_input_contract` | `legacy-v1-unverified-fixture` |

この2 keyが欠損、不正、重複相当、または別versionなら新入口は拒否する。既存`publication_estimated`を新しい意味へ転用しない。

### `market_ingest_runs`

| field | type / constraint |
|---|---|
| `run_id` | TEXT PRIMARY KEY、`mprov1:`＋64桁小文字hex |
| `schema_version` | INTEGER、厳密に1 |
| `contract` | TEXT、`legacy-v1-unverified-fixture` |
| `request_start` / `request_end` | DATE、start <= end |
| `normalized_input_sha256` | TEXT、64桁小文字hex |
| `recorded_at` | TIMESTAMPTZ、呼出側が渡したaware時刻 |
| `status` | TEXT、保存可能値は`COMPLETED`だけ |

FAILEDはこの表へ保存しない。失敗の監査が将来必要なら、市場データtransactionとは別の失敗台帳として新たに設計する。

### `market_ingest_run_rows`

append-onlyのrun内索引であり、削除しない。

| field | type / constraint |
|---|---|
| `run_id` | TEXT、`market_ingest_runs.run_id`参照 |
| `table_name` | TEXT、主要5表の限定enum |
| `row_key_json` | TEXT、後述canonical JSON |
| `normalized_row_sha256` | TEXT、64桁小文字hex |
| primary key | (`run_id`, `table_name`, `row_key_json`) |

### `market_row_provenance`

現在の主要表行と一対一に対応し、訂正時は主要行と同じtransactionで置換する。

| field | type / constraint |
|---|---|
| `table_name` | TEXT、主要5表の限定enum |
| `row_key_json` | TEXT、canonical JSON |
| `run_id` | TEXT、COMPLETED run参照 |
| `ohlc_basis` | `ADJUSTED` / `RAW` / NULL |
| `volume_basis` | `ADJUSTED` / `RAW` / `MISSING` / NULL |
| `turnover_basis` | `RAW` / `MISSING` / NULL |
| `factor_basis` | `PROVIDED` / `DEFAULT_ONE` / NULL |
| `publication_source` | `EXPLICIT` / `DATE_PLUS_4` / NULL |
| primary key | (`table_name`, `row_key_json`) |

価格行だけが4つのbasis列を持ち、公表日sourceはNULLとする。信用残行だけが`publication_source`を持ち、basis列はNULLとする。他3表は全詳細列をNULLとする。この形状をCHECKとinspectionの両方で検査する。

row keyは主要表の主キー値を配列にしたcanonical JSONとする。順序は`listed:[code]`、`prices_daily:[code,date]`、`margin_weekly:[code,date]`、`index_daily:[name,date]`、`calendar:[date]`。日付はISO `YYYY-MM-DD`、文字列は変換後の値を使う。

## 正規化hashとrun ID

1. 現行mappingと有限値検査を完了し、主要5表へ格納予定の値を作る。部分調整OHLCはこの段階でbatch拒否する。
2. envelopeを`schema_version`、`contract`、`request_start`、`request_end`、`tables`の固定keyで作る。各tableは主キー昇順のrow配列とする。rowはDB列順のobjectとし、DATEはISO文字列、boolはJSON bool、欠損はJSON null、有限数値だけをJSON numberにする。basisとpublication sourceも各rowへ含める。
3. UTF-8、`ensure_ascii=false`、key昇順、空白なし、NaN禁止のJSON bytesをSHA-256化し、`normalized_input_sha256`とする。
4. `run_id = "mprov1:" + normalized_input_sha256`とする。取得時刻、token、path、外部response本文はhashへ入れない。
5. 各rowも同じcanonical規則で、table名、row key、格納予定値、来歴enumをhash化してrun別索引へ保存する。

同じ`run_id`が既にCOMPLETEDで、contract、期間、input hash、全run-row索引が一致する再実行はDB変更なしのno-opとする。IDが同じで内容、期間、索引のどれかが異なる場合は衝突として拒否する。hash一致は署名、取得者証明、悪意ある改ざん対策ではない。

## 訂正とtransaction

内容が変わる訂正は新しいinput hashとrun IDになる。新runと全run-row索引を追記し、対象となる主要行と`market_row_provenance`を同時に置換する。過去の`market_ingest_runs`と`market_ingest_run_rows`は削除しない。主要表に残る現在値だけが現在行来歴へ対応し、過去値そのものはrun-row hashから復元できない。

処理順は次に固定する。

1. API引数、固定fixture、全row、部分OHLC、schema markerを副作用前に検査する。
2. `BEGIN`後にschema markerと同一runの有無を再確認する。
3. no-opでなければ主要5表、現在行来歴、run、run-row索引を同じtransactionへ書く。
4. 全主要行と現在行来歴の対応、run-row件数と計算済み索引を再照合する。
5. 成功時だけCOMMITする。例外時はROLLBACKし、rollback失敗で元例外を隠さない。

runはCOMPLETEDだけをtransaction内で保存するため、途中状態はDBに公開しない。プロセス停止やDB engine障害に対する電源断耐久性は別契約である。

## 提案API

```python
ingest_legacy_fixture_with_provenance(
    home,
    *,
    start: date,
    end: date,
    recorded_at: datetime,
    fixture: Mapping[str, list[Mapping[str, object]]],
) -> dict

inspect_market_provenance(home, *, as_of: date) -> dict
```

取込APIは通信clientやpathを受けず、呼出側が安全に固定した5 endpoint相当のmappingだけを受ける。戻り値は`run_id`、`normalized_input_sha256`、`result=COMPLETED|NO_OP`、表別件数に限定する。

このAPIは既存`fetch`、`run_signals`、packet生成、backtest、daily runnerへ自動接続しない。現行Cの研究変換、共通仕様、既存homeの動作を維持したまま、新規homeと独立fixtureだけで技術的なrun/row履歴を検証する入口とする。履歴が整合しても、外部データ方針の採用、入力の業務的正しさ、実運用適格性を判定しない。

inspectionはDBをread-onlyで開き、schema/version、全current rowとの一対一対応、COMPLETED run参照、run-row索引、enum形状、公表日sourceを確認する。旧home、marker欠損、孤立・欠落・余分な来歴、`DATE_PLUS_4`、basis間の未検証状態は安全側へ分類する。値、銘柄、候補を返さず、`ready_for_live=false`、`current_signal=false`、`read_only=true`を固定する。旧homeを自動作成、移行、修復しない。

## 受入シナリオ

1. 新規homeへ完全調整OHLCを取り込み、5表・run・全行来歴が一transactionで対応する。
2. 全調整OHLC不存在のraw rowを受け入れ、実際のvolume/turnover/factor basisを記録する。
3. 4調整OHLCの各部分欠損をbatch全体で拒否し、全表・全来歴を不変に保つ。
4. 明示公表日を`EXPLICIT`、欠損fallbackを`DATE_PLUS_4`として別rowに記録し、後者を研究用警告にする。
5. 同一fixture・期間の再実行を`NO_OP`とし、既存runやcurrent rowを変更しない。
6. 同一run IDに不一致の期間、hash、run-row索引があれば衝突拒否する。
7. 同一主キーの訂正で新runを追記し、主要current rowと現在行来歴だけを同時置換し、旧run索引を保持する。
8. 主要5表、current provenance、run、run-row索引の各書込点と最終照合点の例外で全rollbackし、FAILED/COMPLETEDを残さない。
9. schema marker欠損、旧version、旧`publication_estimated=False`だけのhomeを新取込・新inspectionの正常状態として扱わない。
10. 孤立、欠落、余分、別run参照、不正enum、不正hashをinspectionが検出し、部分要約やready判定を返さない。
11. sourceがsyntheticまたは未知のhomeを取込前とtransaction内再確認で拒否する。
12. hashのrow順不変性、数値・bool・nullの型分離、非有限値拒否を固定fixtureで確認する。

## 採用判断で残る一点

この案は実装形を一つに絞るが、調整basisの意味そのものは外部契約で未確認である。したがって採用しても「観測した選択を記録した」範囲に留め、volume、turnover、factorがOHLCと整合すると判定しない。現在の外部契約資料と非機密fixtureが別途確認されるまでは、実通信と`ready_for_live=true`を採用しない。
