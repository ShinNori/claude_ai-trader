# 市場データ保存契約の選択肢

2026-09-11。価格調整列の部分欠損と信用残の推定公表日について、現在の契約と将来案を分けて記録する。現時点で採用しているのは選択肢Cだけである。選択肢A・Bは未採用であり、この文書だけを根拠に製品コード、既存DB、共通仕様を変更しない。

本書は現行の`legacy-v1-unverified`入力と固定fixtureを対象にした設計比較である。J-Quantsの現在のAPI、項目、調整方法、提供条件への適合を示さない。外部通信、実データ取得、実売買の許可でもない。

## 現在確認できる問題

現行取込は、Adjustment Open/High/Low/Closeの4項目がすべて存在するときだけ調整OHLCを使い、一つでも欠けると非調整OHLC一式を使う。出来高はこの判断と独立してAdjustmentVolumeが存在すれば採用し、turnoverとadjustment factorも別に保存する。このため、部分欠損ではOHLC、出来高、turnover、factorの基準が混在し得る。保存後の行には、どの基準を選んだかを示す履歴がない。

`publication_estimated`は、その取込で取得した信用残行のうち、公表日を基準日+4暦日で補った行が一つでもあるかを表す。値はprovenanceの同じkeyへ毎回上書きされる。推定行がDBに残っていても、後の取込がすべて明示公表日ならFalseへ変わる。したがってTrue/Falseのどちらも、現在保存されている全行の来歴を証明しない。

現在の市場入力検査が`ADJUSTMENT_BASIS_UNVERIFIED`と`PUBLICATION_ESTIMATE_HISTORY_UNVERIFIED`を返し、`ready_for_live=false`を維持するのはこの制約による。

## 選択肢C：現在の研究用互換を維持する（採用済み）

### 契約

- 調整OHLCが4項目すべて存在するときは調整OHLCを使い、それ以外は非調整OHLC一式を使う。
- AdjustmentVolume、turnover、adjustment factorは現コードの選択規則を維持する。基準が揃っているとは表明しない。
- 公表日が無い信用残行は基準日+4暦日を使う。`publication_estimated`は今回の取込batchについて計算し、従来どおり上書きする。
- inspectionは調整基準と推定公表日の履歴を未検証として警告する。警告を補正、候補承認、実データ準備完了へ変換しない。

### 訂正・再実行・rollback

- 同一主キーの再取込は従来の`INSERT OR REPLACE`を維持する。訂正後の値は残るが、訂正前の値や選択基準の履歴は残らない。
- 同じ入力の再実行も許容する。run IDや原本receiptが無いため、同一原本だったことはDBから証明できない。
- 5表とprovenanceの取込transactionは従来どおり一括でcommitし、取込例外時はrollbackする。通信前のDB初期化副作用まで無かったことは保証しない。

### 旧schemaと受入

- 現行schemaと既存homeをそのまま扱う。自動移行は行わない。
- 既存のfixture試験、反復取込、transaction rollback、source混在拒否が通ることを受入条件とする。
- 部分調整rowや推定公表日を含むDBは研究用互換として読めても、実データ適格性を満たしたとは扱わない。

### 限界

この選択肢は互換維持であり、二つの履歴問題を解決しない。部分調整をfield単位で採用することや、外部仕様に適合していることも意味しない。

## 選択肢A：保守的な取込拒否とversion付き集約履歴（未採用）

### 提案契約

- Adjustment Open/High/Low/Closeは「4項目すべて存在」または「4項目すべて不存在」だけを許可し、部分欠損rowはデータINSERT前に固定理由で取込全体を拒否する。
- OHLCとAdjustmentVolume、turnover、factorを同じ基準として扱える条件は、別途明示的に決める。決定前は、OHLCだけの拒否をもって調整基準全体が検証済みとは表示しない。
- 推定公表日の集約履歴は、既存`publication_estimated`の意味を黙って変更しない。新しいkeyとprovenance versionを導入し、「このDBで一度でもfallbackを観測したか」をFalseからTrueへだけ進む値として保存する。
- 新keyの欠損、旧version、既存の`publication_estimated=False`はUNKNOWNとする。Falseを「過去を含め推定なし」の証明にしない。

### 訂正・再実行・rollback

- 部分欠損検査は全responseを変換した後、取込transactionのデータ変更前に完了させる。不合格時は5表と新provenanceを変更しない。
- 同一行の訂正や再実行でも、ever-estimatedはTrueからFalseへ戻さない。推定行が明示公表日に訂正された事実を、この集約値だけでは表現しない。
- 新provenanceの更新は5表と同じtransactionに含める。途中失敗時はデータと履歴を共にrollbackする。

### 旧schemaと受入

- 旧DBを自動的に新versionへ変換しない。旧DBは研究用互換としてCで読むか、UNKNOWNとして新契約の取込を拒否するかを入口で明示する。
- 新契約を採用する場合は、新規homeを原則とする。既存homeを扱うには、別の明示移行仕様と受入が必要である。
- 受入では、4調整列の全有・全無・各部分欠損、複数row中の一件欠損、同一期間再実行、別期間追記、True後の明示日取込、変換・INSERT・provenance更新の各失敗点を固定fixtureで検証する。
- rollback後に5表と全provenanceが取込前と同一であること、旧Falseやkey欠損が安全側のUNKNOWNになることを確認する。

### 限界

ever-estimatedは「過去にfallbackを観測した」という保守的な集約であり、現在どの行が推定か、訂正で解消したかを示さない。調整volume/turnoverの基準も別の採用判断が必要である。

## 選択肢B：取得runと行単位の来歴を保存する（未採用）

### 提案契約

- 新規home専用のversioned schemaを作り、主要5表を変えずに取得run監査表と行来歴表を追加する。
- run監査には少なくともrun ID、契約識別子、要求期間、取得時刻、endpoint別件数、固定原本または正規化入力のhash、完了状態を保存する。秘密値、token、response本文を監査表示へ出さない。
- 信用残の行来歴は主キーに対応させ、公表日sourceを`EXPLICIT`、`DATE_PLUS_4`、`UNKNOWN`等の限定値で保存し、取得runへ関連付ける。
- 価格の行来歴はOHLC basis、volume basis、turnover basis、factorの有無を限定値で保存する。部分調整rowを拒否するか非調整一式へ揃えるかは、schema導入とは別に採用方針を固定する。
- 既存行の来歴を推測して埋めない。移行元に証拠が無い行はUNKNOWNとする。

### 訂正・再実行・rollback

- 同一主キーの訂正では、現在値と現在値に対応する来歴を同じtransactionで置換する。訂正前を保存するなら、保持期間と履歴主キーを先に決める。
- 同一原本の再実行は、同じrunを再利用するか新runとして記録するかを明示する。hashだけで悪意ある同一性や外部ownerを証明したとは扱わない。
- runを`RUNNING`から`COMPLETED`へする順序、例外時の`FAILED`記録、5表・行来歴・run状態のrollback境界を仕様化する。完了と偽装しないため、主要表だけcommitした状態を成功として表示しない。
- 取込後のinspectionは、対象行とrunの参照整合、限定値、件数、hashの有無を検査する。不一致やUNKNOWN行を自動修復しない。

### 旧schemaと受入

- 新規home限定を推奨する。既存homeは自動移行せず、versionなし・旧versionを新契約として開かない。
- 旧homeを残す場合はCの研究用互換として明示し、新しいready判定へ混ぜない。手動移行を将来採用する場合も、元行の公表日sourceや調整basisを推測せずUNKNOWNへ固定する。
- 受入では、新規初期化、完全調整・非調整・部分欠損、明示公表日・fallback、同一取込、訂正、再実行、run ID衝突、全書込失敗点、rollback、旧schema拒否、UNKNOWN移行行を固定fixtureで検証する。
- 取込成功後に、主要表の各対象行と行来歴が一対一で対応し、runがCOMPLETEDであり、inspectionが欠落・余分・別run参照を検出することを確認する。

### 採用前に決める事項

- 部分調整を全体拒否、非調整一式、係数補正のどれにするか。
- volume、turnover、factorをどのbasisへ揃え、欠損を拒否するか。
- 推定公表日を研究用途で許容する範囲。
- 原本の形式、hash対象、取得runの重複判定、訂正前履歴の保持期間。
- 新schema version、既存homeの読取期限、明示移行手順。

### 限界

行とrunの来歴を保存しても、外部response自体の正しさ、現行APIへの適合、取得主体、悪意ある全面改ざんを自動的に証明しない。固定fixtureと明示された契約版に対するオフライン受入を先に行う必要がある。

## 比較と推奨順序

| 選択肢 | 現在の状態 | 得られるもの | 残る主な制約 |
|---|---|---|---|
| C | 採用済み | 既存研究用fixtureとDBの互換 | 混在basisと推定履歴を解決しない |
| A | 未採用 | 部分OHLCの曖昧な取込を止め、過去のfallback観測を保守的に保持 | 行別来歴、訂正解消、volume/turnover基準は不明 |
| B | 未採用 | run・行単位で現在値の来歴を照合できる | schema・保持・訂正・移行の明示判断が必要 |

短期の次候補はAである。ただし新keyとversionを使い、新規homeで受け入れることを条件とする。長期の実データ適格性にはBを推奨する。Bの設計判断と固定fixtureが揃うまではCを維持し、既存inspection warningを消さない。

field単位で存在する調整値だけを使う案は、OHLCの基準や算術整合を崩す可能性があるため選択肢に採用しない。外部の契約資料と固定fixtureでその意味が確認され、別途明示採用される場合にだけ再検討する。

## 採用変更の共通ゲート

AまたはBを採用する変更は、次をすべて満たしてから行う。

1. 採用するbasis、公表日source、schema version、旧homeの扱いを文書で確定する。
2. 外部通信を使わない固定fixtureで正常、部分欠損、訂正、再実行、各失敗点のrollbackを先に受け入れる。
3. source混在拒否、単一取込transaction、候補生成の単一読取snapshotを維持する。
4. 旧schemaを新契約として黙って解釈せず、欠損・旧False・来歴不明をUNKNOWNにする。
5. 市場inspectionの警告を、新しい履歴が完全に照合できた範囲だけで変更する。警告消去を実運用、現在シグナル、売買承認へ転用しない。
6. 新規homeで検証し、既存homeの自動移行、自動修復、自動再取得を行わない。

