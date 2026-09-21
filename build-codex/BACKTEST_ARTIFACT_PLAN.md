# バックテスト成果物の安全な再公開契約

## 位置づけ

この契約は`backtest_artifacts.py`と`backtest.run`に限定採用済みである。共通フェーズ1仕様が定める公開API、同じ`out_dir`への反復実行、strategy/version別directory、および次の4成果物の名前と内容契約を維持する。

- `trades.csv`
- `equity.csv`
- `summary.json`
- `report.html`

目的は、再実行の生成または公開が失敗したときに、直前まで正常だった4成果物を保護することである。新しい世代番号や利用者による出力先変更は要求しない。

## 実装済みの公開手順

1. rawの`out_dir`からstrategy/version別の最終directoryを組み立てる。resolveでlinkを隠す前に、Dropboxを含むpath、親componentと最終directoryのsymlink・junction・Windows reparse、不正なfile/directory種別を拒否する。拒否時はdirectory作成や既存成果物の変更へ進まない。
2. 最終directory単位のwriter lockを排他的に確保する。既存lockがあれば処理を拒否し、自動で古いと判断して削除・上書き・再利用しない。lockは生成、公開、必要な復元が終わるまで保持する。
3. 最終directoryと同じ安全な保存境界内に、衝突しないunique stage directoryを排他的に作る。4成果物をすべてstage内へ生成し、各成果物が通常fileであり、期待した名前の4件が揃い、必要なserializationとreport生成が完了したことを確認する。この段階では既存4成果物を変更しない。
4. 公開直前に、既存の4成果物だけをunique backup directoryへ移す。4件以外のfileやsubdirectoryは移動、削除、上書きしない。対象が存在する場合は通常fileかつlink/reparseでないことを再確認する。
5. stage内の4成果物を最終名へ順に置換する。4件すべての公開が成功した後に正常完了とする。同じ`out_dir`への再実行では、この公開によって従来どおり同じ4つの最終pathが更新される。
6. 公開途中に例外が起きた場合は、今回すでに公開した新成果物を最終pathから退避し、backupに存在する旧成果物を元の名前へ復元する。再実行前に存在しなかった対象は、失敗後も最終pathへ新しい不完全成果物を残さない。
7. 復元が完了した場合も元の公開失敗を返す。復元自体が失敗した場合は、backupを削除せず保持し、通常成功とは異なる明示的な復元失敗を返す。元の公開失敗と復元失敗の両方を診断可能にし、自動再試行やbackupからの自動採用は行わない。
8. 正常公開後だけstageとbackupの後片付けを試みる。後片付け失敗を理由に未知の残骸を自動削除せず、次回は既存lock、stage、backupを自動解除・再利用しない。手動照合なしにどの世代を正しいと推測しない。

## pathと並行実行の境界

- 最終directory、lock、stage、backupおよび4つの最終targetは、raw pathの各既存componentを検査する。通常directoryを要求する箇所と通常fileを要求する箇所を区別する。
- 外部を指すlinkだけでなく、同じ出力directory内を指すlinkも拒否する。resolve後のpathだけをwriterへ渡して検査を迂回しない。
- lockはこの実装を使う協調writer間の排他である。既存lockの所有者生存や処理完了を時刻だけで推測せず、stale lockを自動解除しない。
- 成果物以外の既存fileは保持する。公開失敗時の復元対象も、今回backupした4成果物だけに限定する。
- 実行DB・ログ・結果本体をDropbox外へ置く既定に従い、Dropbox名を含む出力先は公開前に拒否する。禁止場所から安全な場所へ自動移動しない。

## 保証する範囲

- 4成果物の生成が完了する前は、旧4成果物を変更しない。
- 公開途中の通常例外では、旧成果物の復元を試みる。復元失敗時はbackupを保持して明示的に失敗する。
- 同じlock契約を使うwriter同士を排他する。
- 4成果物以外のfileを公開・復元対象に含めない。
- 公開APIの引数・戻り値、成果物名、同一`out_dir`への再実行、研究計算と表示内容を変更しない。

## 保証しない範囲

- 4つの別fileを読むreaderに対する世代全体の原子的な切替は保証しない。公開中のreaderは旧世代と新世代を混在して読む可能性がある。
- lockを無視する外部process、OS上のpath差替え、検査直後のswapを完全には排除しない。
- fileまたはdirectoryの`fsync`、OS書込cache、電源断後の耐久性は保証しない。
- backupの存在は正しい世代、owner、完了receiptまたは安全な自動復旧許可を証明しない。
- DB snapshot、取得原本、実績値の適格性、バックテスト計算の正しさはこの保存契約では証明しない。

## standalone reportとCLI export

- standaloneの`python -m aitrader report --results ...`も採用済みである。4成果物writerと同じ`.<folder>.publish.lock`を確保し、summaryをサイズ上限付きで安定読取して形式・非有限値を検査する。HTMLは同じ親の固有tempへ完成後、`report.html`一件を`os.replace`する。生成・読取・公開前の失敗では旧HTMLを維持する。Dropbox内の概要report再生成は従来どおり許容する。ただしCSVを読まず、summary・CSV・旧HTMLが同一世代であることを証明しない。
- CLIの外部向けcopyは`backtest_exports.py`へ接続済みである。Dropbox外のsourceとtargetの双方で同じpublish lockを一定順序で確保し、`summary.json`と`report.html`を固有stageへbyte単位で複写する。sourceとstageのfingerprint一致、targetの公開前不変を確認し、既存二件をbackup後に公開する。公開失敗では旧二件を復元し、復元失敗ではtarget lockとbackupを保持して`ExportRestoreError`を返す。raw親・出力先・summary/report targetのlink/reparse検査もCLI入口で行う。
- exportが証明するのは観測したsource二件と公開するtarget二件のbyte一致である。sourceのsummaryとreportが同じバックテスト世代から生成されたこと、4成果物receipt、CSV整合、計算原本は証明しない。複数fileを同時に読む非協調readerへの原子的な世代切替も保証しない。

## 検証状況

4成果物公開25件、CLI export17件、standalone report14件、rootの別process間lock実測1件、CLI path境界7件が通過した。正常な初回生成と同一出力先への再実行、生成失敗で旧成果物不変、公開失敗で復元、復元失敗でbackup・lock保持と明示例外、無関係file保持、同時writer拒否、既存lock非解除、Dropbox・親/target link/reparse・不正種別の副作用前拒否を対象にしている。reportではHTML escape、summary安定読取、shape・NaN/Infinity・サイズ上限も確認した。実通信、実注文は使用していない。

### 公開完了後のcleanup失敗（2026-09-11 追補）

共有2fileの置換が完了した後、backup内旧fileやstageの削除に失敗した場合は `ExportCleanupError` を返し、targetのpublish lockを保持する。これは公開失敗・復元失敗とは区別する。新2fileは公開済みで、旧backupは既に一部削除されている可能性があるため「旧pairへ復元済み」「旧backup全保持」と報告しない。残存folder/lockを自動除去せず、次の協調writerを止めて手動確認を要求する。

公開そのものの失敗時は、従来の旧pair復元と、復元不能時の`ExportRestoreError`を維持する。非協調writerや電源断の原子性を追加保証しない。

同じ公開後cleanup区別を4成果物writerにも適用する。`ArtifactCleanupError`では新4fileは公開済み、旧backupの完全性は保証せず、publish lockを保持して次writerを止める。通常の公開前・公開中失敗とは分けて診断する。
