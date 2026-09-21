# 結合v2最小契約案の採否判断（第10回）

## 第11回採否確定（2026-09-17 05:39:17 JST）

D10-01〜05とR11-01は補正付き採用。[後継契約](HISTORY_VALIDITY_BINDING_V2_CONTRACT.md)を優先。比較失敗matched上限をn、先行拒否の併記を段2/4、行検証由来を16理由に補正。E1〜E9を取り込み完全15キーへ展開。既存v1は不変。採用時点ではv2製品未実装で実装は別依頼だった。その後の続行作業のAPI実装記録は後継契約を参照。以下の保留は検討履歴。

第10回レビューの案は、骨格を設計採用し、未整合の条件を保留する。案全体をそのまま確定schemaとして採用しない。今回の依頼は採否判断までであり、履歴v2・結合v2の製品実装には進まない。既存v1の受入・出力・時刻規則は変えない。

## 採用する骨格

| 項目 | 判断 |
|---|---|
| mode / origin | history_validity_binding_fixture_v2 / offline_fixtureを設計採用 |
| 入力5キー | mode/source_origin/input/history/period_historyを採用。validity_documentsは受け取らず、期間履歴の検証済みモデルから直接候補を得る |
| 依存 | strict_input_v1、evidence_history_fixture_v2、period_evidence_history_fixture_v1。履歴v2と結合v2は同時導入 |
| 5段 | 封筒→部品mode値差→構造・内部整合→追加書式→同時点評価・比較という骨格を採用。評価由来の失敗の段は下記の補足が必要 |
| 10理由 | v1の9理由＋PERIOD_HISTORY_MODE_MISMATCHという閉じた語彙を採用。各理由の発生段・優先順の全確定とは区別する |
| 同一時点評価 | 三者の時点一致を確認後、行・期間モデルを同じinput.as_ofで各1回評価。公開単体APIで先に選択して再評価する構成は採らない |
| ID書式 | 期間receipt/revision/seriesとsupersedes参照IDも結合境界のF-01対象。コードは期間subject.code/payload.codeも対象。単体期間v1の受入を狭めない |
| UTC正規化 | 行履歴v2のreceipt比較用コピーだけをUTC・小数6桁へ正規化する方針を採用。原本とpayload hashを変更しない。既存v1には適用しない |
| 15キー | v1の13キー＋period_series_count/period_candidate_countという出力形を設計採用。2件数の対象集合と失敗時値は未確定 |
| timed条件 | 全必須subjectの選択行と入力行hashが一致し、有効期間候補1件のrecord_sha256も一致、半開期間内、余分対象の規則も合格、reason_codes=[]のときに限りtrue |
| 安全フラグ | 成功もready_for_live/current_signal=false、read_only=true。timedは宣言された履歴の検査であり実入手事実・真正性の証明ではない |

10理由はDECISION_TIME_MISMATCH / HISTORY_SELECTION_MISSING / ROW_HASH_MISMATCH / VALIDITY_NOT_SATISFIED / EXTRA_SUBJECT_PRESENT / CODE_FORMAT_INVALID / IDENTIFIER_FORMAT_INVALID / HISTORY_MODE_MISMATCH / PERIOD_HISTORY_MODE_MISMATCH / INVALID_BUNDLE。

selection_sha256は行選択digestを維持する。期間APIの同名digestをこの欄へ入れない。15キー案には期間digest専用欄を追加しない。内部候補から照合するので公開digest同士の比較で写しの同一性を代替しない。

## 原案のまま確定しない点

### D10-01：期間重複の段・評価回数

現行期間モデルは_validate_period_historyで構造と線形履歴を検証し、evaluate(at)でPERIOD_OVERLAP_AT_DECISIONとSELECTION_HASH_INVALIDを生成する。従って「20理由すべて段3へ畳む」「重複は段3 INVALID_BUNDLE」「重複はVALIDITY_NOT_SATISFIED」「段5の時点一致後に各モデル1回評価」は同時には満たせない。

採用するのは検証・評価分離と段5各1回の骨格。原案の反証3（重複を段3で拒否）は採用保留。次のレビューでは、構造由来の18理由は段3 INVALID_BUNDLE、重複は段5 VALIDITY_NOT_SATISFIED、digest生成失敗は防御的INVALID_BUNDLEへ診断をリセット、という修正候補を確認する。この候補を実装済み・確定分類とは扱わない。時点不一致と重複が同居する束の優先理由も必須反証にする。

### D10-02：余分subjectの扱い

原案の「必須外subjectがあっても必須集合の判定を変えない」は、required集合が不変という意味なら妥当だが、余分な期間subjectを無視して成功してよいという意味には採用しない。v1は行履歴全体の未来subjectも拒否し、期間の有効な余分subjectも隠さない設計である。

次レビューへの候補は、行履歴全体と期間履歴全体のsubject集合の和集合から必須集合を引き、重複排除した件数をextraとする。期間側の未来のみ・期限外のみも検出対象としEXTRA_SUBJECT_PRESENTへ写す。原案反証7は「requiredは変わらないが余分subjectは拒否」を期待とする修正が必要。期間全体を受け取れるv2の明示的な追加契約なので、無言でv1へ適用しない。

### D10-03：時点不一致と診断件数

v1では時点不一致でもcorrectedを得るため行モデルをas_ofで評価する。v2原案は三者不一致で評価前に打ち切るため、この診断契約はそのまま継承できない。correctedを期間訂正件数へ拡張する提案も採用していない。

次レビューへの候補は、段1〜4と三者時点不一致では6件数すべて0、selection_sha256=null、timed=falseとし、同時点評価後だけ診断を返すこと。period_series_countは全系列数、period_candidate_countは全subjectの有効候補数という候補を提示する。重複で候補を確定できない場合の件数・digest、行欠落時の各件数も、固定出力例で確定してから実装する。原案15キーを採用したことだけで値の契約まで確定したと扱わない。

### D10-04：UTC正規化の範囲

依存period_evidence_history_fixture_v1のreceipt完全一致規則は維持する。期間v1を結合へ渡した場合だけ正規化する隠れたモード変更はしない。正規化対象は行履歴v2のentry.observed_at/recorded_atの比較用表現に限定する候補とし、payload内の日時・sha256・supersedes・IDを書き換えない。

UTC変換時の範囲外日時の拒否理由、NO_OP判定より先に行う時刻解釈の失敗と既存理由順、秒未満の扱いを履歴v2契約へ具体化する必要がある。期間receiptにもUTC/JST同一性を求めるなら期間履歴の別版が必要で、今回の依存案には追加しない。反証10は行履歴v2に限定し、期間v1では表記差がRECEIPT_CONFLICTになる対照を追加する。

### D10-05：mode差と防御経路

段2はdict・modeキー存在・値差だけを直接判定し、他のキーやorigin差へ依存しない。両履歴が同時に版違いの場合、2理由を同段として昇順併記する候補を次レビューに出す。mode欠落/非dict、inputの版差、その他の部品origin差は段3候補。部品検査順と予期しない内部失敗の固定出力も最小実装契約に必要であり、原案の10語彙だけでは省略できない。

## 提示反証12件の採否

以下は設計期待であり、結合v2の製品試験実測ではない。

| 番号 | 原案の論点 | 採否 |
|---|---|---|
| 1 | 三者一致・全必須整合でtimed=true | 採用。入力行との一致・extra規則・reason空も必要 |
| 2 | 期間decisionだけ差 | 単独DECISION_TIME_MISMATCHを採用。件数はD10-03で要確定 |
| 3 | 期間重複が段3 INVALID_BUNDLE | 保留。段5各1回と衝突。D10-01の修正候補へ |
| 4 | 必須候補0 | VALIDITY_NOT_SATISFIED・timed=falseを採用 |
| 5 | record_sha256差 | VALIDITY_NOT_SATISFIEDを採用 |
| 6 | 区間外だけの必須subject | 欠落として拒否を採用。空の過去候補でも単体期間APIは成功し得る |
| 7 | 余分期間subject | required不変は採用。成功可否は原文不明確、D10-02の拒否案へ |
| 8 | period_history.mode差 | PERIOD_HISTORY_MODE_MISMATCHを採用。両mode差はD10-05 |
| 9 | 期間IDのF-01違反 | 段4拒否を採用。構造破損とmode差の先行順も保持 |
| 10 | UTC/JST receipt同一性 | 行履歴v2のみ採用。期間v1対照と正規化失敗例を追加で要確定 |
| 11 | 結合v2へ行履歴v1 | 段2 HISTORY_MODE_MISMATCHを採用 |
| 12 | 成功もready/current=false | 採用 |

半開期間の充足は同じ期間evaluate結果のcandidates生成条件として使い、候補取得のために公開APIやモデルを再評価しない。履歴v2の入力/公開出力・内部検証器・正規化失敗の理由順も別途具体化する。

次工程はD10-01〜05を反映した段表・件数表・人工成功/失敗例・固定出力をレビューで具体化すること。12件は最低限の反証案であり、境界の網羅や実装着手条件の充足を意味しない。DB保存・実取込・日次シグナル接続・実審査・通知・実接続は範囲外。


再照合・更新時刻: 2026-09-17 06:12:46 JST。第11回の採用内容を維持。今回製品試験未実施。
