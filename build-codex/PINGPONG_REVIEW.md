# 自動引き渡し2方式の実装レビュー

> 対象版について: 本文は06:24の検証に使用した受領版（末尾SHA256）へのレビューである。作業途中で共有フォルダに別担当の変更が入り、pingpongのH01修正、その後の新エンジン/ラッパー等の追加を確認した。**変更後の現行版を不採用と判定したものではなく、現行版の再レビューは未実施**。自動連携は `tools/自動連携_Codex依頼.md` に別枠化されたため、投資開発側のBは本レビューでは変更していない。下記の「現状」「今回の復元」は受領時点の評価・旧依頼内容を指す。末尾の更新記録を優先する。

確認日: 2026-09-09 JST。開始時刻は未取得、最初の時計確認は06:22。依頼の発行表記06:35は実機時計より未来だったため、作業時刻として転記しない。

## 判定・採否

**現状の無人常駐は不採用。監視方式を修正後の第一候補、ピンポン方式を修正後の代替候補とする。** 監視方式の6条件は一部の通常経路では働くが、排他・公開完了・質問待ちを保証しない。ピンポン方式は現行193行目で構文エラーとなり起動できない。以下のピンポンの実行時所見は、この構文エラーを直した後に到達するコードの静的レビューである。

tools/・AGENTS.md・ops/・共通仕様本文は未編集。実LLM、発注、証券接続、LINE、Git初期化・commit、常駐登録は実施していない。実行検証はWindowsの一時フォルダにコピーし、通知環境変数を子プロセス内で空にし、必ず-NoGitを指定した。実行ログ本体はDropbox外、共有用の検証結果だけを本フォルダへ保存した。

重大度: A=起動不能・二重実行・作業消失・禁止操作につながる修正必須、B=運用契約・復旧・誤判定の修正必要、C=表示改善。行番号は今回読んだ原本の番号。

## 指摘表

| 指摘ID | 重大度 A,B,C | 事象 | 修正案 |
|---|---|---|---|
| H01 | A | pingpong.ps1:193 の文字列内 `$agent:` はスコープ付き変数として解析され、InvalidVariableReferenceWithDrive。-NoGitでも構文解析で停止。PS5.1で実測。 | `${agent}:` に修正。例: `Git-Snapshot "pingpong r$round ${agent}: $($result.summary)"`。PS5.1 ParseFileを入口検査にする。 |
| H02 | A | watch:195–203 はDryRun判定144より前にlastHash/日次回数・ロックを保存しEnsure-Gitを呼ぶ。試運転だけで本実行対象が消えることを再現。pingpong:186もDryRun前にGit初期化。両方がprompt/logを書き、通知フックも実行可能。watchの-DryRun単独は終了せず常駐する。 | DryRunを最上位で読取・表示専用に分岐し、状態・ロック・Git・履歴・通知を変更しない。DryRunは1回で終了。previewにCLIインストール必須としない。 |
| H03 | A | watch:178–197 はstate読取→Test-Path→state保存→WriteAllTextの非原子処理。同時起動したW1/W2が両方ロックなしを見て同じBを起動できる。codex.lockとclaude.lockは別、pingpongにはロックなし。watch:213は自分が取得していないロックまで例外時に削除し得る。 | Dropbox外のプロジェクト共通ロックをFileMode.CreateNew＋保持ハンドル等で取得してから状態を読み直す。所有トークン・PID・開始時刻を検証して所有者だけ解放。watch両側、pingpong、手動ランチャーが同じロックを使う。別PCにはローカルロックが効かないため実行PCを1台に固定。 |
| H04 | A | watch:151 / pingpong:156 はcmd.exeのPIDだけをStop-Process。子のCodex/Claudeやテストが残れば、watchはfinallyでロックを消して次のBを起動できる。クラッシュ時は逆に古いlockが永久停止を起こす。 | Job Object等で起動したプロセスツリーを管理し、終了確認までロックを保持。timeout/errorは永続halt状態にし、人間の明示復旧まで次のBを受けない。古いlockはPID再利用も確認し、無条件削除しない。 |
| H05 | A | watch:184–188 はファイルmtimeの古さだけを安定性とする。古いmtimeの部分文書は起動できた。60秒を超える保存中断・同期元時刻の保持にも弱い。hash取得、mtime取得、要約取得、CLIによる再読取が別々で、H1を処理済みにしてH2を実行する競合もある。pingpongにはStableSec相当がない。 | 同じ内容を複数回観測し、ローカル観測時刻から安定時間を計測。依頼ID・本文hash・明示ready状態を検証し、ロック取得後に再検査。不変スナップショットをCLIの読取対象にする。単なる待機秒数増加では不十分。 |
| H06 | A | watch:191 はBではなくファイル全体のmtimeとQUESTIONSを比較。未解決質問より新しく履歴だけ保存しても解除される。同時刻も通る。pingpong:151/166は開始後の更新だけを検出し、前からある未解決質問を再起動時に無視する。 | 依頼IDに紐づくwaiting_human/resolved状態と明示回答・再開操作を使う。両方式とも起動前に未解決状態を確認。mtimeやQUESTIONSファイルの存在だけで解決を推定しない。 |
| H07 | A | watch:63–71 / pingpong:50–59 のB抽出は###保留中の依頼まで含む。本ファイルで保留だけ変更してhashが変化し、現行依頼行欠落時には保留の古い依頼を取得する。B見出し欠落では全文へフォールバック。複数依頼行は先頭だけ採用。`\s*`が改行を消費するため、空の「今回の依頼:」の翌行も要約として採用された。 | B境界を明示し、保留はB外の別セクションへ移す。Bなし・依頼行0件/複数・空要約・書式不正はエラー停止。水平空白は`[ \t]`、値は改行を含まないものに限定。Bは1依頼、複数工程は本文の番号付き項目へ。 |
| H08 | A | watch:195–200 でCLI存在確認より先に処理済み保存。未インストール、起動例外、timeout、途中終了でも次回同じBは抑止。Save-Stateは直接上書きなので途中中断や同時保存でJSON破損・回数消失もあり得る。 | pending/running/completed/failed/waiting_humanを分離し原子的に保存。成功と失敗を同じ「処理済み」にしない。失敗はhaltにし、再試行ID・人間の明示復旧で再開。副作用不明な失敗を自動再実行しない。 |
| H09 | B | watchはlastHash1個のみ。A→B→AでA再実行を再現。state消失・別PC・手動実行後の初回watchでも既存Bを新規とみなす。pingpongには処理済みIDの永続記録がなく起動し直すと同じ依頼を繰り返す。 | request_id、reply_to、本文hash、処理結果を永続履歴に記録。再試行は新ID＋retry_of。初回は基準登録のみか明示初回実行を選ぶ。状態消失は自動再初期化せず停止。 |
| H10 | B | watch:180は日付が変わると回数をリセット。日次上限は当日の暴走量を制限するだけで、翌日から同じ往復が継続する。両側でそれぞれ10回、並行競合で上限を超える可能性もある。pingpongのMaxRoundsも再起動でリセット。 | chain_id単位の累積ターン数/費用/期限と共通停止状態を設ける。日次枠は補助として採用。状態はロック内で更新し、上限到達後は明示再開。 |
| H11 | B | 両方式131はsandboxだけ指定し、承認・hooks・MCP等の実効設定を固定していない。workspace-writeはai-trader全体への書込を許し、役割別のbuild-codex限定ACLではない。Aで許された親の修正提案書は-C配下ではなく、権限不足による停止もある。実機CLIヘルプではHOME関連警告も出た。 | 管理された無人用設定と `codex -a never exec --sandbox workspace-write ... -` を使用。必要な書込範囲を事前整備し、拒否時はQUESTIONS/失敗状態にして止める。hooks/MCP/ネットワーク・CLI/認証環境を事前確認。承認回避のためsandbox無効化はしない。 |
| H12 | B | watch:54–57 / pingpong:43–45 は実行ログ・state/lockをDropbox内へ保存し、設計書/AGENTS/共通仕様の実行時ファイル分離と不一致。複数ホストの同期はロックの代替にもならない。 | `${AI_TRADER_HOME}/handoff/<project-id>/` 等のDropbox外へ移す。共有MDには最小限の結果要約だけ。CLIの必要書込先も無人用設定と整合させる。 |
| H13 | A | watch:203 / pingpong:218 は環境変数PINGPONG_NOTIFY_CMDをcmdへ渡す。LINEを含む外部送信が可能で、今回AのLINE禁止と衝突。DryRunでも通る。watchではB由来のsummaryも埋め込む。 | 今回の運用では通知フックを無効化。将来採用時は独立した明示許可と固定実行先・構造化引数が必要。AI出力やBをシェルコードへ連結しない。 |
| H14 | A | Ensure-Git/Git-Snapshotの`git add -A`は全担当の変更を取り込み、既存.gitignoreを検査しない。新規ignoreも.env以外の秘密ファイルや.sqlite-wal/.duckdb等を網羅しない。commitは役割やテストの弱体化を防止しない。native終了コードも検査せず成功を報告できる。 | 初期化をランチャーから分離し既定-NoGit相当。採用するなら明示した対象ファイルの差分/秘密検査後のみstageし、終了コードを検証。既存の人間のstageを混ぜない。全件commitを保護機構と呼ばない。 |
| H15 | B | 両方式132/137のモデル名は引用・検証なしでcmd文字列へ連結。シェル特殊文字を含む入力で別コマンドになり得る。`cmd /c`はAutoRunや遅延展開設定にも依存。Get-CommandはPS関数/aliasも認識するがcmdから同じ名前を実行できるとは限らない。 | 実ファイルの絶対パスを解決し、モデル値を厳密検証。可能ならProcessStartInfo＋標準入出力へ直接接続。cmdが必要なら`/d /v:off /s /c`等を含め引用規則を検証。通常の日本語・空白・!パスのリダイレクトは今回の既定環境で成功。 |
| H16 | B | watch -Onceはerror/timeout/question/nohandoffを返しても末尾exit 0。常駐は結果を表示して次のチェックへ進む。pingpongも停止理由を書くだけで外部監視に非0を返す規約がない。パラメータは負数や0を受け付け、過大timeoutの乗算やIntervalSecで異常が起こる。 | 成功/質問待ち/異常の終了コードと永続状態を定義し、異常を呼出元に伝える。各数値にValidateRangeを付け、stateの型・必須フィールド・範囲も検証。 |
| H17 | B | 両方式は `-match "引き渡し不要"` という部分一致。実作業「引き渡し不要の検出を修正」が完了扱いとなることを再現。CLI exit 0＋相手Bのhash差分だけでは、成果物完成・有効な次依頼・旧依頼への応答対応も保証しない。 | done状態を構造化するか先頭行を厳密な完了文法で照合。reply_to・成果物manifest・成功状態を検証してから相手へ公開。 |
| H18 | B | 自分宛てB読取専用の規則はプロンプト依存。今回Bの項目3は例外として自分宛てBの復元を明示要求している。watch動作中なら復元を次依頼として拾う。相手向けBの保存直後も、自分のテストやGit終了前に相手watchが起動可能。 | 自分宛てBは原則読取専用、今回の復元は明示例外として履歴化。機械側でも入力hash不変を検査し例外は遷移IDで管理。相手Bの公開は全成果物・履歴保存後の最後に、共通ロックとready状態で行う。 |

採否提案: H01–H08、H13–H14を最優先で修正し、残りのB項目も無人運用開始前に反映する。今回のCodex側更新は文書規則のみで、上記不具合が修正されたという意味ではない。

## 起動条件6つの照合

| 条件 | 実際に防げる範囲 | 防げない経路 |
|---|---|---|
| B hash差分 | 正常なstateが残る単一プロセスの直前B再実行 | A→B→A、状態消失、未完了保存、保留節だけの変更 |
| StableSec | 現在mtimeが直近のファイル | 古いmtimeの部分文書、60秒超の書きかけ、検査と使用の間の変更 |
| 引き渡し不要 | 文字列が含まれる依頼を抑止 | 正当な修正依頼も誤抑止、構造的な完了確認なし |
| QUESTIONS時刻 | 自分の質問ファイルが依頼ファイルより新しい場合 | 履歴追記でmtimeが更新、同時刻、既存質問、回答済み判定なし |
| 当日上限 | 単独正常stateで当日上限に達した場合 | 日跨ぎ連鎖、2担当の合計、同時state更新、再起動型pingpong |
| lock | 既に同一Agentのlockが見えている場合 | 確認と作成の競合、反対Agent、pingpong、別PC、残存子プロセス |

具体的な二重起動順序: W1 Load-State → W2 Load-State → W1 Test-Path=false → W2 Test-Path=false → 両方Save-State → 両方WriteAllText → 両方Invoke-Turn。ロックを取った後のstate再読取と一意な原子取得が必要である。これはコード上の経路分析であり、実LLMを同時起動して再現してはいない。

「先頭行しか読まない」は正確には**ランチャーが要約行を作る**という意味。hashはB全文、実CLIへ渡す一文はMDを再読する指示なので、作業内容の通信路はB本文も含む。長い依頼は要約1行＋本文複数項目が適切。今回復元する保留依頼の折り返された要約は1物理行へ連結し、内容は変えない。

## PowerShell 5.1・Codex CLIの実測

- Windows PowerShell **5.1.26100.9168**で検証。原本2本はともにUTF-8 BOM `EF-BB-BF`あり。watchは構文エラー0、pingpongはH01の1件。BOM欠落が現在の原因ではない。将来も日本語PS1はBOMを維持する。
- `ConvertFrom-Json`はPSCustomObject、正常なrunsTodayはInt32として読み戻される。現在の正常stateではプロパティ代入・加算が動く。任意JSONの欠落・null・文字列・負数を許してよいわけではない（H08/H16）。
- Start-Process＋cmd.exe /cで、日本語・空白・!を含む隔離パスからUTF-8 BOMなしstdinをローカルechoヘルパーへ送り、stdout日本語とstderr分離、exit=0を確認。**実Codexモデル処理やoutput-last-message生成の端から端までの成功は未検証**。
- 実機は `codex-cli 0.153.4`。`codex exec --help`に--sandbox workspace-write、--skip-git-repo-check、-C、--output-last-message、stdin `-` がすべて存在。`codex -a never exec --sandbox workspace-write --skip-git-repo-check -C . --output-last-message build-codex/unused_last.md - --help`も受付。--helpのみでunused_last.mdは生成していない。
- CLIは `WARNING: proceeding, even though we could not create PATH aliases: Could not find home directory` を出した。今回の子シェル環境からログイン/ユーザー設定の正常読込までは保証できない。秘密や認証ファイルは閲覧していない。

提案する呼出し形（設定・認証・書込先を整備したPCで使用するための例。今回は実行しない）:

```powershell
codex -a never exec --sandbox workspace-write --skip-git-repo-check -C "$root" --output-last-message "$lastFile" -
```

stdinはUTF-8のプロンプトを接続する。`-a`はexecの前へ置く。neverは許可拡大ではなく、許可されない操作を失敗として返す設定である。--sandboxだけから「承認待ちが絶対に発生しない」とは判定しない。非対話execでは承認不能がエラーになる経路もあり、すべてが画面待ちになると断定しない。CLIの認証/初期化、hookや外部ツールの対話・待機、権限拒否で未完了になる経路を別々に処理する必要がある。

根拠: [OpenAI公式 非対話モード](https://learn.chatgpt.com/docs/non-interactive-mode)、[OpenAI公式 CLIリファレンス](https://learn.chatgpt.com/docs/developer-commands?surface=cli)、実機CLIヘルプ。公式サイトの列挙値にはローカルCLIと差もあるため、今回の呼出し判定は実機0.153.4で受け付けた範囲を採用。

## DryRun出力と反証結果

再現用: `build-codex/review_handoff.ps1`。Windows PowerShellでこのファイルを実行すると、一意なOS一時フォルダに原本をコピーする。外部起動・Git・通知を行わない。下記は今回の結果の抜粋、全出力は [PINGPONG_EVIDENCE.txt](PINGPONG_EVIDENCE.txt)。

```text
PowerShell=5.1.26100.9168
PARSE watch_handoff.ps1 BOM=EF-BB-BF errors=0
PARSE pingpong.ps1 BOM=EF-BB-BF errors=1
line=193 id=InvalidVariableReferenceWithDrive text=$agent:
COMMAND watch_handoff.ps1 -Agent codex -Once -DryRun -NoGit
[watch/codex 06:24:48] 結果: dryrun
exit=0
State after DryRun: lastHash=433DFCBC5129, date=2026-09-09, runsToday=1
COMMAND watch_handoff.ps1 -Agent codex -Once -DryRun -NoGit (same B)
exit=0 (見張り開始だけで依頼/コマンドは再表示されない)
COMMAND watch_handoff.ps1 -Agent codex -DryRun -NoGit
continuous_returned_without_stop=False
COMMAND pingpong.ps1 -DryRun -NoGit -MaxRounds 1
InvalidVariableReferenceWithDrive
exit=1
OBSERVATION CHECKS=14 (these reproduce defects, not acceptance passes)
REDIRECTION exit=0 stdout=日本語 ! prompt stderr=stderr-ok
```

watchの-DryRun単独は2秒で試験用プロセスを停止した（CLI子プロセスを起動しないDryRunに限定）。-Once -DryRunは自然終了した。原本コードの関数定義だけを読込み、Invoke-Turnをローカルstubへ置き換えた14件の観測チェックはすべて期待した現象を確認した。**製品の受入テスト14件が通過したという意味ではない**。並行プロセスの実負荷試験、実CLI往復、認証、ネットワーク、強制終了時の実CLI子孫挙動は未実施。

ops v0.3.6の388件はこのレビューの対象外で再実行していない。386通過/2失敗はClaudeの既存報告であり、今回の実測値として転記しない。

## Codex側の運用規則・AGENTS.md追記案

`build-codex/Claude引き渡しプロンプト.md` の「今後の更新ルール」にPINGPONG.mdの5点を追記した。Claude向けのB本文は今回変更しない。第10回opsレビュー後に更新するという明示指示を優先した。

AGENTS.mdへの追記案（本文はClaudeが反映）:

```text
自動引き渡し規則:
1. 自分宛ての引き渡しMDのBは原則読取専用。明示された復元例外は履歴に残す。
2. 相手向けBには「今回の依頼: …」を1物理行・1件だけ置き、複数工程は本文へ書く。
3. 作業・必要検証・成果物・履歴を完了するまで相手Bを更新しない。公開を最後の操作にする。
4. 人間の判断が必要なら自分のQUESTIONS.mdへ記録し、Bを変更せず停止する。
   未解決状態はファイル時刻から解除しない。
5. 全体完了なら相手Bを「今回の依頼: 引き渡し不要（理由）」にする。
6. 引き渡しMDの過去の依頼にも開始・終了JST、結果、次の担当を残す。時刻未取得は推測しない。
7. 共通ロック・不変依頼ID・完了状態に対応したランチャーだけを使用する。
   Cowork担当日にPC側Claudeを同時起動しない。監視方式とピンポン方式を重ねない。
8. runtime state/log/lockはDropbox外。LINE通知・実口座・実審査LLM・秘密commit等の禁止は継承する。
9. DryRunは読取・表示専用。エラー/timeout/質問時は永続停止し、明示復旧まで自動再試行しない。
```

受領時のB項目3はCodex向けB復元を要求していたが、復元前に別担当が第10回へ戻し、自動連携を別枠へ分離した。新しい「開発ループのBを変更しない」指定に従い、本レビューによる復元は行っていない。H01–H18は開発ループの次回Bへ混ぜず、[自動連携専用のClaude依頼](AUTOMATION_CLAUDE_HANDOFF.md)で修正状況を照合する。監視の起動や依頼送信は行っていない。

## 原本識別

```text
tools/watch_handoff.ps1 SHA256 A264A9FE73129EC069CC443026E7077495A922286EA9A9B51DDDDE3D11E5270C
tools/pingpong.ps1 SHA256 95B5D43F02D5A8DB45D622C2FE3A936A222E46678A08D18E2D351DFA66679E0B
tools/PINGPONG.md SHA256 B673D58C11523A8C7B298FD57D9F7EE1465539F3BB92FD4F2BC89728729A7DC8
AGENTS.md SHA256 F4B2A0946CE6F28E32A25872B76471618CCFBB781D0781C64D6DF10F50C6BF7F
```

## 更新記録・別枠の完了報告

| 開始 | 終了 | 作業 | 結果 |
|---|---|---|---|
| 開始時刻未取得（06:22時計確認） | 2026-09-09 06:40 | 受領版2方式の独立レビュー | H01〜H18、PS5.1検証、運用5規則を保存。途中の変更版は未検証、開発Bは変更せず別枠へ |

終了時刻: 2026-09-09 06:40 JST
次に渡す一文（コピー用）:
```text
ai-trader の作業フォルダで、build-codex/AUTOMATION_CLAUDE_HANDOFF.md を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、自動連携レビューH01〜H18の変更版への反映状況整理と未解決指摘への対応を行ってください。
```