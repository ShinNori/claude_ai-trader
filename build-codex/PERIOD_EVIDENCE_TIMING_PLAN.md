# 期間証拠の観測・記録履歴：採用する最小契約

2026-09-13、第8回R8-01〜05対応。最小案を以下の補足付きで設計採用する。第9回の人工入力・固定出力・CLI案を採用し、独立期間履歴APIとCLIを実装済み。既存4API、結合v1の13キー・閉じた9理由・period_evidence_timed=falseは変更しない。DB保存・実取込・日次接続の採用ではない。

## 採否と対象

| 論点 | 判断 |
|---|---|
| R8-01 系列キー | 採用。entryのseries_idを投入側が宣言。同系列内の線形訂正と別系列への追加区間を区別する |
| R8-02 有効候補 | 採用。各系列の過去選択後、subjectごとに半開区間内の候補が1件なら写す、0件なら写さず欠落、2件以上なら期間履歴全体を拒否 |
| R8-03 文書ID | 採用。選択された期間revision_idをvalidity_documents.idへそのまま写す |
| R8-04 timed昇格 | 採用。結合v2のみで検討し、v1はfalse固定。独立期間履歴APIは当該フラグを出さない |
| R8-05 API例外の前提 | 採用。JSON由来の素の型を対象とし、メソッドを差し替えたPythonオブジェクトは契約外。既存例外catchは変更しない |
| 最小の独立API案 | 補足付き採用。mode/入出力/選択/識別を独立APIへ実装。橋渡しは投入側の責務を維持 |

解消するのは期間宣言自身の観測・記録時点の人工整合である。自己申告時刻の整合は、実際の入手事実・提供元真正性・公表完了時刻を証明しない。実根拠の不足はREAL_DATA_EVIDENCE.mdに残る。

入力領域はJSON由来の素のdict/list/str/int/float/bool/None。内容の妥当性は別途検査し、敵対的なPythonサブクラスの例外隔離は対象外。

## 入力と識別

mode=period_evidence_history_fixture_v1、source_origin=offline_fixture。最上位はmode/source_origin/decision_at/entriesの4キー。entriesは空でないlist、各entryはreceipt_id/revision_id/series_id/subject/observed_at/recorded_at/payload/sha256/supersedesの9キーちょうど。

subjectはgroup/codeの2キーでgroupはlotsまたはevents。payloadはgroup/code/record_sha256/valid_from/valid_untilの5キーちょうどでsubjectと一致する。record_sha256とsha256は64桁小文字hex。sha256はpayloadのcanonical JSON SHA-256。期間payload自己hashと行参照record_sha256は別用途であり、独立APIは後者が実際の行と一致するか検査しない。

receipt_id/revision_id/series_id/codeは非空trim済み文字列。INVALID_IDENTIFIERの対象はreceipt_id/revision_id/series_idだけであり、subject.codeの不正はINVALID_SUBJECT、payload.codeの不正・subjectとの不一致はINVALID_PAYLOAD。payloadが5キーでない場合もINVALID_PAYLOADで、行用_lot/_event検証器は使わない。ASCII追加制限は既存結合境界だけの契約を維持し、この独立APIには先取りしない。時刻はtimezone付き文字列、observed_at<=recorded_at、valid_from<valid_until。canonical JSONはUTF-8、キー順固定、空白省略、非有限数禁止。receipt時刻表記のUTC正規化はしない。

series_idは期間履歴束内で一つのsubjectに固定する。subjectを変える訂正は不可。区間端やrecord_sha256を訂正するときも系列は変えない。別の追加区間は新series_idで宣言し、区間の重なりから訂正/追加をAPIが推測しない。投入側の系列宣言の正しさを外部根拠なしに証明しない。

receipt_idとrevision_idはそれぞれ期間履歴束全体で一意に識別する（seriesローカルIDではない）。行履歴側のIDとは独立の名前空間とする。同じrevision_idを別系列や別subjectで使い回さない。

## 履歴整合と訂正

同一receipt IDのcanonical entry完全一致再投入はNO_OP、差はRECEIPT_CONFLICT。NO_OPを新規記録の時刻逆行検査より先に扱う。別receiptで既存revisionを再取得できるが、revision同一性（series_id/subject/payload hash/supersedes）が一致しなければREVISION_CONFLICT。再取得は初出時刻・系列末尾・選択を変えない。receipt_countは一意receipt数、noopは完全再投入数。

新規receiptは全系列横断でrecorded_at非減少。同時刻は親が先。入力を自動並替えしない。各seriesの初版はsupersedes=null、その後の新revisionは同seriesの現在末尾revision_id/sha256をsupersedesの2キーで正確に参照する。同系列の古い版参照・前方参照・自己参照・分岐・循環を拒否。別系列の既知revision参照はSERIES_REFERENCE_INVALID。同一series内のsubject変更はSERIES_REFERENCE_INVALID。

## 評価、件数とdigest

構造・内部整合の検証と時点選択を分離する。既存4APIを呼ばない独立モデルとし、行payload用検証器を緩めて期間payloadへ流用しない。

各系列で初出recorded_at<=decision_atの末尾revisionを1つ選ぶ。observed_atだけが判定前でもrecorded_atが後なら選ばない。未来entryを検証入力から削除せず、選択からだけ除外する。

その選択版のうちvalid_from<=decision_at<valid_untilを満たすものだけを有効候補とする。同subjectで2候補以上ならPERIOD_OVERLAP_AT_DECISIONで全体拒否。0候補でも期間履歴単体は成功し得る。選択版が期限外でも、同系列の古い版へ戻って候補を探さない。区間が評価時点以外で重なることはこのmodeの拒否条件ではない。

出力14キー：mode/source_origin/status/reason_codes/receipt_count/revision_count/noop_receipt_count/series_count/selected_series_count/future_revision_count/selection_sha256/ready_for_live/current_signal/read_only。

series_countは全系列数。selected_series_countは過去選択が存在する系列数で、区間外の選択も含む。有効候補数とは別。future_revision_countは初出記録がdecision_atより後の一意revision数で、初版も含む。再取得はrevision数へ加算しない。

selection_sha256は有効候補だけを{group,code,series_id,revision_id,sha256}の配列へ写し（このsha256は選択された期間revisionのpayload自己hashであり、payload.record_sha256という行hashではない）、(group,code)順に並べたcanonical JSONのhash。有効候補0件なら空配列hash。これは[結合v1](HISTORY_VALIDITY_BINDING_PLAN.md)の同名フィールド（行履歴の選択digest）とは異なる射影を定義し、偶然同じdigest値でも同じ意味ではなく、相互に代用しない。selected_series_countと配列長は一致しなくてよい。銘柄・ID・区間・本文は公開出力へ追加しない。

成功はreason_codes=[]、status=VERIFIED_OFFLINE_PERIOD_HISTORY。不成功はstatus=DATA_INCOMPLETE、単独理由、6件数すべて0、digest=null。ready_for_live/current_signal=false、read_only=true。period_evidence_timedは出さない。

理由候補は第8回最小案に欠けていたINVALID_HASHを補い、履歴v1の17種＋期間固有3種の20種を設計採用する：INVALID_BUNDLE/INVALID_MODE/INVALID_ORIGIN/INVALID_DECISION_AT/INVALID_ENTRIES/INVALID_ENTRY/INVALID_IDENTIFIER/INVALID_SUBJECT/INVALID_TIMESTAMPS/RECORDED_AT_REVERSED/INVALID_PAYLOAD/INVALID_HASH/PAYLOAD_HASH_MISMATCH/INVALID_SUPERSEDES/RECEIPT_CONFLICT/REVISION_CONFLICT/SELECTION_HASH_INVALID/PERIOD_INTERVAL_INVALID/SERIES_REFERENCE_INVALID/PERIOD_OVERLAP_AT_DECISION。entry.sha256の書式破損はINVALID_HASH、payload.record_sha256の書式破損はINVALID_PAYLOAD、supersedes.sha256の書式破損はINVALID_SUPERSEDES、payloadの自己hash差はPAYLOAD_HASH_MISMATCH、期間時刻のtimezone欠損・解釈不能・開始>=終了はPERIOD_INTERVAL_INVALIDへ分類する。この20理由は新しい期間履歴だけの語彙で、結合v1の閉じた9理由は増やさない。

SELECTION_HASH_INVALIDは選択投影のdigest生成失敗専用の防御理由であり、検証済みの通常JSON入力では通常到達しない。payload形の不正には使わない。

### 最小の判定順序

封筒→mode→origin→decision_at→entriesを先行検査。各entryを入力順に、キー/canonical化→識別子→receipt再投入/競合→subject→観測/記録時刻と逆行→payload形とsubject一致→期間→自己hash→supersedes形→revision再取得/競合→seriesのsubject固定→訂正参照の順で検証し、最初の理由だけ返す。未知・前方・自己・分岐・参照hash差はINVALID_SUPERSEDES、参照先が既知で他seriesの場合だけSERIES_REFERENCE_INVALID。同系列名の別subject再利用もSERIES_REFERENCE_INVALID。全entry検証後に選択→有効候補重複→digestを評価する。期間重複によって壊れた未来entryの検査を飛ばさない。

既知seriesへの新revisionでsupersedes=nullを再投入する場合、subjectが同じならINVALID_SUPERSEDES。R9-03の「subjectが同じでも別でも」という表現は既定の先行判定と衝突するため、その部分は採用しない。subject変更は先行するSERIES_REFERENCE_INVALID、既存revision_idの内容差はさらに先行するREVISION_CONFLICTを維持する。

## 結合v1への写し方と保証の限界

期間履歴全体が合格した場合だけ、各subjectの有効候補1件を{id: revision_id, sha256: 期間payloadのhash, payload: 同じ期間payload}としてvalidity_documentsへ写す。0候補のsubjectは要素を作らず（全0候補なら空list）、結合側のVALIDITY_NOT_SATISFIEDによる欠落判定に任せる。2候補以上なら同hashであっても期間履歴で拒否し、結合へ候補を渡さない。

結合へ写すrevision_idは結合境界のASCII ID書式[A-Za-z0-9][A-Za-z0-9._:-]{0,127}を満たす必要がある。期間履歴単体の非空trim済みID受入を狭めず、単体成功を結合の書式合格とは扱わない。投入側は結合に使用可能なIDを宣言し、写すときのtrim/変換/別IDへの置換では救済しない。

候補は時点・区間で選び、record_sha256が入力行と合う候補だけに事前絞込みしない。余分なsubjectの有効候補も勝手に落とさず結合へ渡し、既存の対象集合不一致検査を維持する。複数期間があるという理由で結合v1の重複subject拒否を緩めない。

公開期間履歴APIは件数とdigestだけを返す。v1用の写しは投入側の責務であり、期間履歴API成功と結合v1成功をANDしただけでは、投入側が同じ選択を写したことを機械的に証明できない。digestを結合v1へ追加したり照合だけでtimed=trueにしない。結合v1は渡されたvalidity_documentsが期間履歴の選択結果から作られたかを検証せず、写しの出所や同一性を保証しない。v1のperiod_evidence_timedは常にfalse。未来・期限外だけの期間subjectは写しに現れないため結合v1では検出できない。元の期間履歴全体に対する余分subject検査は結合v2で別途決める。

## 結合v2に限る将来の昇格

true導入は結合v2のみ。必要な最小条件は、行/期間両履歴の検証成功、同一as_ofでの内部の過去選択、全必須subjectで有効候補ちょうど1件、選択行hashとrecord_sha256の一致、半開期間充足、結合全体のreason_codes空であること。投入側の写しを未検証で信用せず、共用選択モデルによる対応付けを別途設計する。

外部真正性や実際の入手事実の証明とは別で、ready_for_live/current_signalを昇格しない。履歴v2/結合v2のUTC正規化同時導入という既決の版境界を維持する。本書は結合v2の全schema・理由・実装を確定するものではない。

## 反証表

| ケース | 期待 |
|---|---|
| 同subjectの追加区間を新series_idで投入 | 独立系列として保持 |
| 同seriesでvalid_untilを訂正 | 同じ系列の線形訂正 |
| 新receipt/新revisionで同seriesのsubject変更 | SERIES_REFERENCE_INVALID |
| supersedesで別series参照 | SERIES_REFERENCE_INVALID |
| 過去に適用、期間初出記録は判定後 | 選択なし。単体成功可、結合は欠落 |
| 観測だけが判定前 | 記録後の版は選択しない |
| 記録/区間開始が判定と同時 | 他条件成立なら有効候補 |
| 区間終了が判定と同時 | 過去選択ありでも有効候補ではない |
| 初版は判定前、訂正は判定後 | 初版を使う。同値訂正でも同じ |
| 旧版再取得/完全再投入 | 選択を戻さず、後者はNO_OP |
| 同receiptで時刻表記を変更 | canonical差ならRECEIPT_CONFLICT |
| 同subjectの有効系列候補2件 | PERIOD_OVERLAP_AT_DECISION、全体拒否 |
| 複数選択系列のうち有効は1件 | その1件だけを写す |
| 期間だけ変更し旧payload hash使用 | PAYLOAD_HASH_MISMATCH |
| 有効候補のrecord_sha256が行と違う | 候補を隠さず結合へ。不適合 |
| 有効な余分subject | 捨てずに結合へ。全体不成功 |
| 全系列が区間外 | selected_series_count>0でもdigestは空配列hash |

## 第9回の採用範囲と残工程

R9-01〜05を上記へ反映。独立APIはinspect_period_evidence_history、CLIはpython -m aitrader.period_evidence_history_fixture_cli --input PATHを採用する。提示人工入力をexamples/period_evidence_history_valid.jsonへ保存し、固定14キー出力とdigestを製品試験で検証する。

CLIは既存packet_cli._mappingとstrict_input_cli._Parserの安全読取を流用する。1MiBちょうどを許容し、超過はAPI非呼出。成功は終了0とJSON、不成功判定は終了2とJSON、引数・読取・予期しない例外は終了2、stdout空、stderr「期間証拠履歴を検査できません。入力形式と系列の参照を確認してください。」の1行とする。

既存4API・結合v1の保証は変更しない。結合v2の全schema・理由、DB保存、実データ原本の証明、実接続は残工程であり今回の実装範囲外。
