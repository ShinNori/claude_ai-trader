# 主系の台帳片側保存からの復旧案

2026-09-11、Codex読取レビュー。主系INTENTからの書込復旧は未採用契約を含む設計案で、未実装である。読取専用診断は末尾の追記のとおり実装済み。参照: [RUNNER.md](RUNNER.md)、[PHASE2_INTEGRATION.md](PHASE2_INTEGRATION.md) の台帳予約・クラッシュ復旧、`aitrader/runner.py`、`aitrader/mock_delivery.py`。

## 現在の動作

runnerはゲート承認後、orchestration.sqliteのcandidatesにINTENT（候補全体hash、日付、方向、承認判定、owner run_id）を保存し、台帳create_noticeでCREATEDと予約を保存、APPROVEDへ進める。その後journalの同じトランザクションで候補outboxを保存し、candidatesをAPPROVEDへ更新する。台帳とjournalを跨ぐ原子性はない。

同run・同入力のINTENT再開で台帳通知がなければ、現在時点でゲートを再評価できる。通知が既にあればCREATEDでもAPPROVEDでも「台帳だけに通知が存在します。照合が必要です」と停止し、予約を保持する。別run所有のINTENT、候補hash変更、参照価格・カレンダー変更も拒否する。完了runは保存結果の参照であり、新たな送信許可ではない。

現在の `reconcile_prepared_mock` は、完了runに紐付く通知キューの耐久SENTを模擬台帳へ反映するAPIである。runnerの未完了INTENTを復旧するAPIではなく、原本未完了runには使用できない。

## 状態ごとの案

| journal / 台帳 | 現状 | 提案する扱い |
|---|---|---|
| INTENT / 通知なし | 同owner・同入力で再評価可能 | 既存経路を維持。新しい現在時刻で承認条件を再検査 |
| INTENT / CREATED | 予約保持して停止 | 原本・所有・台帳履歴・二承認を検証し、現在も許可される場合だけ既存予約のままAPPROVEDへ進める案 |
| INTENT / APPROVED | 予約保持して停止 | 同じ検証後、既存承認のjournal/outbox反映のみ行う案。create_notice禁止 |
| INTENT / SENT・取消・約定等 | 停止 | 自動採用しない。外部書込や後続処理の可能性があり照合待ち |
| APPROVED / outbox欠損 | 停止 | 本来journal内原子処理なので単純クラッシュ以外を疑い、勝手に再構築しない |
| 通知あり / INTENTなし | 停止 | 所有関係を証明できないため採用しない |
| 完了run | 保存結果参照 | 通知・配信側の明示APIへ分離し、過去の結果を現在の承認に変えない |

## 継続前に必要な照合

runner-lockの取得から照合・台帳操作・journal記録・結果保存までを同一操作として直列化する。専用mock homeのみとし、直接Ledger/Notifier/CSV/Webhook書込との混用は禁止する。このロックは外部書込元を強制排除するものではない。

候補IDだけの一致では不十分。runsのrequest_hash、manifestの候補・Verdict hash、保存済み入力、candidates.hash、owner、day、side、台帳proposal全体を照合する。通知IDやpacket_hashだけが同じでもイベント・理由など全体が異なれば拒否する。両judgeが異なる名前で存在し、それぞれ同一候補ID/hash/run_id、mock情報、有効なAPPROVE、07:15未満の受信であることも再確認する。

候補台帳への書込が「このINTENTによるもの」と証明できる必要がある。現台帳Proposalにはowner run_idがないため、INTENTのownerと同じProposalの存在だけでは外部書込を厳密に識別できない。initial_ledger_seqだけも複数候補処理の間に進むため十分ではない。候補単位のcreate直前seq、採用した台帳イベントID/seqなどの保存契約を追加するか、単一書込主体と変更のない履歴を厳密に要求するかは未決である。

## 予約・現在リスクの再評価

CREATEDで資金・株数は既に予約されている。現在のledger.viewへ同じ候補を普通にevaluateすると、自分の予約を再度必要資金として数える可能性がある。予約を一度取消して作り直す方法は禁止する。

提案は、対象候補の既存予約だけを除外して評価する公開view/APIを追加し、他候補の予約・未確認約定・残高変化は維持すること。BUY資金だけでなくSELL予約株数、集中度、保有銘柄数、日次件数にも同一候補の重複計数がないか確認する。日次枠は既存runnerと同じ候補ID集合とし、自分のINTENTは一度だけ数える。現在残額が足りない場合は継続せず予約を保持して照合待ちとする案。

過去のゲート承認結果をコピーするだけでは、後からの出金、未確認約定、STOP、損失・DD条件を反映できない。反対に現在評価で過去承認を履歴から消すことも避ける。元判定、復旧時判定、復旧時刻を別記録にする必要がある。

## 時刻・停止と未決事項

復旧時刻は元run開始・両受信・対象台帳イベントより前を拒否し、執行日当日限定、07:15以上は新たな承認・配信可能化を行わない案。通知側の修復時刻を初回成功時刻と扱わない原則と同様に、復旧時刻を元承認時刻へ遡らせない。

既存APPROVEDを締切後にjournalへ記録するだけなら履歴修復として可能という別案もある。ただしそのoutboxが後続通知へ誤って流れない契約が必要で、現時点では採用しない。STOP中のNEWを継続させず、EXITとの違いを独立して試験する。現在の新規managed-v1 homeでは、審査・BUY予約直前・通知準備/配信の管理STOP確認と共通runner-lockが接続済みである。legacy homeや共有ロックに参加しない直接writerまで統一されたという意味ではなく、復旧だけに独自の停止仕様を入れない。

初期設計の「二承認確認不能なら未送信を確定してREJECTEDへ」は、未送信の証明と予約解放の明示契約が必要である。現在の停止動作から自動取消へ黙って変更しない。判定不能・入力衝突・通知キュー存在・外部書込疑いは予約保持の照合待ちとする。

## 実装前の受入試験案

各停止点へ例外を注入し、INTENTのみ、CREATED後、APPROVED後、journalトランザクション前後から同run再実行を検証する。create_noticeの呼出回数と予約増分、日次枠、全候補の最終状態、成果物の再構築を確認する。

拒否試験は別owner、同ID別hash、同hash別イベント、片側承認・未来受信、過去復旧時刻、締切直前/同時/直後、STOP、出金後余力不足、他候補予約、部分約定、SELL予約競合、台帳seq変更、outboxだけ欠損を含める。失敗後の再試行でも同じ予約・イベントを重複追加しないこと、無関係runを変更しないこと、ロック解放、実通信禁止も確認する。

まず観測専用の復旧診断（候補別状態・原本一致・所有証拠・未決理由）を実装し、その結果と上記未決契約を採用してから書込APIを追加する順序を推奨する。診断だけで予約を解放したりrunnerを再開したりしない。

本メモ作成ではコード変更・追加試験・自動復旧・見張り再開は行っていない。Claudeへの引き渡し不要。

## 追記：読取診断を実装（2026-09-11）

上記設計の第一段階として `aitrader.runner_diagnostics.diagnose_mock_run(home, run_id, execution_day)` を実装した。原本manifest/result、候補・Verdict成果物hash、journalの候補とowner、台帳の通知状態の観測、承認outboxを読み取り照合する。APIのexecution_dayはdate型。診断はJSONを返すだけで、DB・診断ファイルの保存や復旧は行わない。

既存の模擬実行先へ、次のPowerShellで診断できる。以下のパスは記録済み実測例の一時フォルダであり、削除済みの場合は自分の保存先へ置き換える。診断のために新しいDBを作成しない。

```powershell
Set-Location -LiteralPath 'D:\Desktop\Ace-1_Dropbox\Ace-1 Dropbox\Norimitsu Shin\!!!※則光用\999_投資関係\ai-trader'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = "$PWD\ops;$PWD\build-codex"
$diagnosticHome = 'C:\Users\s\AppData\Local\Temp\ai-trader-synthetic-full-xx60g3zp\home'
& 'C:\Users\s\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m aitrader.runner_diagnostics --home $diagnosticHome --run-id synthetic-pipeline --day 2026-08-31
```

停止中のDBが対象である。orchestration.sqliteまたはledger.sqliteにWAL/SHMがある場合は、未反映履歴を無視して成功と判定せず、`NEEDS_RECONCILIATION`・`LIVE_DATABASE_UNSUPPORTED`を返してDBを読まない。WAL/SHMを利用者へ削除させる手順は設けない。ない場合だけ `mode=ro&immutable=1` で開く。読み取り前後にsidecarを確認するが、外部書込との競合や複数DBの同時点整合を保証するものではない。

| 分類 | 意味 |
|---|---|
| COMPLETE | 観測した原本・候補・台帳・outboxに対象の不整合を検出しなかった |
| MISSING | 必要なDB・run・候補記録がない |
| CONFLICT | 原本・hash・候補内容・outbox等が一致しない、または読み取れない |
| NEEDS_RECONCILIATION | 途中処理、台帳片側保存、複雑な履歴、稼働中DBなどで照合が必要 |

すべての分類で `automatic_resume_allowed=false`、`repaired=false`、`snapshot_consistent=false` を返す。COMPLETEも現在の売買承認や再開許可ではない。約定・訂正など複雑な台帳履歴は簡易観測から現在状態を決めつけず、UNKNOWNと照合待ちにする。

初回独立試験では通常のmode=roでも空WAL/SHMが作られる不具合を検出したため、上記の停止中DB限定方式へ修正した。修正後はlive WALを持つDBも含め、DB・sidecar・成果物の内容不変を確認した。追加試験を含む最新の件数と結果は [README](README.md) を参照する。診断APIが完成してもINTENT/CREATED/APPROVED片側保存からの書込復旧は未実装であり、前述の所有証拠・既存予約再評価などの契約は未決のままである。

## 2026-09-11 候補の所有境界の補強

未完了runが既存candidateを利用するときは、INTENTだけでなくAPPROVED/REJECTEDもowner・day・side・proposal全体hashが現在runの入力と一致することを必須にする。異なるrun_idで同じ完成候補を現在の承認として流用しない。別runで別候補を処理する既存の日次件数制限は維持する。

保存済み判定のproposal_id/status/gate形状、APPROVED outboxのkey/mode/delivery/kind/proposal全体も再利用前に確認する。未知stateや矛盾した判定は停止し、既存予約を解放・再作成しない。診断のowner照合も全stateへ適用する。

同runの完了結果の再読は保存履歴としての既存契約を維持し、現在の再承認とは扱わない。このfast pathは全journal行を再検証する経路ではない。改変が疑われる保存物の整合は読取診断で確認し、診断CONFLICTを無視して履歴から新規承認や配信を行わない。

raw homeの親と主要file/SQLite sidecarは、resolveでリンク情報を消す前に検査する。存在する通常sidecarをwriterで一律拒否する変更ではなく、リンク・reparse・型不正の拒否に限定する。非協調な外部変更の完全排他やINTENT書込復旧、legacy STOPの仕様変更は含めない。

## 出力pathの事前確認（2026-09-11）

runnerは日付/run directoryとmanifest・proposals・verdicts・gate_results・result・failureの各JSONについて、raw親のlink/reparseと通常型をDB接続前およびロック取得直後に確認する。既知の保存先異常を予約前に止めるためであり、同run完了結果を現在の承認として再検証するものではない。検査後の非協調差替え、電源断、クロスDB/複数fileの一括コミットは引き続き未保証。
