# tools/watch_handoff.ps1 v2 — 引き渡しファイルを定期的に見張り、依頼が更新されていたら作業を引き受ける（監視方式）
# v2 (2026-09-09 07:40 JST): Codex レビュー build-codex/PINGPONG_REVIEW.md H02〜H17 を反映
#   - 実行時ファイル（state / lock / log / halt）は Dropbox 外 ${AI_TRADER_HOME}\handoff\（既定 ~\.ai-trader\handoff\）  [H12]
#   - プロジェクト共通ロックを FileMode.CreateNew で原子的に取得し、作業中は保持。所有者以外は解放しない            [H03]
#   - DryRun は読取・表示のみ（state / lock / log を書かない。1 回で終了）                                          [H02]
#   - 依頼セクションは「## B. 今回の依頼」または「## 今回の依頼」から次の見出し（# 任意の深さ）/--- まで。
#     コードフェンス内の見出しは無視。「今回の依頼:」行は 1 物理行・1 件・非空を要求し、違反は起動しない            [H07]
#   - 同じ内容を StableSec 以上の間隔で 2 回観測してから起動（mtime だけに頼らない）                                 [H05]
#   - 処理済みは hash の履歴（直近 200 件）で管理。失敗は「処理済み」にせず HALT にして人間の復旧を待つ              [H08][H09]
#   - QUESTIONS.md が存在し「回答済」を含まない間は起動しない（時刻比較をやめる）                                    [H06]
#   - 当日上限に加えてチャネル累計上限（MaxRunsTotal）。到達後は人間が -ResetCounters するまで再開しない            [H10]
#   - codex は `codex -a never exec …`、モデル名は英数字 . _ - のみ許可                                              [H11][H15]
#   - タイムアウトは taskkill /T でプロセスツリーごと停止し HALT                                                     [H04]
#   - 完了判定は先頭行が「引き渡し不要」で始まる場合のみ                                                             [H17]
#   - 通知フック・git 自動 commit は廃止（LINE 禁止・全担当の変更を巻き込む問題）。スナップショットは人間が取る       [H13][H14]
#   - -Once の終了コード: 0=起動なし/成功, 2=質問待ち, 3=HALT/異常, 4=引き渡しなし。数値引数は範囲検証             [H16]
#
# 使い方（ai-trader フォルダで。tools\start_watch_codex.cmd をダブルクリックでも可）:
#   powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent codex              # 両チャネルを常駐で見張る
#   powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent codex -DryRun      # 起動判定と一文だけ表示（副作用なし）
#   powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent codex -Once        # 1 回確認して終了
#   powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent codex -Status      # 状態（HALT / 上限 / 処理済み）を表示
#   powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent codex -ResetCounters   # 累計上限をリセット（HALT は人間が halt ファイルを削除）
#
# チャネル:
#   dev  : 開発ループ   codex 宛て = Codex引き渡しプロンプト.md（## B. 今回の依頼）  claude 宛て = build-codex/Claude引き渡しプロンプト.md
#   auto : 自動連携     codex 宛て = tools/自動連携_Codex依頼.md（## 今回の依頼）    claude 宛て = tools/自動連携_Claude依頼.md

param(
  [Parameter(Mandatory = $true)][ValidateSet("codex", "claude")] [string]$Agent,
  [ValidateSet("dev", "auto")] [string[]]$Channel = @("dev", "auto"),
  [ValidateRange(10, 3600)]  [int]$IntervalSec = 60,
  [ValidateRange(10, 3600)]  [int]$StableSec = 60,
  [ValidateRange(1, 100)]    [int]$MaxRunsPerDay = 10,
  [ValidateRange(1, 1000)]   [int]$MaxRunsTotal = 20,
  [ValidateRange(1, 720)]    [int]$TurnTimeoutMin = 45,
  [string]$CodexModel = "",
  [string]$ClaudeModel = "",
  [switch]$ClaudeSkipPermissions,
  [switch]$Once,
  [switch]$DryRun,
  [switch]$Status,
  [switch]$ResetCounters
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
foreach ($m in @($CodexModel, $ClaudeModel)) { if ($m -and $m -notmatch '^[A-Za-z0-9._-]{1,64}$') { throw "モデル名に使えない文字が含まれています: $m" } }
if ($DryRun) { $Once = $true }

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)   # ai-trader/
Set-Location $root
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# 実行時ファイルは Dropbox 外
$home_ = if ($env:AI_TRADER_HOME) { $env:AI_TRADER_HOME } else { Join-Path $env:USERPROFILE ".ai-trader" }
$rtDir  = Join-Path $home_ "handoff"
$logDir = Join-Path $rtDir "logs"
$lockFile = Join-Path $rtDir "handoff.lock"          # プロジェクト共通（Agent をまたいで 1 つ）
$haltFile = Join-Path $rtDir "HALT_$Agent.txt"       # これがある間は起動しない。人間が確認後に削除
if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

function Now-JST { (Get-Date).ToUniversalTime().AddHours(9).ToString("yyyy-MM-dd HH:mm") }
function Log([string]$msg) { Write-Host "[watch/$Agent $(Get-Date -Format 'HH:mm:ss')] $msg" }

function Get-ChannelSpec([string]$ch) {
  if ($ch -eq "dev") { $codexIn = "Codex引き渡しプロンプト.md";   $claudeIn = "build-codex/Claude引き渡しプロンプト.md" }
  else               { $codexIn = "tools/自動連携_Codex依頼.md"; $claudeIn = "tools/自動連携_Claude依頼.md" }
  if ($Agent -eq "codex") { $in = $codexIn;  $out = $claudeIn; $q = "build-codex/QUESTIONS.md" }
  else                    { $in = $claudeIn; $out = $codexIn;  $q = "ops/QUESTIONS.md" }
  [pscustomobject]@{
    name = $ch; inFile = $in; outFile = $out; qFile = $q
    inPath = (Join-Path $root ($in -replace "/", "\")); outPath = (Join-Path $root ($out -replace "/", "\")); qPath = (Join-Path $root ($q -replace "/", "\"))
    stateFile = (Join-Path $rtDir "state_${ch}_$Agent.json")
  }
}

# ---- 依頼セクションの読み取り（H07） ------------------------------------------
function Get-Request([string]$path) {
  # 戻り値: @{ ok; section; summary; hash; reason }
  $r = @{ ok = $false; section = ""; summary = ""; hash = ""; reason = "" }
  if (-not (Test-Path -LiteralPath $path)) { $r.reason = "ファイルなし"; return $r }
  $text = (Get-Content -Raw -Encoding UTF8 -LiteralPath $path) -replace "`r`n", "`n"
  $lines = $text -split "`n"
  $start = -1; $end = $lines.Count; $fenced = $false; $heads = 0
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $l = $lines[$i]
    if ($l.TrimStart() -like '```*') { $fenced = -not $fenced; continue }
    if ($fenced) { continue }
    if ($l -match '^## (B[.．] )?今回の依頼') { $heads++; if ($start -lt 0) { $start = $i } ; continue }
    if ($start -ge 0 -and ($l -match '^#{1,6} ' -or $l -match '^---\s*$')) { $end = $i; break }
  }
  if ($heads -ne 1) { $r.reason = "依頼見出し（## B. 今回の依頼 / ## 今回の依頼）が $heads 個"; return $r }
  $section = ($lines[$start..($end - 1)] -join "`n")
  $r.section = $section
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $r.hash = [BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($section))).Replace("-", "").Substring(0, 16)
  $reqLines = @($section -split "`n" | Where-Object { $_ -match '^[ \t]*今回の依頼[:：]' })
  if ($reqLines.Count -ne 1) { $r.reason = "「今回の依頼:」行が $($reqLines.Count) 件（1 件のみ許可）"; return $r }
  $summary = ($reqLines[0] -replace '^[ \t]*今回の依頼[:：][ \t]*', '').Trim()
  if (-not $summary) { $r.reason = "「今回の依頼:」の内容が空"; return $r }
  $r.summary = $summary; $r.ok = $true
  return $r
}
function Get-Mtime([string]$path) { if (Test-Path -LiteralPath $path) { (Get-Item -LiteralPath $path).LastWriteTimeUtc } else { [datetime]::MinValue } }

# ---- 状態（H08/H09、原子的保存） -------------------------------------------------
function New-State { [pscustomobject]@{ processed = @(); date = ""; runsToday = 0; runsTotal = 0; candidateHash = ""; candidateSeenUtc = ""; lastRunJST = ""; lastResult = "" } }
function Load-State([string]$file) {
  if (-not (Test-Path -LiteralPath $file)) { return New-State }
  try {
    $s = Get-Content -Raw -Encoding UTF8 -LiteralPath $file | ConvertFrom-Json
    $n = New-State
    foreach ($k in @("date", "candidateHash", "candidateSeenUtc", "lastRunJST", "lastResult")) { if ($s.$k) { $n.$k = [string]$s.$k } }
    foreach ($k in @("runsToday", "runsTotal")) { if ($s.$k -ne $null) { $n.$k = [int]$s.$k } }
    if ($s.processed) { $n.processed = @($s.processed | ForEach-Object { [string]$_ }) }
    return $n
  } catch { throw "state が壊れています（$file）。内容を確認して削除してください: $($_.Exception.Message)" }
}
function Save-State([string]$file, $s) {
  $tmp = "$file.tmp"
  [IO.File]::WriteAllText($tmp, ($s | ConvertTo-Json -Depth 4), $utf8NoBom)
  Move-Item -LiteralPath $tmp -Destination $file -Force
}

# ---- 共通ロック（H03/H04） -------------------------------------------------------
$script:lockStream = $null
function Acquire-Lock([string]$note) {
  try {
    $fs = New-Object IO.FileStream($lockFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes("agent=$Agent pid=$PID start=$(Now-JST) $note")
    $fs.Write($bytes, 0, $bytes.Length); $fs.Flush()
    $script:lockStream = $fs
    return $true
  } catch [System.IO.IOException] {
    return $false
  }
}
function Release-Lock {
  if ($script:lockStream) { $script:lockStream.Dispose(); $script:lockStream = $null; Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue }
}
function Halt([string]$reason) {
  [IO.File]::WriteAllText($haltFile, "$(Now-JST) JST`n$reason`n確認後、このファイルを削除すると再開します。", $utf8NoBom)
  Log "HALT: $reason → $haltFile"
}

$history = Join-Path $root "tools\pingpong_history.md"
function Append-History([string]$line) {
  if (-not (Test-Path -LiteralPath $history)) {
    "# 監視方式 実行履歴（自動追記。時刻は JST）`n`n| 開始 | 終了 | チャネル | 担当 | 依頼（先頭行） | 結果 |`n|---|---|---|---|---|---|" | Set-Content -Encoding UTF8 -LiteralPath $history
  }
  Add-Content -Encoding UTF8 -LiteralPath $history -Value $line
}

# ---- 1 回の作業を起動 ----------------------------------------------------------
function Invoke-Turn($spec, [string]$summary) {
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $ch = $spec.name
  $rules = if ($ch -eq "dev") { "「A. 固定プロンプト」と「B. 今回の依頼」" } else { "Codex引き渡しプロンプト.md の「A. 固定プロンプト」の規則と、このファイルの「今回の依頼」" }
  $prompt = @"
ai-trader の作業フォルダで、$($spec.inFile) を読み、$rules に従い、$summary を行ってください。

（自動実行モードの追加規則。チャネル: $ch）
- これは tools/watch_handoff.ps1 による無人実行です。人間はこのターン中に応答できません。
- 自分宛ての依頼ファイル $($spec.inFile) は読むだけで、変更しないでください。
- 人間の判断が必要な事項が出たら $($spec.qFile) に質問を書いて作業を止め、$($spec.outFile) は更新しないでください。
- 作業・検証・成果物・履歴の保存をすべて終えた最後の操作として、次に相手へ渡す作業がある場合のみ $($spec.outFile) の「今回の依頼」セクションを更新してください。
  「今回の依頼: …」は 1 物理行・1 件だけ。複数工程は本文の番号付き項目に書いてください。
- 相手に渡す作業がなく全体が完了した場合は、$($spec.outFile) の先頭行を「今回の依頼: 引き渡し不要（理由）」にしてください。
- チャネル $ch 以外の引き渡しファイルは変更しないでください（開発ループと自動連携を混ぜない）。
- 受入テストの削除・skip・xfail・条件緩和、勝てるまでのパラメータ探索は禁止です。
- 報告の末尾は「報告の末尾」テンプレートに従ってください。
"@
  $promptFile = Join-Path $logDir "${ch}_${Agent}_${stamp}_prompt.txt"
  $outLog     = Join-Path $logDir "${ch}_${Agent}_${stamp}_stdout.txt"
  $errLog     = Join-Path $logDir "${ch}_${Agent}_${stamp}_stderr.txt"
  $lastFile   = Join-Path $logDir "${ch}_${Agent}_${stamp}_last.md"

  if ($Agent -eq "codex") {
    $exe = Get-Command codex -CommandType Application -ErrorAction SilentlyContinue
    if (-not $exe) { return @{ status = "error"; detail = "codex が見つかりません（npm install -g @openai/codex）" } }
    $cmd = "`"$($exe.Source)`" -a never exec --sandbox workspace-write --skip-git-repo-check -C `"$root`" --output-last-message `"$lastFile`""
    if ($CodexModel) { $cmd += " -m $CodexModel" }
    $cmd += " -"
  } else {
    $exe = Get-Command claude -CommandType Application -ErrorAction SilentlyContinue
    if (-not $exe) { return @{ status = "error"; detail = "claude が見つかりません（npm install -g @anthropic-ai/claude-code）" } }
    $cmd = "`"$($exe.Source)`" -p --output-format text"
    if ($ClaudeModel) { $cmd += " --model $ClaudeModel" }
    if ($ClaudeSkipPermissions) { $cmd += " --dangerously-skip-permissions" }
    else { $cmd += " --permission-mode acceptEdits --allowedTools `"Read,Edit,Write,MultiEdit,Glob,Grep,Bash(python:*),Bash(python3:*),Bash(pytest:*),Bash(git:*),Bash(dir:*),Bash(ls:*),Bash(type:*),Bash(cat:*)`"" }
  }
  $cmdLine = "chcp 65001>nul && cd /d `"$root`" && $cmd < `"$promptFile`" > `"$outLog`" 2> `"$errLog`""
  Log "[$ch] 依頼: $summary"
  Log "[$ch] cmd : $cmd"
  if ($DryRun) { return @{ status = "dryrun" } }

  [IO.File]::WriteAllText($promptFile, $prompt, $utf8NoBom)
  $startJ = Now-JST
  $start = Get-Date
  $outBefore = (Get-Request $spec.outPath).hash
  $p = Start-Process -FilePath "cmd.exe" -ArgumentList "/d", "/s", "/c", "`"$cmdLine`"" -NoNewWindow -PassThru
  if (-not $p.WaitForExit($TurnTimeoutMin * 60 * 1000)) {
    try { & taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null } catch {}
    Append-History "| $startJ | $(Now-JST) | $ch | $Agent | $summary | タイムアウト（$TurnTimeoutMin 分）→ HALT |"
    return @{ status = "timeout"; detail = "タイムアウト $TurnTimeoutMin 分。ログ: $errLog" }
  }
  if ($Agent -eq "claude" -and (Test-Path -LiteralPath $outLog)) { Copy-Item -LiteralPath $outLog -Destination $lastFile -Force }
  $elapsed = [int]((Get-Date) - $start).TotalMinutes
  Log "[$ch] 終了 exit=$($p.ExitCode) ($elapsed 分)"

  if ((Get-Mtime $spec.qPath) -ge $start.ToUniversalTime()) {
    Append-History "| $startJ | $(Now-JST) | $ch | $Agent | $summary | 質問あり → $($spec.qFile)（人間待ち） |"
    return @{ status = "question" }
  }
  if ($p.ExitCode -ne 0) {
    Append-History "| $startJ | $(Now-JST) | $ch | $Agent | $summary | 異常終了 exit=$($p.ExitCode) → HALT |"
    return @{ status = "error"; detail = "CLI 異常終了 exit=$($p.ExitCode)。ログ: $errLog" }
  }
  $outAfter = Get-Request $spec.outPath
  if ($outAfter.hash -eq $outBefore) {
    Append-History "| $startJ | $(Now-JST) | $ch | $Agent | $summary | 完了したが $($spec.outFile) が未更新（引き渡しなし）→ HALT |"
    return @{ status = "nohandoff"; detail = "$($spec.outFile) が更新されていません。$lastFile を確認" }
  }
  Append-History "| $startJ | $(Now-JST) | $ch | $Agent | $summary | 完了。次: $($outAfter.summary) |"
  return @{ status = "ok"; next = $outAfter.summary }
}

# ---- 1 チャネル分の確認。戻り値: idle / started:<status> ----------------------------
function Check-Channel($spec) {
  $state = Load-State $spec.stateFile
  $today = (Get-Date).ToUniversalTime().AddHours(9).ToString("yyyy-MM-dd")
  if ($state.date -ne $today) { $state.date = $today; $state.runsToday = 0 }

  $req = Get-Request $spec.inPath
  if (-not $req.ok) {
    if ($req.reason -ne "ファイルなし" -and $state.candidateHash -ne "invalid") { Log "[$($spec.name)] 起動しません: $($req.reason)"; if (-not $DryRun) { $state.candidateHash = "invalid"; Save-State $spec.stateFile $state } }
    return "idle"
  }
  if ($state.processed -contains $req.hash) { return "idle" }
  if ($req.summary -match '^引き渡し不要') {
    Log "[$($spec.name)] 完了状態: $($req.summary)"
    if (-not $DryRun) { $state.processed = @($state.processed + $req.hash | Select-Object -Last 200); $state.candidateHash = ""; Save-State $spec.stateFile $state }
    return "idle"
  }
  # 安定観測（H05）: 同じ hash を StableSec 以上の間隔で 2 回見る。かつ mtime も StableSec より古い
  $nowU = (Get-Date).ToUniversalTime()
  if ($state.candidateHash -ne $req.hash) {
    Log "[$($spec.name)] 新しい依頼を検知: $($req.summary)（$StableSec 秒後に再確認）"
    if (-not $DryRun) { $state.candidateHash = $req.hash; $state.candidateSeenUtc = $nowU.ToString("o"); Save-State $spec.stateFile $state }
    if (-not $DryRun) { return "idle" }
  } else {
    $seen = [datetime]::Parse($state.candidateSeenUtc).ToUniversalTime()
    if (($nowU - $seen).TotalSeconds -lt $StableSec) { return "idle" }
  }
  if (($nowU - (Get-Mtime $spec.inPath)).TotalSeconds -lt $StableSec) { Log "[$($spec.name)] 更新直後のため待機"; return "idle" }
  # 質問待ち（H06）
  if ((Test-Path -LiteralPath $spec.qPath) -and ((Get-Content -Raw -Encoding UTF8 -LiteralPath $spec.qPath) -notmatch '回答済')) {
    Log "[$($spec.name)] $($spec.qFile) に未回答の質問。起動しません（回答後に「回答済」と追記）"; return "idle"
  }
  # 上限（H10）
  if ($state.runsToday -ge $MaxRunsPerDay) { Log "[$($spec.name)] 本日の起動上限（$MaxRunsPerDay）に到達"; return "idle" }
  if ($state.runsTotal -ge $MaxRunsTotal) { Log "[$($spec.name)] 累計上限（$MaxRunsTotal）に到達。-ResetCounters で再開"; return "idle" }

  if ($DryRun) { $null = Invoke-Turn $spec $req.summary; return "started:dryrun" }

  # ロック取得（H03）→ 取得後に依頼を読み直して不変を確認
  if (-not (Acquire-Lock "[$($spec.name)] $($req.summary)")) { Log "ロック取得できず（他の作業中）: $lockFile"; return "idle" }
  try {
    $again = Get-Request $spec.inPath
    if (-not $again.ok -or $again.hash -ne $req.hash) { Log "[$($spec.name)] ロック取得後に依頼が変化。今回は見送り"; return "idle" }
    $state.runsToday = [int]$state.runsToday + 1; $state.runsTotal = [int]$state.runsTotal + 1
    $state.lastRunJST = Now-JST; $state.lastResult = "running"
    Save-State $spec.stateFile $state
    $r = Invoke-Turn $spec $req.summary
    $state.lastResult = $r.status
    if ($r.status -eq "ok" -or $r.status -eq "question") {
      $state.processed = @($state.processed + $req.hash | Select-Object -Last 200); $state.candidateHash = ""
    } else {
      Halt "[$($spec.name)] $($r.status): $($r.detail)`n依頼: $($req.summary)"
    }
    Save-State $spec.stateFile $state
    Log "[$($spec.name)] 結果: $($r.status)"
    return "started:$($r.status)"
  } finally { Release-Lock }
}

function Check-Once {
  if (Test-Path -LiteralPath $haltFile) { Log "HALT 中（$haltFile を確認して削除すると再開）"; return "halted" }
  foreach ($ch in $Channel) {
    $res = Check-Channel (Get-ChannelSpec $ch)
    if ($res -ne "idle") { return $res }      # 1 回の確認で起動するのは 1 チャネルまで
  }
  return "idle"
}

# ---- メイン -----------------------------------------------------------------
if ($Status) {
  Write-Host "runtime: $rtDir"; Write-Host "lock   : $(if (Test-Path -LiteralPath $lockFile) { Get-Content -Raw -LiteralPath $lockFile } else { 'なし' })"
  Write-Host "halt   : $(if (Test-Path -LiteralPath $haltFile) { Get-Content -Raw -LiteralPath $haltFile } else { 'なし' })"
  foreach ($ch in $Channel) { $s = Get-ChannelSpec $ch; $st = Load-State $s.stateFile; $rq = Get-Request $s.inPath
    Write-Host "[$ch] in=$($s.inFile) 依頼=$(if ($rq.ok) { $rq.summary } else { '(不正: ' + $rq.reason + ')' }) hash=$($rq.hash) 処理済み=$(($st.processed -contains $rq.hash)) 本日=$($st.runsToday)/$MaxRunsPerDay 累計=$($st.runsTotal)/$MaxRunsTotal 最終=$($st.lastRunJST) $($st.lastResult)" }
  exit 0
}
if ($ResetCounters) {
  foreach ($ch in $Channel) { $s = Get-ChannelSpec $ch; $st = Load-State $s.stateFile; $st.runsTotal = 0; $st.runsToday = 0; Save-State $s.stateFile $st; Log "[$ch] カウンタをリセットしました" }
  exit 0
}
$targets = ($Channel | ForEach-Object { (Get-ChannelSpec $_).inFile }) -join " / "
Log "見張り開始: $targets → 更新されたら $Agent を起動（間隔 $IntervalSec 秒、安定 $StableSec 秒、上限 $MaxRunsPerDay 回/日・累計 $MaxRunsTotal 回、runtime: $rtDir）"
if ($Once) {
  $res = Check-Once
  switch -Wildcard ($res) { "started:question" { exit 2 } "started:nohandoff" { exit 4 } "started:error" { exit 3 } "started:timeout" { exit 3 } "halted" { exit 3 } default { exit 0 } }
}
while ($true) {
  try { Check-Once | Out-Null } catch { Log "エラー: $($_.Exception.Message)"; Release-Lock; Halt "内部エラー: $($_.Exception.Message)" }
  Start-Sleep -Seconds $IntervalSec
}
