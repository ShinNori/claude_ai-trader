# 単元・イベントの適用期間検査

2026-09-12。`strict_validity_fixture_v1` は、人工原本が宣言した適用期間と、選択された単元/イベントの値の対応を確認する独立オフライン検査。実際の銘柄の適用期間を調査・取得した機能ではない。

## 入力契約

最上位はmode、source_origin、input、validity_documentsだけ。modeは`strict_validity_fixture_v1`、source_originは`offline_fixture`。inputは既存strict_input_v1一式で、原本hash・参照・価格・公表・単元/eventの検査を最初に通す。ここで不足があれば適用期間検査へ進まない。

validity_documentsは空でない配列。各文書のキーはid、sha256、payloadだけ。idは前後空白のない非空文字列で一意、sha256はpayloadのcanonical JSON SHA-256（既存strict_inputと同じ表現）の小文字64桁。

payloadの必須キーは次の5つだけ。

| キー | 値 |
|---|---|
| group | lotsまたはevents |
| code | inputのexpected_codesにある銘柄 |
| record_sha256 | そのgroup/codeが実際に参照する単元/イベント行全体のcanonical SHA-256 |
| valid_from | timezoneを伴う開始日時 |
| valid_until | timezoneを伴う終了日時。開始より後 |

各銘柄のlotsとeventsについて証拠をちょうど1件ずつ要求する。欠落、余分な対象、同じ対象の重複、文書IDの重複、原本hashの不一致を拒否する。単元数等を変えて元入力のhashを計算し直しても、古い値のrecord_sha256を流用できない。

## 時点の扱い

本人工契約は **valid_from ≦ input.as_of ＜ valid_until** とする。開始ちょうどは有効、終了ちょうどは期限切れ。異なるtimezone表記でも同一時点として比較する。開始前、期限切れ、逆転/同時刻の期間、timezone欠損を拒否する。

期間を推測して補完せず、終了不明を無期限へ変換しない。この半開区間は人工入力の明示規則であり、提供元の生データが同じ規則を採用しているという意味ではない。実原本を受け入れる際は、その境界規則との対応を別に確定する。

## 結果と保証範囲

成功は`VERIFIED_OFFLINE_VALIDITY`、不足は`DATA_INCOMPLETE`。`scope="selected_lots_events"`。expected_evidence_countは期待件数、verified_evidence_countは全チェックを満たした一意の対象数。重複した対象/文書は確認済み件数へ加えない。入力自体が不正なら`INPUT_`理由を返し、件数は両方0。部分成功の件数があってもstatusが不合格なら全体を通過扱いしない。

銘柄・原本ID・値・適用日時は出力せず、固定理由と件数だけを返す。成功失敗ともready_for_live=false、current_signal=false、read_only=true。DB保存・訂正履歴・候補生成・通信は行わない。

宣言した期間と値の整合を確認するだけで、期間の外部真正性、当時利用可能だった証拠、公表/取得完了時刻、後日の訂正、実市場での適用を証明しない。価格鮮度・営業日・全ページの同一時点性は対象外。

## 操作

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader\build-codex'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.strict_validity_cli --input .\examples\strict_validity_valid.json
```

Python入口は`inspect_strict_validity(bundle)`。例は架空銘柄0001の人工証拠2件。CLIの成功0、不足/読取不正2。既存の限定JSON読取を使い、読取エラーは固定日本語文にする。

## 検証

新規API38件・CLI5件、計43件。既存strict_input52件と合わせて95 passed（0.64秒）。期間境界・同一時点の別timezone・値の取り違え・参照先・重複・欠損・hash・不正型・入力不変・本文非表示を確認。サンプルの実CLIも2件一致で成功した。全体実測は[README](README.md)。

残る実データの根拠は[REAL_DATA_EVIDENCE.md](REAL_DATA_EVIDENCE.md)、全体工程は[COMPLETION_ROADMAP.md](COMPLETION_ROADMAP.md)。

## F-09 防御処理

入力検査後の原本参照アクセス/解決がKeyError・IndexError・TypeError・ValueError・OverflowError・RecursionErrorで失敗した場合、INPUT_SOURCE_REFERENCE_INVALIDで拒否する。期待数は維持し、検証済み件数は0。正常な参照解決の契約や既存期待値は変更しない。

入力検査後、as_ofをtimezone付き時刻として解釈できない場合はINPUT_INVALID_AS_OFで拒否し、算出済み期待数を維持、検証済み件数は0とする。入力取得から行解決までの防御であり、期待数を算出する前の失敗では0。結合側はこのINPUT_*をINVALID_BUNDLEへ写像し、期待数を再利用しない。
