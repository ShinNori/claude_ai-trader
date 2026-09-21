# 履歴選択と適用期間の結合契約

現状の[履歴検査](EVIDENCE_HISTORY_FIXTURE.md)と[適用期間検査](STRICT_VALIDITY.md)は独立している。両方が成功しても、同じ版を見ているとは限らない。この文書は人工受入の結合v1契約であり、新しい取込経路やDB保存の採用ではない。

## 現在の実装（2026-09-13、第6回対応）

`aitrader.history_validity_binding.inspect_history_validity_binding(bundle)` と `aitrader.history_validity_binding_cli --input PATH` を追加。履歴の内部検証モデルを公開履歴APIと結合APIで共用し、receipt同一性・公開v1出力を維持する。以下の過去レビュー節にある未実装/設計段階という記述は当時の記録である。時刻UTC正規化の履歴v2/結合v2、DB保存、実データ取込は引き続き未実装。最新の実測と残制限はREADME末尾。

## 結合時に要求する一致

1. strict_inputのas_ofと履歴decision_atをtimezone込みの同一時点として比較する。日付だけの一致では足りない。
2. 必須subjectはstrict_inputのexpected_codes×lots/events。履歴が過去時点に選択したsubjectに欠けがあれば拒否する。履歴内部整合の成功や空配列digestを充足の代用にしない。
3. 選択revisionのpayload hashと、strict_inputが参照する実際のlots/events行のcanonical hashを一致させる。最新headを使うと未来訂正が混ざるため、過去選択を使う。
4. 適用期間証拠も同じ選択行のhashを参照し、valid_from<=as_of<valid_untilを満たす。期間が過去へ遡っていても、後日記録された訂正版を当時取得済みと扱わない。
5. 外部向け出力は件数・固定reason・digest・false flagsだけとし、行本文や識別子を診断へ露出させない。

## 次の実装前に固定する点

履歴APIは現在digestのみ返すため、外側で履歴選択を再実装しない。結合は内部の検証済み選択モデルを共用する単一APIに固定する。public APIへ原本本文を追加しない。外部digest照合は採らないためselection_value_sha256も追加しない。版入りdigestは同値の別revisionを区別する用途で維持する。余分subjectは拒否する。

適用期間証拠そのものの観測・記録時点は現履歴モデルに含まれていない。行の取得時刻と期間証拠の取得時刻を同一と推測しない。両者を結ぶ追加の人工証拠契約なしには「当時すべて分かっていた」と認定しない。

## 必要な反証

- 履歴と入力の判定時刻が異なる。同一時点のUTC/JST表記差は許可する。
- 履歴は正常だが、必須銘柄のeventsが欠ける／全版が未来にしかない。
- 入力が訂正後の値、過去選択は訂正前の値で、双方単体は正常。
- 適用期間のhashが別版、または期間証拠が後日しか観測されていない。
- 古い版の再取得で現在版が巻き戻らない。同値訂正でも版識別を失わない。
- 途中不適合で部分成功を返さず、DB・home・ファイルに書き込まない。

ここまで整えても提供元の真正性、実公表時刻、実取引への適合、永続化の原子性は別途残る。

## Claude Opus F-01〜F-08の採用判断

以下は次の結合モードの固定設計。既存strict_input_v1/evidence_history_fixture_v1の識別子や再投入の受入を、この文書だけで変更しない。新しい結合APIはまだ未実装。

| 指摘 | 採用する契約 |
|---|---|
| F-01 | 結合境界では入力・履歴両方に同じ共用検査を適用する。銘柄コードはASCII大文字英数字4〜5文字 `[0-9A-Z]{4,5}`。IDはASCII `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`。空白・制御文字・全角・ゼロ幅文字を拒否し、自動NFKC変換やtrimで原本を変えない。これは人工結合モードの書式であって実市場コードの完全な規則ではない。原本document ID、期間document ID、receipt/revision IDと参照IDへ同じID規則を使う。JSON PointerはIDではなく、この制限を適用しない。 |
| F-02 | 内部共用選択モデルによる単一APIを採用。外側の選択再実装と外部digest照合はしない。selection_value_sha256は不要。 |
| F-03 | 履歴v2と結合v2を同時導入するときのみ時刻をUTC・マイクロ秒6桁へ正規化後、receipt同一性を比較する案Aを採用する。結合v1は履歴v1のcanonical JSON完全一致を維持する。原本は保持し、入力を変更しない。既存履歴v1はcanonical JSONの完全一致とRECEIPT_CONFLICTを維持し、無言で新規則へ切り替えない。新しい共用モデル/版の実装で両契約を明示的に分ける。as_ofとdecision_atや期間境界は従来どおり同一時点比較。 |
| F-04 | 投入側が全sourceの新receiptをrecorded_at昇順に安定整列する。検査APIは自動ソートしない。同時刻の訂正は親が先という順序を投入側が保持し、曖昧な親子順を推測しない。完全再投入のNO_OPは時刻逆行検査より先。 |
| F-05 / F-07 | required_subject_count、matched_subject_count、extra_subject_countを必須出力にする。必須集合はexpected_codes×lots/events。余分subjectは未来だけに存在するものも含め、履歴全体のsubject集合から数えて拒否する。matchedは過去選択が存在し、入力行hashと一致し、適用期間も合格した必須subject数。成功条件はrequired>0、matched=required、extra=0、時点一致、かつreason_codesが空であること（status=VERIFIED_OFFLINE_BINDINGと同値）。matched=requiredでも期間証拠の余分subjectなどの理由があれば不成功。空配列digestの固定値を充足判定へ焼き込まない。 |
| F-06 | 成功/失敗ともperiod_evidence_timed=falseを必須出力にする。成功は人工的な値・期間整合まで。期間証拠自身の観測/記録時刻を行の時刻で代用しない。ready_for_live/current_signalもfalse固定。 |
| F-08 | examples/history_validity_binding_valid.jsonを結合専用の人工例とする。同一判定時点、lots/eventsの両方、同じ行hashを持つ期間証拠を収録。既存の単体2例は変更しない。 |

required/matched/extraは検証診断であり、保存・部分承認の件数ではない。構造破損等で安全に集合を確定できなければ全件数0と固定reasonで拒否する。正常な部品同士の不一致の場合だけ診断件数を返す。本文・銘柄・IDは返さない。

追加の必須出力corrected_after_as_of_countは、input.as_of時点で選択が存在する必須subjectのうち、判定後に初出記録された訂正版（supersedes非null）が1件以上あるsubjectの数。同じsubjectに複数訂正があっても1、再取得/再投入は数えない。未来の初版だけでは訂正に数えない。初版と訂正版がともにas_ofより後のsubjectも数えない（corrected=0）。値は出さず、過去選択も変えない。

F-01/F-03の新規則は既存受入互換を伴うため、次工程は共用モデルと版境界の実装・反証である。現時点で正規形検査や新しいreceipt時刻同一性が稼働済みとは扱わない。

## 結合が必要な具体例

既存history例のdecision_atだけをstrict_input例のas_ofへ合わせると、履歴は単元200、入力は100を選び、双方単体では成功できる。単体成功をANDするだけではこの差を検出できない。新しい専用例はこの不一致を避けた設計受入用で、実データではない。

## 第2回レビューによる結合v1の固定契約（R2-01〜09）

この節は以前の「次版」の曖昧な表現に優先する。結合APIは未実装。

- mode=`history_validity_binding_fixture_v1`、source_origin=`offline_fixture`。
- 依存はstrict_input_v1 / evidence_history_fixture_v1 / strict_validity_fixture_v1。receipt同一性はcanonical JSON完全一致。F-01は結合境界のみ。内側3APIの受入は変更しない。
- UTC正規化はevidence_history_fixture_v2とhistory_validity_binding_fixture_v2の同時導入まで効かせない。異なる版の混在は不可。履歴mode差はHISTORY_MODE_MISMATCHで拒否する。
- 入力最上位はmode/source_origin/input/history/validity_documentsの5キーのみ。

### 出力と閉じた理由集合（R2-03）

出力キーはmode、source_origin、status、selection_sha256、reason_codes、required_subject_count、matched_subject_count、extra_subject_count、corrected_after_as_of_count、period_evidence_timed、ready_for_live、current_signal、read_onlyで固定する。

statusはVERIFIED_OFFLINE_BINDINGまたはDATA_INCOMPLETE。成功時reason_codes=[]。selection_sha256は検証済み履歴の同名digestを再掲し、新たなdigestを作らない。period_evidence_timed/ready_for_live/current_signal=false、read_only=trueは全結果で固定。

理由コードは以下9種だけ。複数該当時は重複除去して昇順に返す。ただし構造破損は単独INVALID_BUNDLEで全件数0、digest=nullとする。前提検査失敗で安全な選択を得られない場合も件数0/digest=null。部品の内部整合不成立はINVALID_BUNDLEへ分類し、元の理由や入力本文をそのまま外へ返さない。正常な部品間の比較結果のみ診断件数と履歴digestを返す。

| 理由 | 条件 |
|---|---|
| DECISION_TIME_MISMATCH | as_ofとdecision_atが異なる時点 |
| HISTORY_SELECTION_MISSING | 必須subjectの過去選択がない |
| ROW_HASH_MISMATCH | 過去選択と入力行hashが異なる |
| VALIDITY_NOT_SATISFIED | 期間証拠の対象/hash/期間等が不適合 |
| EXTRA_SUBJECT_PRESENT | 履歴全体に必須集合外subjectがある |
| CODE_FORMAT_INVALID | 指定コード書式に違反 |
| IDENTIFIER_FORMAT_INVALID | 指定ID書式に違反 |
| HISTORY_MODE_MISMATCH | 履歴modeが依存v1と異なる |
| INVALID_BUNDLE | 最上位や部品の構造・原本・履歴整合を検証できない |

期間証拠は構造/型/原本自己hash破損ならINVALID_BUNDLE、構造が正しい証拠の欠落・重複subject・行hash不一致・期間外ならVALIDITY_NOT_SATISFIED。同時点の検査が不成立ならmatched=0として時点の異なる成功を返さない。形式/版の先行拒否では安全な集計を行わず全件数0/digest=nullとする。

### 集合・時刻・書式（R2-04/05/07）

corrected_after_as_of_count>0は成功を妨げない。これはinput.as_of時点で選択が存在する必須subjectの訂正あり件数で、履歴future_revision_count（全subjectの未来版数、初版を含む）とは別物。一方を他方の代用にしない。

extra=0にするため投入側でsubject単位に絞り込んでよい。ただし残すsubjectについては判定後のentryも全件渡し、時刻で切り詰めない。外部で削除された履歴の存在をこのAPIが検出できる保証はない。

コード書式を適用する6箇所は、input.expected_codesの各要素、input.sources[group]の辞書キー、選択lots/events行のcode、history.entries[].subject.code、同payload.code、validity_documents[].payload.code。同一subjectを指すコードはすべてUTF-8バイト単位で同一を要求する。

### 例とサイズ（R2-06/08）

専用例には最上位mode/source_originを追加済み。結合CLIの入力ファイル全体にも1MiB（1,048,576バイト）上限を採用する設計で、履歴件数を実質制限する（1件約378バイトならentryのみ約2,700件。ただし入力/期間証拠/ID長/整形分も容量を使うため保証件数ではない）。自動分割・上限引上げはしない。CLIは内側3CLIと同じpacket_cli._mappingによる安全なJSON読取を流用し、link/reparse・読取中の変化・重複キー・非有限値の拒否を維持する。段1より前の読取境界でサイズを検査し、比較は超過のみを拒否する。上限ちょうどはサイズ検査を通し、超過時は結合APIを呼ばず、固定文「履歴と適用期間を検査できません。入力形式とサイズを確認してください。」と改行をstderrへ出し、終了コード2、stdoutは空とする。13キーのJSONやINVALID_BUNDLEを返す経路ではない。サイズ内でAPIまで到達した場合は13キーのJSONをstdoutへ出し、VERIFIED_OFFLINE_BINDINGなら終了コード0、DATA_INCOMPLETEなら2。JSON読取・引数等のCLI例外も同じ固定stderr/空stdout/終了コード2とする。これはファイルのバイト数に対するCLI制限で、メモリ内API引数を再シリアライズして上限を課す規則ではない。

### 追加反証（R2-09）

- 未来だけに現れる余分subjectを拒否する。
- 必須subjectの判定後訂正があり、成功しつつcorrected_after_as_of_count=1になる。
- 全角・内部空白・制御文字・ゼロ幅文字・ID128文字超の書式違反を結合境界で拒否する。

period_evidence_timedはv1では常にfalse。将来trueを認めるには期間証拠自身のobserved_at/recorded_atと履歴選択の追加契約・検証が必要で、既存行時刻だけでは昇格しない（R2-10）。

## 第3回レビュー確定：分類と評価順（R3-01〜07）

本節は以前の分類・先行拒否・digestの一般記述より優先する。結合APIと境界検査は設計段階であり、内側v1実装を変更しない。

### 期間証拠の事前形検査（R3-01）

strict_validityのreasonだけから構造/不適合を逆算しない。結合境界の段3でvalidity_documentsを直接検査する。リスト型を要求し、各要素はid/sha256/payloadの3キーちょうど、payloadはgroup/code/record_sha256/valid_from/valid_untilの5キーちょうど。id/group/codeは文字列、idは内側v1で受理される非空trim済み文字列。sha256とrecord_sha256は64桁小文字hex、valid_from/valid_untilはtimezone付き時刻として解釈できる文字列。sha256はpayloadのcanonical JSONのSHA-256と一致する。canonical化失敗も構造破損として拒否する。ASCII書式の追加制限は段4で検査する。

空のvalidity_documentsは証拠欠落として段5へ渡す。一方、空または非listのhistory.entriesは履歴v1の入力契約違反であり、段3のINVALID_BUNDLE単独とする（段2のmode値差があれば先にそちらで拒否）。重複IDは文書識別の内部整合破損として段3で拒否する。異なるIDの重複subject、group="prices"等の対象違い、正しい型のvalid_from>=valid_until、行hash差、期間外は形検査を通し段5で不適合とする。これにより形検査後のstrict_validityの通常の不合格をVALIDITY_NOT_SATISFIEDへ分類できる。ただしINPUT_*は例外なく段3のINVALID_BUNDLEへ写像する。

### 全順序（R3-02）

段1：最上位5キー・mode・source_originの不一致はINVALID_BUNDLE単独。段2：historyが辞書でmodeキーが存在し、その値が依存v1と異なる場合のみHISTORY_MODE_MISMATCH単独。段2は依存APIを呼ばず直接判定し、historyのキー集合やmode以外の値の妥当性を条件にしない。mode値差と余分キーまたはdecision_at欠落が併存しても段2で拒否する。historyが非dictまたはmodeキー欠落なら段2は拒否せず段3のINVALID_BUNDLE単独とする。段3：strict_input/履歴の構造・内部整合不成立、期間証拠の上記事前形検査不成立、入力由来の不成立は段3で実行するinspect_strict_inputの失敗として判定しINVALID_BUNDLE単独。strict_validityは段3では呼ばず、段5の時点一致後にのみ呼ぶ。そこでINPUT_*が出た場合はR4-06の防御により段3相当の結果へ戻す。history.source_origin差、input.mode/input.source_origin差を含むその他の部品の版差・封筒差は段3のINVALID_BUNDLE単独へ写像し、新しい理由を追加しない（最上位封筒差は段1、history.mode値差だけ段2が優先）。段4：追加書式違反はCODE_FORMAT_INVALID/IDENTIFIER_FORMAT_INVALIDを併記可。段5：先に時点一致を判定し、不一致ならDECISION_TIME_MISMATCH単独で打ち切る。一致時のみHISTORY_SELECTION_MISSING/ROW_HASH_MISMATCH/VALIDITY_NOT_SATISFIED/EXTRA_SUBJECT_PRESENTを併記可。同じ段の理由だけ重複除去・昇順で返し、段が異なれば最初の不合格段で打ち切る。

段3でstrict_validityの通常の期間不適合まで拒否してはならない。それらは段5へ送る。段1〜4はrequired_subject_count/matched_subject_count/extra_subject_count/corrected_after_as_of_countの4件数すべて0、selection_sha256=null。診断件数を返せるのは段5のみ。全段で固定false/trueフラグを維持する。

### 時点不一致と件数（R3-03/05/06）

時点不一致は段5。required/extra/corrected_after_as_of_countを返すがmatched=0、selection_sha256=nullとする。別時点の選択digestを保存・比較させない。corrected_after_as_of_countは常にinput.as_ofを基準に数え、history.decision_at差で基準を変更しない。時点不一致時はDECISION_TIME_MISMATCH単独とし、他の比較理由を生成・併記しない。成功には時点一致を必須とする。段3の部品構造・内部整合検査は先に完了させるが、時点不一致で打ち切る際は以降の部品比較を呼ばず、strict_validityも呼ばない。そのため、この経路では段5の遅延INPUT_*防御には入らない。

requiredはstrict_inputの検証済みexpected_codesから必須subject集合を作って算出する。strict_validity.expected_evidence_countを再利用しない。壊れた入力由来の診断件数を結合の根拠にしない。

### 契約と運用（R3-04/07）

strict_validityのINPUT_INVALID_AS_OFを含むINPUT_*系はすべてINVALID_BUNDLEへ写像する。INVALID_BUNDLEの原因特定は内側3CLI（aitrader.strict_input_cli / aitrader.evidence_history_fixture_cli / aitrader.strict_validity_cli）を個別に実行する。結合APIは内部理由や原本を要約・出力しない。

束をそのまま3CLIへ渡さず、input、history、{mode:strict_validity_fixture_v1,source_origin:offline_fixture,input,validity_documents}の各入力へ分ける。診断用ファイル・実行結果はDropbox外に置く。個別成功でも結合成功を意味しない。

## 第4回レビュー対応（R4-01〜06）

R4-01/02は上記全順序と時点不一致節を直接修正した。段2は値の版差だけを扱い、非dict/キー欠落は段3へ。段5内の時点不一致は単独理由で打ち切り、required/extra/correctedは返すがmatched=0、selection_sha256=null。

R4-03：内部共用選択モデルは評価時点を引数に取る設計とする。検証済み履歴を使い、v1では時点一致時に選択とcorrected_after_as_of_countを同じ評価の1回で得る。不一致時はcorrectedのためにas_ofで1回だけ評価し、decision_atでの結合用選択・digest評価は行わない。二重評価は将来v2で時点差を許す場合の設計上の余地であり、v1の結合評価では実施しない。履歴の構造・内部整合の検証と、結合診断用の時点選択を分離する。元bundleの時刻を書き換えず、receiptのcanonical JSON完全一致も変更しない。correctedはas_of時点で選択が存在する必須subjectの判定後初出訂正版の有無を数え、公開future_revision_countで代用しない。共用モデル本体は未実装。

R4-04：段3を通った後のINVALID_VALIDITY_DOCUMENTSは空リストによる証拠欠落のみを意味し、段5でVALIDITY_NOT_SATISFIEDへ写像する。証拠欠落の専用理由を作らない。

R4-05：結合mode自体の版差は意図的に段1のINVALID_BUNDLE単独とする。束全体を解釈できない以上、他の診断が意味を持たないためである。履歴modeの版差との非対称は意図的で、10種目の理由コードを追加しない。

R4-06：段5の呼び出し中にINPUT_*を観測した場合も、段3の結果としてINVALID_BUNDLE単独・4件数すべて0・selection_sha256=nullを返す。既に計算した診断件数や他の理由を破棄する。通常入力では到達しない防御経路であり、時点一致後の部品呼出で内部前提の崩れを検出した場合は他の比較理由を優先しない。時点不一致時は当該部品を呼ばず、この防御は観測されない。

## 第5回レビュー対応（R5-01〜05）

R5-01：段2はhistoryのdict/mode存在/値差だけを直接検査する。依存APIのINVALID_MODEの写像で代用しない。
R5-02：history.mode以外の部品版差・origin差は段3のINVALID_BUNDLE。最上位封筒の段1を維持する。
R5-03：correctedはas_ofで選択のある必須subjectに限定する。未来初版＋未来訂正版だけのsubjectは0、選択のある別subjectの後日訂正は数える。従って別subjectの選択欠落とcorrected>0が同時に存在する場合はあり得る。
R5-04：v1の結合用評価は一致時1回、不一致時as_ofで1回。構造検査は先行し、不一致時のstrict_validity呼出とdecision_at選択の追加評価はしない。
R5-05：1MiBはCLIの段1前の読取制限。超過は検査非呼出、固定stderr、終了2、stdout空。サイズ内の通常JSON結果と区別する。

以上は設計文書の確定であり、内部共用選択モデル・結合API/CLI・境界検査の実装完了ではない。既存v1部品・既存試験期待値・専用例は変更しない。

## 第6回レビュー対応（R6-01〜04）

安全JSON読取の流用、段3の入力評価主体（inspect_strict_input）、correctedの選択あり限定、空履歴と空期間証拠の段の違いを上記本文へ反映した。第6回レビューで契約上の実装阻害事項なしと確認され、結合API・CLIと内部共用選択モデルの実装に着手する。既存履歴v1の公開出力・受入・receipt完全一致を維持して内部を共用化する。過去節の未実装という記述は各レビュー時点の記録で、最新の実装・実測状況は本書末尾とREADME末尾を参照する。

### 実装入口と内部分離

履歴の `_validate_history` は時点選択を行わず内部整合を検証し、`_ValidatedHistory.evaluate(at)` が検証済みrevisionの選択・互換digest・全subject・訂正subjectを算出する。公開履歴APIはdecision_at、結合APIは段1〜4通過後にas_ofで1回だけ評価する。結合のmatchedは選択行hashと入力行hashが一致し、期間証拠がsubjectごとに一意で同じ行hashを持ち半開区間を満たす件数。strict_validity全体の合否と件数は混同せず、余分な期間subjectによって全体不適合でも個々の正常subjectのmatched診断は維持する。

CLI実行例（build-codexで実行）:

```powershell
python -m aitrader.history_validity_binding_cli --input examples/history_validity_binding_valid.json
```

入力例はrequired=matched=2、extra=corrected=0でVERIFIED_OFFLINE_BINDINGとなる人工束。成功でもperiod_evidence_timed/ready_for_live/current_signalはfalse。実売買の準備完了や期間証拠の当時入手可能性を証明しない。

## 第7回レビュー対応（R7-01〜03）

R7-01：成功条件へreason_codes空を本文で明記。件数一致だけで成功を判断しない。
R7-02：AttributeError追加は今回は採用せず、現在の6種類の結合API例外境界を維持する。通常入力からの到達例は原レビューで未検出で、内部退行の提案に対して既存4モジュールの例外契約を一括変更する必要はない。無条件のException捕捉でプログラム不具合を隠さない。将来の共通境界見直しでは入力・履歴・期間・結合の4入口をそれぞれ調査し、追加対象/固定応答/観測手段/互換性を先に定義する。4モジュールが現在同じ広さの捕捉を持つと主張するものではない。CLIの既存Exception捕捉と、メモリAPIの例外境界は別契約である。
R7-03：Claude追加96件を既存のまま再検証し、Codexの複数銘柄製品試験を専用新規ファイルへ追加する。専用例・既存試験期待値・製品コードは変更しない。最新実測はREADME末尾。

次工程は[期間証拠の観測・記録時点の契約候補](PERIOD_EVIDENCE_TIMING_PLAN.md)。現行v1の成功条件やperiod_evidence_timed=falseは維持し、新mode採用・保存・実取込・日次接続へ拡大しない。

## 第8回R8-05：メモリAPIの例外境界の前提

本APIの検査対象はjson.loads由来の素のdict/list/str/int/float/bool/Noneによるデータ（同じ素の型で直接構築した値を含む）。JSON由来という理由だけで内容を信頼・受理せず、構造・時刻・hash等の検査を行う。非有限数等は正常入力としない。__getitem__やstripなどを差し替えたPythonサブクラス/敵対的オブジェクトの直接注入は例外隔離の契約範囲外。CLIの安全JSON読取とメモリAPIの6種類のcatchは別契約で、AttributeErrorを今回追加しない。将来4入口を見直す際はstrict_input参照解決の内側catchがValueError/TypeError/OverflowErrorである点も確認する。

R8-01〜04の期間履歴最小案はPERIOD_EVIDENCE_TIMING_PLAN.mdで設計採用。結合v1は不変、timed昇格は将来結合v2に限る。

## 第9回対応：期間履歴からの写しと結合v1の限界（R9-05）

[期間証拠履歴v1](PERIOD_EVIDENCE_TIMING_PLAN.md)から結合v1へ渡すvalidity_documentsは投入側が作る写しである。結合v1は、その写しが期間履歴の選択結果から作られたか、欠落・置換されていないかを検証せず、写しの出所や同一性を保証しない。この限界のためperiod_evidence_timed=false固定を維持する。

期間履歴単体が受理する非空trim済みrevision_idも、結合へ写す時点で結合境界のASCII ID書式`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`を満たさなければならない。trim、変換、別IDへの置換で救済せず、単体成功を結合の書式合格とは扱わない。

期間履歴側の選択digestと本APIのselection_sha256は同名だが、前者は期間revisionのpayload自己hashを含む射影、後者は行履歴の選択digestであり、異なる定義である。同じdigest値になっても相互に代用しない。詳細な写し方と保証境界は[期間証拠の観測・記録履歴](PERIOD_EVIDENCE_TIMING_PLAN.md)を一次参照とする。結合v1の13キー・閉じた9理由・成功条件は変更しない。
