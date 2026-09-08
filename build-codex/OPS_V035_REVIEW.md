# ops v0.3.5 独立レビュー（第9回）

2026-09-09 Codex。確認開始05:58 JST。ops/は読取のみ、既存テスト・共通仕様本文は変更していない。

## 結論

**要修正。381件中378通過・3失敗（10.97秒）、skip/xfailなし。** 既存364件は全通過を維持。build-codex/tests/test_ops_v035_review.pyへ追加17件、14通過・3失敗。全体を全件通過とは扱わない。

K06のDISCARD記憶・再送・監査優先順位・ID衝突の畳み込みは追加試験でも通過。一方、新たに明記された「SNAPSHOT前replayは空」が後着保留行で破れ、保留と解決の業務時刻が逆順の場合にもreplayが例外になる。残高の二重計上や永続DBの破損は今回再現していない。

## 失敗3ケースと修正依頼

### L02（2ケース、A：採用済み契約違反）

provenance付きSNAPSHOTを08:00に作成。06:00のTRADEまたはCSV_FILLを後から取り込むと、cutover前なので正常にPENDINGとして記録される。その後07:00のreplayを要求すると、SNAPSHOTは時刻フィルタで除外される一方、保留の元イベントは再生対象になり、LedgerNotInitializedが漏れる。

要求された契約(c)は「SNAPSHOT前なら空」。実装は履歴が単純な場合だけ成立しており、正当な後着保留を含むと成立しない。ops/aitrader_ops/ledger.pyのreplay（SNAPSHOTの除外とstate.applyの組合せ）を修正し、開始残高前はcash=0・保有/予約なしを一貫して返す必要がある。旧台帳の残高を混入させない。試験はTRADE/CSVの2経路で固定済み。

### L03（1ケース、B：受理可能な日時関係と再生契約の隙間）

通常台帳で10:00を業務時刻とするCSVを、通知不明のため保留。その保留を08:00でDISCARDする操作は公開APIが受理する。09:00のreplayではCSVだけが未来として除外され、DISCARDが対象保留なしで実行されて「保留行 csv1 がありません」となる。

通常の現在残高・再起動は壊れていないが、受理済み履歴の時点再生ができない。解決時刻を保留の業務時刻より前にできるかは未定義なので、Aと断定せずBとして契約判断を依頼する。候補は、入力時にこの日時関係を拒否するか、時点再生で保留の非金銭的参照を補い、残高を先取りせず解決を再現すること。単に「保留がないなら全て無視」で不正履歴を隠さない。入力拒否を採用する場合は現試験の受理前提を更新する必要があるため、Claudeは契約案・実測を記録し、Codex所有試験は変更しない。

## 追加17ケースの結果

| ID | 件数 | 結果と確認内容 |
|---|---:|---|
| L01 | 4 | 通過。TRADE/CSV双方のDISCARD後に同種・別種で再送、別ハンドル同期、再起動。繰返しDISCARD/APPLYはLedgerErrorでイベント増加なし。時点/記録順の残高一致 |
| L02 | 2 | 失敗。後着した境界前PENDINGがあるとSNAPSHOT前replayが初期化例外 |
| L03 | 1 | 失敗。保留の業務時刻より早いDISCARDを含む時点再生で参照欠落 |
| L04 | 2 | 通過。故障注入で生SQLにより既存ADJUSTにtrade:/csv:の衝突IDを保存。公開APIへerrorとして返り、失敗分はロールバック、CSV同一バッチの後続行/TRADEの次報告を適用 |
| L05 | 3 | 通過。半角空白・全角空白・改行で囲んだ精算IDを既存IDと同一視し拒否 |
| L06 | 3 | 通過。DEPOSIT/WITHDRAW/FEEへの精算categoryは拒否、現金・履歴不変 |
| L07 | 1 | 通過。cutoverとSNAPSHOTの同一瞬間を別UTCオフセットで指定すると受理、1マイクロ秒先は拒否 |
| L08 | 1 | 通過。全量売却→再購入後のPOST_EXIT精算は拒否し、保有中FRACTIONAL_CASHOUTは受理、株数不変 |

L04の生SQLは合成テストDBでの故障注入のみ。本番運用の生SQL許可ではない。

## 監査・重複・精算の所見

- DISCARDの同内容再送はoutcome=DUPLICATE / reason_code=DISCARDED。公開フィールド変更はID_PAYLOAD_CONFLICTが優先。TRADE↔CSVの交差再送も同じIDとして止まり、経路ごとのpayload差があるためCONFLICTになる。DISCARD前の保留中再送は通常のDUPLICATEと区別される実装。
- report/import_csv_fillsの履歴INSERTでのIntegrityErrorはLedgerErrorへ変換され、後続を処理できた。ただしその拒否について独立のREJECTED監査を追加する処理はない。今回の「バッチ継続・ロールバック」は満たすが、監査完全性の追加契約候補として残す。
- 引き渡しBの「全角空白・改行はstrip対象外＝現状」は事実と異なる。実装は引数なしのstr.strip()で、全角空白・改行を含む前後空白を除去する。L05で実測済み。改訂案には採用済みのstrip契約を正確に記し、対象外という誤記は継承しない。文字列内部の空白やUnicode正規化までは行わない。
- 全量売却後でも再購入で保有中ならPOST_EXIT精算を拒否するのは採用契約どおり。過去の売却に由来する精算を自動識別して例外受理はしない。FRACTIONAL_CASHOUTとの同一証憑を跨ぐ重複防止は別問題で、自由文側に精算キーを持たない既存の制限を解消したとは扱わない。

## 改訂案への反映

共通仕様_フェーズ2_改訂案_v1.1.mdに第9回追記として(a)cutover<=SNAPSHOT.at、(b)精算scope3点、(c)開始残高前replayは空、(d)DISCARD再送DUPLICATE/DISCARDEDとCONFLICT優先を反映した。これは採用契約の反映であり、L02が示す未達も併記する。共通仕様本文は未変更。旧OPS_V034_REPORTの再保留記述は履歴として残し、今回採用契約が優先する。

## 実行・保全

ops/から指定対象 `python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q` を実行。PCでは既存Python＋依存探索パスを指定したrunpy起動、`-p no:cacheprovider --tb=short`、bytecode無効化。実測 **378 passed, 3 failed in 10.97s**。共通162＋主系/レビュー60＋ops159＝381。

ops/aitrader_ops/ledger.pyのSHA256は確認前後一致：`52388208126C44AB0A7F1A8541B8019B2E5555EA7331F2B69ABB8A3AAA39732E`。ops内ファイルへの書込みは行っていない。実DB・口座・LINE・実審査は未操作。

[次回Claudeへの依頼](Claude引き渡しプロンプト.md)。
確認開始：2026-09-09 05:58 JST、終了：2026-09-09 06:03 JST。[Claude引き渡しプロンプト.md](Claude引き渡しプロンプト.md) 保存済み、未送信。
