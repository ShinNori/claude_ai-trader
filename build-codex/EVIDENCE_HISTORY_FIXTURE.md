# 人工証拠の取得・訂正履歴モデル

`evidence_history_fixture_v1` は[保存契約案](STRICT_EVIDENCE_STORAGE_PLAN.md)の重複と訂正を検証する、純粋なメモリ内モデル。DB保存、実入力の取込、既存home移行は実装しない。`strict_input` や `strict_validity` の成功とは別の検査であり、有効期間と履歴を一括認定しない。

## 入力契約

最上位は `mode`, `source_origin=offline_fixture`, timezone付き `decision_at`, 空でない `entries` のみ。各entryは `receipt_id`, `revision_id`, `subject`, `observed_at`, `recorded_at`, `payload`, `sha256`, `supersedes` のみ。

- IDと銘柄コードは空白除去済みの非空文字列。subjectは `group=lots|events` と `code`。payloadは既存strict_inputの単元/イベント行と同じ厳密な形で、銘柄も一致させる。
- hashはUTF-8、キー順ソート、空白なし、非有限値禁止のJSONから計算するSHA-256小文字64桁。
- observed_atとrecorded_atはtimezone付きで `observed_at<=recorded_at`。新しいreceiptは入力順にrecorded_atが逆行しない。同一時刻は許可する。
- 同じreceipt_idの正規化JSONが完全一致する再投入はNO_OP。古いreceiptの再投入を時刻逆行と誤判定しない。内容が異なれば全体拒否する。
- revision_idの同一性はsubject・payload hash・supersedesで決める。同じ版を別receiptで再取得すると取得数だけ増え、過去版を再取得しても最新版へ戻さない。
- 初版はsupersedes=null。訂正版は同じsubjectの現在末尾のrevision_idとhashを参照する。未知版、別銘柄、別group、参照hash差、分岐、自己参照、前方参照を拒否する。同じ値でも新revisionなら参照を要する。

## 過去時点の選択と出力

初めて現れたrevisionのrecorded_atがdecision_at以下のときだけ、そのsubjectの選択を更新する。後日の訂正は履歴として検証するが過去時点の選択に混ぜない。これは申告時刻の比較であり、当時の実際の入手可能性を証明しない。

成功は `VERIFIED_OFFLINE_HISTORY`。receipt_countは固有取得数、revision_countは固有版数、noop_receipt_countは完全再投入数、selected_revision_countは選択できたsubject数、future_revision_countは初出記録が判定後の版数。selection_sha256は選択した `{group,code,revision_id,sha256}` をgroup/codeでソートした配列のhash。選択ゼロなら空配列のhashとする。

どこか1件でも不正なら `DATA_INCOMPLETE`、全件数0、selection_sha256=null、固定reasonを返す。途中まで通った件数を保存成功のように返さない。ID・銘柄・値・時刻の本文は出力しない。全結果で `ready_for_live=false`, `current_signal=false`, `read_only=true`。

## 実行例

ai-traderでPowerShellの作業フォルダを開き、build-codexをPython検索対象にして実行する。

```powershell
$env:PYTHONPATH="$PWD\build-codex"
python -m aitrader.evidence_history_fixture_cli --input build-codex/examples/evidence_history_valid.json
```

人工例は9月10日の単元100と9月12日の訂正200を持ち、判定時点9月11日には旧版だけを選ぶ。CLIは既存の安全なJSON読取を使い、DBや履歴ファイルを作らない。正常0、不適合/読取エラー2で終了する。

## 残る境界

永続化・排他・電源断耐久・原本署名・実公表時刻・実有効期間・全銘柄の網羅性は未保証。保存済み選択の不可変性もこのAPIだけでは保証しない。次は適用期間検査と履歴選択の結合契約を文書化し、人工受入で検証する。実DB接続は別工程。

次工程の具体案は[履歴と適用期間の結合案](HISTORY_VALIDITY_BINDING_PLAN.md)。必要subjectを要求しない本モデルでは、全版が未来でも内部整合が正しければ選択0件で成功する。実行可能な入力が揃った意味ではない。

## CLI読取上限（F-10）

入力JSONの上限は1 MiB（1,048,576バイト）。超過時は検査を開始せず、固定文「証拠履歴を検査できません。入力形式と訂正の参照を確認してください。」をstderrへ出し、終了コード2を返す。JSON結果はstdoutへ出さない。既存の安全な読取でリンク/reparse・観測できた読取中差し替え・重複キーも拒否する。あらゆる非協調変更を完全検出する保証ではない。分割や上限引上げを自動で行わない。

次の結合用コード/ID書式と時刻正規化の固定案は[結合案 F-01/F-03](HISTORY_VALIDITY_BINDING_PLAN.md)。現履歴v1のtrim済み非空文字列・canonical JSON完全一致は現行互換のため維持する。


## 第6回対応：内部選択モデルの共用（2026-09-13）

構造・内部整合検証を `_validate_history`、評価時点の選択を内部モデルの `evaluate(at)` へ分離し、履歴公開APIと結合APIが共用する。公開出力のキー・reason・digest・receipt完全一致・NO_OP・再取得・線形訂正のv1契約は変更しない。選択hash生成失敗は従来どおりSELECTION_HASH_INVALIDで固定拒否する。内部モデルの選択行やsubjectは公開JSONへ追加しない。結合v1では追加ASCIIコード/ID検査を境界だけへ適用し、既存履歴v1の受入を狭めない。
