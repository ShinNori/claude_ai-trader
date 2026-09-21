# 次に判断する限定契約

**2026-09-12更新：** ユーザーが新しい専用モードを選択し、人工原本付き入力のメモリ内検査と読取CLIをstrict_input_v1として限定採用した。[現在の契約と操作](STRICT_INPUT.md)を参照。以下の市場3項目「未採用」は以前の状態であり、今回の限定範囲は採用済み。外部資料の真正性、公表時刻の実証、turnover/factor、価格の鮮度、単元有効期間、全ページ網羅性、DB取込・既存home移行・売買への接続は引き続き未採用/未実装である。

2026-09-11。これは [実データ入力readiness](DATA_INPUT_READINESS.md) と [通知統合契約](NOTIFY_INTEGRATION_PLAN.md) に残る判断を、小さな単位へ整理した文書である。表のdirect queue時刻下限は作成後の同日に限定採用された。市場入力3項目の推奨案は引き続き未採用である。共通仕様と実通信許可は変更しない。

## 判断表

| 項目 | 現行動作 | 未決点 | 推奨案と代案 | 互換影響 | 採用後のoffline受入ケース | 実通信なしでできる準備 |
|---|---|---|---|---|---|---|
| direct queue時刻下限（限定採用） | 主系mock delivery/reconcileの既存防御に加え、direct `Notifier.flush`のactive対象行と`reconcile_sent`のSENT対象行も、選択全件を副作用前に検査する | `now >= created_at, updated_at, all attempt.at`を必須とする。空選択、指定外行、各APIの既存terminal filterは対象外 | **採用:** 選択された処理対象だけのqueue履歴下限。**不採用:** Ledgerの通知状態時刻を通知作成時刻以後へ一律制限する広い規則 | 過去時刻を使うdirect callerは拒否される。schema、content hash、state遷移、Q04履歴は不変 | flushの前後/同時刻、複数行後続不整合時の先行不変、reconcile、`keys=None`/明示/空/指定外、合法terminal filter、別timezone同一時点、Q04回帰 | Temp SQLiteとstub transportだけで検証可能。最終件数はrootの後続実測を参照 |
| 調整OHLCと数量系 | 調整OHLC4項目が揃えば採用し、一つでも欠ければraw OHLC一式へ戻る。volumeは独立に調整値を選ぶため基準混在があり得る。inspectionは`ADJUSTMENT_BASIS_UNVERIFIED`を表示 | 部分欠損rowを拒否するか、raw一式へ揃えるか、係数補正するか。volume/turnover/factorとの同一基準 | **推奨案:** 外部項目契約を確認するまで、部分調整や基準混在を実データ適格性では拒否し、研究互換経路は警告付きで維持。**代案:** raw一式へ統一。係数からの独自補正は根拠確定後だけ | 既存legacy研究結果を変えないため、厳格判定は新mode/明示検査として分離が必要。既存取込を直ちに拒否へ変えると互換影響が大きい | 調整4項目の全有/部分欠損/全無、volume/turnover有無、NULL、既存raw/adjusted互換、拒否時transaction不変、inspection warning | 公式仕様を断定せず、人工responseの組合せ表と期待するbasisを判断用fixtureにする |
| 推定公表日 | 公表日欠損時は基準日+4暦日を研究用fallbackとして保存。`publication_estimated`は最新batchのboolで上書きされ、過去推定行が残ってもFalseになり得る | fallbackをどの用途で許すか。推定有無を行、取得run、DB全体のどこへ保存するか | **推奨案:** 行単位sourceとrun履歴を新専用modeで保持し、推定行は実データ適格性を満たさない。**代案:** DB全体boolを全行から再計算。fallback全面拒否は研究互換を変えるため別判断 | schema・既存DB移行が必要。旧`legacy-v1-unverified`のboolを完全な履歴へ自動昇格しない | 明示日/fallback混在、別期間追記後も旧推定来歴保持、訂正、同一batch no-op、hash/run整合、旧home非移行、inspection ready=false | canonical人工run-rowを使う既存provenance fixture試作を判断材料にできる。実データ証明とはしない |
| response原本・単元・event | legacy取込はresponse原本、取得run、期間、件数、全体hashを保存しない。packetのlots省略は100、event欠損はUNKNOWN。安全なJSON読取とsnapshot hashは実装済み | 正本、contract版、取得期間・訂正履歴、銘柄別単元、決算/規制eventを誰がどの形式で固定するか | **推奨案:** 非機密固定原本manifestと、検証済みlots/eventsを必須にする新しい明示modeを先に定義し、欠損は`DATA_INCOMPLETE`。**代案:** 現行100/UNKNOWNを研究専用で継続。外部応答を即時正本扱いする案は採らない | 実データmodeでlots省略を拒否すると既存CLI互換が変わるため、legacy研究modeは維持し新modeへ限定。snapshot hashは取得receiptの代用にならない | 原本path/hash/contract/期間/件数、重複・訂正、lots欠損/不一致、event欠損、全入力同一時の決定性、差替え検出、`DATA_INCOMPLETE`で予約0 | 取得済みと称さない人工fixture、manifest schema、銘柄別lots/events fixtureを作り、ネットワークを禁止した受入を先行作成できる |

## 最小の次採用単位

最小単位だった **direct queue時刻下限** は限定採用された。対象は`flush`のactive行と`reconcile_sent`のSENT行のうち、その呼出しが選択した行だけである。DB schema、hash、状態遷移、Q04のLedger規則は変えない。

市場入力は、まず「部分調整を実データ適格性で許すか」と「推定公表日をどの保存単位で識別するか」を別々に決める。その後に原本manifest・単元・eventの新modeを決める。これらを一括実装すると、研究互換、schema移行、正本の定義、適格性判定が混ざり、反証の原因を分離できない。

## 自動継続を妨げている理由

direct queue時刻下限はQ04を変えない範囲で判断済みである。残る市場入力3項目は複数の安全な選択肢が既存共通仕様から一意に決まらない。調整値を推測補正する、最新batchのboolを全履歴の証明にする、100株既定やUNKNOWN eventを実データの確認済み値へ読み替えることはできない。

したがって、これらの判断前に自動で製品変更を続けると、互換性またはデータ適格性の新契約を無断採用することになる。判断までの間も、固定人工fixture、故障注入、期待する拒否位置と不変条件の試験案はオフラインで準備できる。実API、実審査、実通知、注文への接続はどの選択にも不要であり、別の明示許可事項として残る。
