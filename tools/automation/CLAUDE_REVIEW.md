# 自動連携（tools/automation）Claude 独立レビュー 第1回

作成: 2026-09-09 07:35 JST（Claude / Cowork）。対象: Codex 版 `tools/automation/codex_engine.py`（364 行）、各 ps1、`tools/tests/test_handoff_engine.py`（22 件）、`IMPLEMENTATION.md`、`README.md`。
検証環境: 則光さんの PC 上の Cowork VM（Linux、Python 3.10、標準ライブラリのみ）。PowerShell 5.1 の実行はできないため ps1 は読取レビュー。実 CLI・実 LLM は起動していない。

## 採否（Claude 側の決定）

**tools/automation（Codex 版）を正規の実装として採用し、Claude 版 `tools/watch_handoff.ps1` v2・`pingpong.ps1`・起動用 cmd・タスク登録・初期の `tools/handoff_engine.py` 群は `tools/_deprecated/` へ移して廃止する。** 理由: Codex 版は H01〜H18 の A 判定をすべて設計で潰しており（下表）、Windows 実機で unittest 22 件・PS5.1 構文・DryRun を通している。Claude 版は同じ問題に PowerShell 単体で対処したが、実機検証ができておらず、二重管理を続ける利益がない。

ただし Codex 版は**自動連携チャネル（tools/automation/ の専用 MD）しか見張らない**設計で、本来の目的である投資本体の開発ループ（`Codex引き渡しプロンプト.md` ⇄ `build-codex/Claude引き渡しプロンプト.md`）は自動化されない。そこで Claude が **dev チャネル**を追加した（下記）。

## H01〜H18 の反映状況（受領版 → 現行 tools/automation）

| ID | 重大度 | 現行版での状態 | 根拠 |
|---|---|---|---|
| H01 | A | 非該当 | 旧 pingpong.ps1 は廃止。現行 ps1 は引数配列を Python に渡すだけ |
| H02 | A | 対応済み | `Engine.once(dry=True)` は state/lock/log を書かない。`test_dryrun_has_no_state_logs_claim_or_git` |
| H03 | A | 対応済み | `lock()` が `O_CREAT|O_EXCL` でプロジェクト共通 `project.lock` を取得。取得後に `read_request` で入力不変を再検査。`test_lock` 系 |
| H04 | A | 対応済み | Windows は `taskkill /T /F`、POSIX は `killpg`。timeout/error は `blocked` に永続化し `resume` まで再起動しない |
| H05 | A | 対応済み | ファイル全体の署名を 2 回観測＋`stable` 秒＋mtime。公開マーカー（B の SHA256）が一致しないと `unpublished` |
| H06 | A | 対応済み | `QUESTIONS_*.md` は `<!-- handoff-questions: resolved -->` を人間が書くまで未解決。時刻比較なし |
| H07 | A | 対応済み | `read_request` は `## B.` を 1 個要求、コードフェンス内見出しを無視、`### 保留中` 以降は B 外、「今回の依頼:」1 行のみ |
| H08 | A | 対応済み | `processed[key]='running'` → 結果で上書き。失敗は `blocked` |
| H09 | B | 対応済み | `processed` は hash 全履歴の dict。再起動で消えない |
| H10 | B | 一部対応 | 日次上限は両エージェント合計（チャネル共通）。累計上限・chain_id はない。日跨ぎ連鎖は日次上限×日数で進む → 運用で `--max-runs` を小さく（5）にする |
| H11 | B | 対応済み | `codex -a never exec --sandbox workspace-write … -`。claude は acceptEdits＋限定 allowedTools（Bash(git:*) は含めない） |
| H12 | B | 対応済み | `runtime_dir` が Dropbox 配下・checkout 配下を拒否 |
| H13 | A | 対応済み | 通知フックなし |
| H14 | A | 対応済み | Git 操作なし（`-NoGit` は互換のため受理のみ） |
| H15 | B | 対応済み | `subprocess.Popen(args, shell=False)`、stdin UTF-8。`.cmd/.bat/.ps1` を拒否しネイティブ実行ファイルのみ |
| H16 | B | 対応済み | `--once` は失敗系で exit 1。数値引数は正の検証。state の型検証あり |
| H17 | B | 対応済み | `summary.startswith('引き渡し不要')` |
| H18 | B | 対応済み | 実行後に自分宛て MD の不変を検査（`self_modified`）、相手宛ては公開マーカー必須（`nohandoff`） |

## Claude が見つけた点（現行版）

| ID | 重大度 | 事象 | 対応 |
|---|---|---|---|
| C01 | A（目的） | 開発ループ（投資本体）が自動化対象外。README に「rootのCodex引き渡しプロンプト.md…は監視しません」 | **dev チャネルを追加**（下記）。既定の watch は `auto,dev` の両方 |
| C02 | B | `watch` は単一チャネル前提で、`done`/`daily_limit` で終了する。常駐で両チャネルを見張ると一方が「引き渡し不要」なだけで終了してしまう | 複数チャネル時は `done`/`daily_limit` を待機扱いにした（単一チャネルは従来どおり） |
| C03 | B | `prompt_for` が auto 専用文面（「編集対象は tools/automation/ と tools/tests/ だけ」）。dev で使うと開発作業を禁止してしまう | dev 用の文面を追加（A/B 準拠、`publish --channel dev`、開発用 QUESTIONS） |
| C04 | C | `read_request` の見出し終端 `^#{1,3} ` は `####` 以深を終端にしない | **対応済み（09:15）**: `^#{1,6} `。試験 `test_c04_deeper_headings_end_section_b` |
| C05 | C | `lock()` は排他作成のみでハンドル保持ではない。クラッシュ時の残存ロックは手動削除（README 記載どおり） | 未変更。運用で対応 |
| C06 | 情報 | Claude CLI はこの PC に未導入（README・IMPLEMENTATION の記載どおり）。よって当面 **Claude 側は Cowork が担当**し、PC では `-Agent codex` だけを常駐 | 運用方針として確定 |

## dev チャネルの追加（Claude 変更、2026-09-09 07:30 JST）

- `codex_engine.py`: `CHANNELS = {auto, dev}`。dev は `Codex引き渡しプロンプト.md` / `build-codex/Claude引き渡しプロンプト.md`、質問は `build-codex/QUESTIONS.md` / `ops/QUESTIONS.md`。`Engine(channel=…)`、state キーは `dev:` 接頭辞（auto は従来キーのまま＝既存 22 件は無変更で通過）、`project.lock`・`blocked`・日次回数はチャネル共通。`--channel auto,dev` で 1 プロセスが両チャネルを順に確認（1 tick で起動は 1 件）。
- `watch_handoff.ps1`: `-Channel`（既定 `auto,dev`）。`publish_handoff.ps1`: `-Channel`（既定 `auto`）。
- 追加試験 `tools/tests/test_handoff_channels.py`（7 件）: dev のファイル/質問の分離、dev プロンプト（A/B 準拠・publish --channel dev・開発用 QUESTIONS）、`### 保留中` を B に含めない、state キーのチャネル分離とロック共有、dev の質問が auto を止めないこと、失敗ブロックはチャネル横断、未知チャネルの拒否。
- 実測: `python -m unittest discover -s tools/tests` **29 件通過（skip 1＝Windows 専用）**、Linux VM。dev チャネルの DryRun（実ファイル）で `summary`＝第11回の依頼、`published=false` を確認 → その後 `publish --channel dev --agent codex` で公開。

## 運用の確定（2026-09-09）

1. PC で `powershell -NoProfile -ExecutionPolicy Bypass -File tools\automation\watch_handoff.ps1 -Agent codex`（既定で auto,dev の両方を見張る）。まず `-DryRun` で確認。
2. Claude 側は Cowork が担当。Cowork が相手宛て B を書いたら `python tools/automation/codex_engine.py publish --channel dev --agent codex`（Cowork VM から実行可）。Codex は完了時に `publish --channel dev --agent claude` で返す。
3. 質問は各 QUESTIONS.md。人間が答えたら `<!-- handoff-questions: resolved -->` を書く。失敗ブロックは `handoff_status.ps1 -Resume`。
4. 旧 Claude 版（tools/_deprecated/）は起動しない。

## 追加変更（2026-09-09 09:15 JST、Claude）

- C04 対応: B の終端見出しを `#`〜`######` に拡張。
- C07（新規、B）: Cowork（Claude 側）は PC の runtime（Dropbox 外）を読めず、見張りが生きているか・ブロック中かを判断できなかった。`watch` が 1 tick ごとに `tools/automation/watch_status.json`（担当・各チャネルの直近判定・blocked・runtime・PID・書込時刻）を書くようにした。ログ・状態本体は runtime のまま（H12 の趣旨を保つ）。`--no-status-file` で抑止、DryRun は書かない。
- C08（新規、B）: 複数チャネルの常駐 watch で、片方の QUESTIONS 待ちが `return 1` で全体を止めていた。質問はチャネル別なので、複数チャネル時は待機扱いにして他方の見張りを続ける（単一チャネルは従来どおり終了）。
- 実測: `python -m unittest discover -s tools/tests` **32 件通過（skip 1）**、Linux VM。
- 運用上の注意: 実行中の見張りは起動時のコードを使い続けるため、この変更を反映するには見張りの再起動が必要。

## 2026-09-09 Codex再検証・修正（最新版）

専用Bのdev/状態表示/質問待ちを確認し、登録入口も含めD01〜D09を修正。52件全通過5.978秒、skipなし。PS5.1構文6件と実フォルダDryRun4入口も通過。実タスク登録・実AI起動は未実施。詳細と採否はCODEX_REVIEW.md、導入手順の最新版はREADME.md。

C04はClaudeの1〜6段終端を採用し、コード枠外だけのB/公開判定へ補強。C05は現方式維持（fdはyield中も保持している）。クラッシュ時の残存ロックは手動確認後の除去で、PIDだけを根拠に自動削除しない。

常駐登録はDryRun/RegisterOnly/明示Replace/所有・稼働確認/終了コード保持を追加。自動再試行3回は廃止。共有状態は処理中も周期更新、要約・runtimeパスを共有しない。過去の「root開発Bを監視しない」「共有ファイルは一切なし」という初期専用チャネル説明は、現行auto/devと観測専用状態表示の範囲に置き換える。状態・ログ本体はDropbox外を維持する。

## 最終独立確認（2026-09-09 09:32 JST、Claude）— Codex 修正 D01〜D09 と常駐登録・状態表示

対象: `CODEX_REVIEW.md`（D01〜D09、C04/C05 の採否）、`codex_engine.py`（SHA256 09A9C5C6…）、`register_watch_task.ps1`（ED53D577…）、`watch_handoff.ps1`／`pingpong.ps1`／`handoff_status.ps1`／`handoff_common.ps1`、`tools/tests/test_handoff_review.py`・`test_handoff_windows.py`、`PS_EVIDENCE.txt`、`TEST_EVIDENCE.txt`。

| 項目 | 確認結果 |
|---|---|
| D01 枠外 B・公開マーカー | `markdown_lines` の構造行走査で、コード枠内の `## B` とマーカーを無視。見出し終端 `#`〜`######` は C04 を継承。異なる長さのフェンス・チルダも区別 |
| D02 state 型検証 | `load_state` が runs（非負 int）・date（YYYY-MM-DD）・blocked（str/None）・processed（str→str）・observations（signature str、since 有限数）を検証し、不正は ProtocolError で停止（自動リセットなし） |
| D03 Resume | ロック内で **全チャネル**の QUESTIONS を確認してから blocked 解除。`--dry-run` はロック・状態を作らず preview のみ |
| D04 Once の集約 | tick 内の全結果 `tick_statuses` で exit を判定（先行チャネルの question が後続の待機に隠れない）。実行ログを持つ結果（`log`）で tick を終了し 1 tick 1 実行を保つ |
| D05 DryRun の CLI 検証 | `cli_found` は実ファイル・ネイティブ CLI の条件で判定し `cli_error`/`processed` を表示。dev プロンプトに質問の自己解決禁止を明記 |
| D06 状態表示 | `status_heartbeat` が CLI 実行中も `interval` ごとに更新（別スレッド、例外は伝播）。共有内容は担当・チャネル別 status/at・blocked 有無・phase のみ（依頼要約・runtime パスを除去）。Claude 側は `watch_status_claude.json` に分離 |
| D07 PS 入口 | `pingpong.ps1 -Channel`、`watch_handoff.ps1 -NoStatusFile`、`handoff_status.ps1 -Resume -DryRun` を確認 |
| D08 登録スクリプト | 事前に engine の DryRun で CLI 実体・runtime を解決し、blocked/question があれば登録しない。引数は単一引用符エスケープ→UTF-16 EncodedCommand。既存タスクは Description で所有確認、Running は拒否、更新は `-Replace` 明示。`-RegisterOnly`・`-Unregister -DryRun` |
| D09 表示と設定 | 子の終了コード伝播、表示は「start requested」に限定。Interactive/Limited、IgnoreNew、無期限、バッテリー継続。安全停止後の自動再試行なし。電源設定は変更しない |
| C04 | 採用（枠外判定の補強つき） |
| C05 | 現方式維持（`os.open` の fd は yield 中も保持＝排他作成による二重実行抑止。残存ロックの自動回収はしない）。私の当初の「ハンドル非保持」は訂正 |
| 実測（Linux VM） | `python -m unittest discover -s tools/tests -v` **52 件中 45 通過・7 skip（Windows 専用: PS5.1 統合 6＋taskkill 1）**、0.77 秒。Windows 実測は Codex の 52 件全通過（TEST_EVIDENCE.txt）と分けて記載 |
| 実フォルダ DryRun（Windows、PS_EVIDENCE.txt） | auto/dev とも `published: true`、`cli_found: true`（`…\OpenAI\Codex\bin\…\codex.exe`）、runtime は `C:\Users\s\.ai-trader\automation-handoff\…`。register の DryRun は Execute=powershell.exe＋EncodedCommand の計画を表示 |

新しい欠陥なし。反証の追加は不要と判断。自動連携の開発確認を完了とし、auto チャネルの Codex 宛て B を「引き渡し不要（自動連携の双方確認完了。常駐登録はユーザー実行待ち）」として公開する。

### 残ること（コード試験では確認できない）

- 実タスク登録（権限）、実 Codex 認証・モデル実行、常駐後の見張り状態。ユーザーが README の手順（登録 DryRun → 登録/起動 → 状態確認）で実施する。
- `tools/automation/register_watch_task.cmd`（ダブルクリック用）は初回登録専用。更新時は `register_watch_task.ps1 -Replace` を使う。

## 質問ブロック単位の解決判定（QUESTION_REVIEW.md）の独立確認（2026-09-09 10:30 JST、Claude / Cowork）

対象: `codex_engine.py` の `unresolved_questions` / `unanswered`（Codex 修正版）、`tools/tests/test_question_sections.py`（9 件）。検証は Cowork VM（Linux、標準ライブラリ）。実 CLI・実モデルは起動していない。

| 項目 | 確認結果 |
|---|---|
| 受領版の実測 | `python -m unittest discover -s tools/tests -v` **66 件中 58 通過・8 skip（Windows 専用）**、0.52 秒。Codex 追加 9 件はすべて通過 |
| 採用契約 | `##` ブロック単位・依頼 hash の診断表示・異なる hash の未解決も停止・枠内マーカー無効・旧形式は 1 ブロック・実行後は question 最優先。QUESTION_REVIEW.md の記述と実装が一致 |
| Claude 案との差 | 「現在 hash 一致だけ停止」を採らず全未解決で停止する判断に同意（依頼を変えるだけで回答を省略できない） |

### 反例（`tools/tests/test_question_sections_claude.py`、7 件。3 件が現行実装で失敗）

| ID | 重大度 | 事象 | 判定 |
|---|---|---|---|
| **E01** | **A** | 解決マーカーの**下に見出しなしで追記された質問**が、同じ `##` ブロック内にあるため解決済みとみなされ飲み込まれる（`resolved = ブロック内にマーカーが 1 つでもあれば True`）。10:01 の事故と同じ形（マーカー付きブロックへの追記）が `##` を付け忘れると再発する | **失敗** |
| **E02** | B | 最初の `##` より前の本文は「文書タイトル」として全部無視される。タイトル 1 行以外に見出しなしの質問があっても質問なし扱い | **失敗** |
| E03 | — | タイトル行だけの前置きは質問ではない（現行どおり） | 通過 |
| E04 | — | 字下げ・空白なし・`~~~` 枠内・行内のマーカーは解決にならない（安全側） | 通過 |
| E05 | C | 複数行 HTML コメント `<!-- メモ … -->` の内側のマーカーが解決扱い | **失敗** |
| E06 | — | 同名見出しの 2 ブロックは別扱い | 通過 |
| E07 | — | 1 ブロックに異なる hash が 2 つなら hash=None で診断 | 通過 |

追加後の実測: **73 件中 62 通過・3 失敗・8 skip**。

### 修正案（Codex 判断）

- E01/E02 を同時に満たす規則: 「マーカーは、そのブロック内で**マーカーより上**の本文だけを解決する。最後のマーカーより下に本文が残っていれば未解決」＋「最初の `##` より前は、`#` 見出し行と空行を除いて本文が残っていれば旧形式ブロックとして扱う」。ブロックの終端規則・枠内無効・hash 診断はそのまま。
- E05 は `markdown_lines` で `<!--` … `-->` の複数行コメントを非構造行にするか、マーカー行が単独のコメントであること（行頭 `<!--` と行末 `-->` が同じ行）を要求する。

### 運用上の確認（コードとは別）

- 10:01 の書込拒否は `common/tests/phase2` の ACL 継承欠落（QUESTION_REVIEW.md の診断）。`build-codex/QUESTIONS.md` の第 2 ブロックは「修復・検証済み」で解決マーカー付き。修復は則光さんの承認のもとで実施されたと記録されているが、本セッションでは PC 側の ACL を直接確認できないため、dev チャネルの再開は Codex サンドボックスでの書込再検証が済んでいることを前提にする。

## Windows 実機での独立確認（2026-09-09 17:01 JST、Claude / PC 上の Claude Code）

対象: `codex_engine.py`（SHA256 927F5702…）の `unresolved_questions`／`unanswered`、`tools/tests/test_question_sections.py`（9 件）、および前回 Cowork（Linux）で確認できなかった Windows 専用試験。
環境: Windows 11、PS5.1、Codex 同梱 Python 3.12.14（`C:\Users\s\.cache\codex-runtimes\…\python.exe`）、標準ライブラリのみ。実 CLI・実モデル・実タスク登録・ACL 操作はしていない。dev（投資本体）には触れていない。

### 実測（`unittest discover -s tools/tests`、skip 0）

| 対象 | コンソール符号化 | 結果 |
|---|---|---|
| 受領版 66 件（Codex 追加 9 件を含む） | UTF-8（chcp 65001） | **66 件全通過**、12.802 秒 → Codex 報告（6.465 秒）を件数・内容で再現。所要時間差は実行環境の負荷差 |
| ＋ Claude 反例 7 件（`test_question_sections_claude.py`） | UTF-8 | 73 件中 **3 失敗**（E01・E02・E05）、11.786 秒 |
| ＋ 今回追加 2 件（`test_ps_encoding_claude.py`） | UTF-8 | 75 件中 **4 失敗**（E01・E02・E05・E08）、13.228 秒 |
| 同上 | **既定の OEM（chcp 932＝この PC の既定）** | 75 件中 **4 失敗＋2 エラー**、13.003 秒 |

E01・E02・E05 は Linux だけの現象ではなく Windows でも同じ形で再現した（未修正）。「9 件追加・66 件全通過」という受領版の主張自体は正しい。ただし**再現には chcp 65001 が要る**（Codex CLI の環境がそうなっている）。既定の日本語コンソールでは `test_handoff_windows.py` の 2 件が `UnicodeDecodeError`（0x93）になる。原因は下の E08 と同じ。

### 新しい欠陥

| ID | 重大度 | 事象 | 判定 |
|---|---|---|---|
| **E08** | **B** | `register_watch_task.ps1:33-35` が engine の UTF-8 JSON を `@(& $PythonExe …)` で**捕捉**するため、`[Console]::OutputEncoding`（日本語 Windows の既定＝CP932）で復号されて依頼要約が文字化けする。記号の並び（例:「…（A）・E02…」）では CP932 の 2 バイト先頭が続く `"` を飲み込み、`ConvertFrom-Json` が `Invalid object passed in, ':' or '}' expected. (226)` で落ちる。**この実フォルダの現在の依頼で再現済み**（`-File register_watch_task.ps1 -DryRun` が rc=1、タスク登録も DryRun 前確認も実行不能）。README の登録・保守手順が既定環境で動かない。危険側ではなく「登録できない」側に倒れる | **失敗**（`test_e08_register_dryrun_survives_oem_console_codepage`） |
| E09 | — | `watch_handoff.ps1`／`publish_handoff.ps1` は engine の出力を捕捉せず素通しするため、CP932 コンソールでも UTF-8 のまま壊れない（現行どおり） | 通過（修正で捕捉方式に変えないための番人） |

反例は `tools/tests/test_ps_encoding_claude.py`（2 件、Windows 専用）。子 PowerShell 側で `[Console]::OutputEncoding` を CP932 に固定して隔離コピー上で再現するので、実行 PC の chcp に依らず同じ結果になる。

再現手順（この作業フォルダ、DryRun のみ・副作用なし）:

```
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\automation\register_watch_task.ps1 -DryRun
```

修正案（採否は Codex 判断）: 捕捉の前に `[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)` を置く（`handoff_common.ps1` に集約すると `register_watch_task.ps1` の自前の python 呼び出しにも効く）。または engine の出力を一時ファイルへ書かせて `Get-Content -Encoding UTF8 -Raw` で読む。E09 の素通し経路は変えない。`test_handoff_windows.py` の 2 件は ps1 の stdout を UTF-8 と仮定しているので、同じ修正で既定コンソールでも通るようになる（試験側で復号を OEM に緩めるのは、実運用の文字化けを隠すので採らない）。

### 判定

新しい欠陥（E08）を検出したため「引き渡し不要」にはしない。auto チャネルの Codex 宛て B を E01・E02・E05 に E08 を加えた内容へ更新して公開する。権限（ACL）の再修復・質問の自己解決・ブロック解除は行っていない。
