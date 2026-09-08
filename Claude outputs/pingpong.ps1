# tools/pingpong.ps1 — Claude Code と Codex を交互に自動実行する（ピンポン方式）
#
# 使い方（ai-trader フォルダで）:
#   powershell -ExecutionPolicy Bypass -File tools\pingpong.ps1                       # Codex から開始、最大 3 往復
#   powershell -ExecutionPolicy Bypass -File tools\pingpong.ps1 -StartWith claude -MaxRounds 2
#   powershell -ExecutionPolicy Bypass -File tools\pingpong.ps1 -DryRun               # 何を実行するかだけ表示
#
# 通信路は既存の引き渡しファイルそのもの:
#   Claude → Codex : Codex引き渡しプロンプト.md            の「B. 今回の依頼」
#   Codex  → Claude: build-codex/Claude引き渡しプロンプト.md の「B. 今回の依頼」
# 各ターンは「相手が書いた B の先頭行（今回の依頼: …）」から一文プロンプトを組み立てて CLI に渡す。
#
# 停止条件（どれか 1 つで止まる）:
#   1. MaxRounds 往復に達した
#   2. build-codex/QUESTIONS.md または ops/QUESTIONS.md がこのターンで更新された（人間の判断待ち）
#   3. 相手向け B に「引き渡し不要」と書かれた（作業完了）
#   4. ターン後に相手向け B が更新されていない（引き渡しが行われなかった＝異常）
#   5. ターンが TurnTimeoutMin 分を超えた
# 毎ターン終了後に git commit（-NoGit で無効）。履歴は tools/pingpong_history.md に追記。

param(
  [int]$MaxRounds = 3,
  [ValidateSet("codex", "claude")] [string]$StartWith = "codex",
  [string]$CodexModel = "",
  [string]$ClaudeModel = "",
  [int]$TurnTimeoutMin = 45,
  [switch]$ClaudeSkipPermissions,   # claude を --dangerously-skip-permissions で起動（既定は allowedTools を限定）
  [switch]$NoGit,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)   # ai-trader/
Set-Location $root
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$codexHandoff  = Join-Path $root "Codex引き渡しプロンプト.md"                 # Claude が書き、Codex が読む
$claudeHandoff = Join-Path $root "build-codex\Claude引き渡しプロンプト.md"    # Codex が書き、Claude が読む
$questionFiles = @((Join-Path $root "build-codex\QUESTIONS.md"), (Join-Path $root "ops\QUESTIONS.md"))
$logDir  = Join-Path $root "logs\pingpong"
$history = Join-Path $root "tools\pingpong_history.md"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Now-JST { (Get-Date).ToUniversalTime().AddHours(9).ToString("yyyy-MM-dd HH:mm") }

function Get-Section-B([string]$path) {
  if (-not (Test-Path $path)) { return "" }
  $text = Get-Content -Raw -Encoding UTF8 $path
  $m = [regex]::Match($text, '(?ms)^## B\..*?(?=^## [^B]|^---\s*$|\z)')
  if ($m.Success) { return $m.Value } else { return $text }
}
function Get-Request-Line([string]$path) {
  $b = Get-Section-B $path
  $m = [regex]::Match($b, '(?m)^\s*今回の依頼[:：]\s*(.+?)\s*$')
  if ($m.Success) { return $m.Groups[1].Value } else { return "" }
}
function Get-B-Hash([string]$path) {
  $b = Get-Section-B $path
  $sha = [System.Security.Cryptography.SHA256]::Create()
  return [BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($b))).Replace("-", "").Substring(0, 12)
}
function Get-Mtime([string]$path) { if (Test-Path $path) { (Get-Item $path).LastWriteTimeUtc } else { [datetime]::MinValue } }

function Ensure-Git {
  if ($NoGit) { return $false }
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Host "[pingpong] git がないためスナップショットは取りません"; return $false }
  if (-not (Test-Path (Join-Path $root ".git"))) {
    git init -q | Out-Null
    if (-not (Test-Path (Join-Path $root ".gitignore"))) {
      "logs/`nresults/`n__pycache__/`n*.pyc`n.env`n*.sqlite`n*.sqlite-journal`n.pytest_cache/`n" | Set-Content -Encoding UTF8 (Join-Path $root ".gitignore")
    }
    git add -A | Out-Null
    git -c user.name=pingpong -c user.email=pingpong@local commit -q -m "pingpong: initial snapshot" | Out-Null
    Write-Host "[pingpong] git リポジトリを初期化しました（初回スナップショット）"
  }
  return $true
}
function Git-Snapshot([string]$msg) {
  git add -A | Out-Null
  $status = git status --porcelain
  if ($status) { git -c user.name=pingpong -c user.email=pingpong@local commit -q -m $msg | Out-Null; Write-Host "[pingpong] git commit: $msg" }
  else { Write-Host "[pingpong] 変更なし（commit なし）" }
}

function Append-History([string]$line) {
  if (-not (Test-Path $history)) {
    "# ピンポン実行履歴（自動追記。時刻は JST）`n`n| 開始 | 終了 | ラウンド | 担当 | 依頼（B の先頭行） | 結果 |`n|---|---|---|---|---|---|" | Set-Content -Encoding UTF8 $history
  }
  Add-Content -Encoding UTF8 -Path $history -Value $line
}

# ---- 1 ターン実行 -------------------------------------------------------------
function Invoke-Turn([string]$agent, [int]$round) {
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  if ($agent -eq "codex") {
    $readFile  = "Codex引き渡しプロンプト.md";              $readPath  = $codexHandoff
    $writeFile = "build-codex/Claude引き渡しプロンプト.md"; $writePath = $claudeHandoff
    $qFile = "build-codex/QUESTIONS.md"
  } else {
    $readFile  = "build-codex/Claude引き渡しプロンプト.md"; $readPath  = $claudeHandoff
    $writeFile = "Codex引き渡しプロンプト.md";              $writePath = $codexHandoff
    $qFile = "ops/QUESTIONS.md"
  }
  $summary = Get-Request-Line $readPath
  if (-not $summary) { throw "$readFile の B に「今回の依頼: …」の行がありません。手動で B を整えてから再実行してください" }
  if ($summary -match "引き渡し不要") { return @{ status = "done"; summary = $summary } }

  $prompt = @"
ai-trader の作業フォルダで、$readFile を読み、「A. 固定プロンプト」と「B. 今回の依頼」に従い、$summary を行ってください。

（自動実行モードの追加規則）
- これは tools/pingpong.ps1 による無人実行です。人間はこのターン中に応答できません。
- 人間の判断が必要な事項が出たら $qFile に質問を書いて作業を止め、$writeFile の B は更新しないでください。
- 作業が完了し、次に相手へ渡す作業がある場合のみ $writeFile の B を更新してください（先頭行は「今回の依頼: …」）。
- 相手に渡す作業がなく全体が完了した場合は、$writeFile の B の先頭行を「今回の依頼: 引き渡し不要（理由）」にしてください。
- 受入テストの削除・skip・xfail・条件緩和、勝てるまでのパラメータ探索は禁止です。
- 報告の末尾は「報告の末尾」テンプレートに従ってください。
"@
  $promptFile = Join-Path $logDir "r${round}_${agent}_${stamp}_prompt.txt"
  $outFile    = Join-Path $logDir "r${round}_${agent}_${stamp}_stdout.txt"
  $errFile    = Join-Path $logDir "r${round}_${agent}_${stamp}_stderr.txt"
  $lastFile   = Join-Path $logDir "r${round}_${agent}_${stamp}_last.md"
  [IO.File]::WriteAllText($promptFile, $prompt, $utf8NoBom)

  if ($agent -eq "codex") {
    if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { throw "codex が見つかりません（npm install -g @openai/codex）" }
    $cmd = "codex exec --sandbox workspace-write --skip-git-repo-check -C `"$root`" --output-last-message `"$lastFile`""
    if ($CodexModel) { $cmd += " -m $CodexModel" }
    $cmd += " -"      # プロンプトは標準入力から
  } else {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { throw "claude が見つかりません（npm install -g @anthropic-ai/claude-code）" }
    $cmd = "claude -p --output-format text"
    if ($ClaudeModel) { $cmd += " --model $ClaudeModel" }
    if ($ClaudeSkipPermissions) { $cmd += " --dangerously-skip-permissions" }
    else { $cmd += " --permission-mode acceptEdits --allowedTools `"Read,Edit,Write,MultiEdit,Glob,Grep,Bash(python:*),Bash(python3:*),Bash(pytest:*),Bash(git:*),Bash(dir:*),Bash(ls:*),Bash(type:*),Bash(cat:*)`""
    }
  }
  $cmdLine = "chcp 65001>nul && cd /d `"$root`" && $cmd < `"$promptFile`" > `"$outFile`" 2> `"$errFile`""

  Write-Host ""
  Write-Host "[pingpong] ===== round $round / $agent ====="
  Write-Host "[pingpong] 依頼: $summary"
  Write-Host "[pingpong] cmd : $cmd"
  if ($DryRun) { return @{ status = "dryrun"; summary = $summary } }

  $bHashBefore = Get-B-Hash $writePath
  $qBefore = Get-Mtime (Join-Path $root $qFile)
  $start = Get-Date
  $startJ = Now-JST
  $p = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmdLine -NoNewWindow -PassThru
  if (-not $p.WaitForExit($TurnTimeoutMin * 60 * 1000)) {
    try { Stop-Process -Id $p.Id -Force } catch {}
    Append-History "| $startJ | $(Now-JST) | $round | $agent | $summary | タイムアウト（$TurnTimeoutMin 分）で停止 |"
    return @{ status = "timeout"; summary = $summary }
  }
  $elapsed = [int]((Get-Date) - $start).TotalMinutes
  Write-Host "[pingpong] 終了 exit=$($p.ExitCode) ($elapsed 分)  出力: $outFile"
  if ($agent -eq "claude" -and (Test-Path $outFile)) { Copy-Item $outFile $lastFile -Force }

  # ---- 停止判定 ----
  $qPath = Join-Path $root $qFile
  if ((Get-Mtime $qPath) -gt $qBefore -and (Get-Mtime $qPath) -ge $start.ToUniversalTime()) {
    Append-History "| $startJ | $(Now-JST) | $round | $agent | $summary | 質問あり → $qFile（人間待ち） |"
    return @{ status = "question"; summary = $summary; file = $qFile }
  }
  if ($p.ExitCode -ne 0) {
    Append-History "| $startJ | $(Now-JST) | $round | $agent | $summary | 異常終了 exit=$($p.ExitCode)（$errFile） |"
    return @{ status = "error"; summary = $summary }
  }
  $bHashAfter = Get-B-Hash $writePath
  $next = Get-Request-Line $writePath
  if ($bHashAfter -eq $bHashBefore) {
    Append-History "| $startJ | $(Now-JST) | $round | $agent | $summary | 完了したが $writeFile の B が未更新（引き渡しなし）→ 停止 |"
    return @{ status = "nohandoff"; summary = $summary }
  }
  Append-History "| $startJ | $(Now-JST) | $round | $agent | $summary | 完了。次: $next |"
  if ($next -match "引き渡し不要") { return @{ status = "done"; summary = $summary; next = $next } }
  return @{ status = "ok"; summary = $summary; next = $next }
}

# ---- メインループ -------------------------------------------------------------
$useGit = Ensure-Git
$agent = $StartWith
$result = $null
for ($round = 1; $round -le $MaxRounds; $round++) {
  foreach ($half in 1..2) {
    $result = Invoke-Turn $agent $round
    if ($result.status -eq "dryrun") { $agent = if ($agent -eq "codex") { "claude" } else { "codex" }; continue }
    if ($useGit) { Git-Snapshot "pingpong r$round $agent: $($result.summary)" }
    if ($result.status -ne "ok") { break }
    $agent = if ($agent -eq "codex") { "claude" } else { "codex" }
  }
  if ($result.status -ne "ok" -and $result.status -ne "dryrun") { break }
}

# ---- 終了報告 -------------------------------------------------------------
$reason = switch ($result.status) {
  "ok"        { "ラウンド上限 $MaxRounds に到達（次の依頼は残っています: $($result.next)）" }
  "done"      { "作業完了（引き渡し不要）: $($result.next)" }
  "question"  { "人間の判断待ち: $($result.file) を確認してください" }
  "nohandoff" { "相手向け B が更新されなかったため停止（ログを確認）" }
  "timeout"   { "ターンがタイムアウト" }
  "error"     { "CLI が異常終了（logs/pingpong の stderr を確認）" }
  "dryrun"    { "DryRun（実行なし）" }
  default     { $result.status }
}
$summaryFile = Join-Path $logDir "last_run.md"
$report = "# ピンポン実行結果`n`n終了時刻: $(Now-JST) JST`n停止理由: $reason`n`n履歴: tools/pingpong_history.md`nログ: logs/pingpong/`n"
[IO.File]::WriteAllText($summaryFile, $report, $utf8NoBom)
Write-Host ""
Write-Host "[pingpong] ===== 終了 ====="
Write-Host "[pingpong] $reason"
Write-Host "[pingpong] 結果: $summaryFile"
if ($env:PINGPONG_NOTIFY_CMD) { try { cmd.exe /c "$env:PINGPONG_NOTIFY_CMD `"$reason`"" } catch {} }
