# tools/watch_handoff.ps1 — 引き渡しファイルを定期的に見張り、依頼が更新されていたら作業を引き受ける（監視方式）
#
# 使い方（ai-trader フォルダで。常駐させる。tools\start_watch_codex.cmd をダブルクリックでも可）:
#   powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent codex          # Codex 側の見張り（開発ループ＋自動連携の両チャネル）
#   powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent codex -Channel dev   # 開発ループだけ
#   powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent claude         # PC で Claude 側も自動化する場合
#   powershell -ExecutionPolicy Bypass -File tools\watch_handoff.ps1 -Agent codex -Once    # 1 回だけ確認して終了（タスクスケジューラ用）
#
# チャネル（開発と自動連携を混ぜないために分ける。見張りは両方を順に確認する）:
#   dev  : 自動投資システムの開発ループ
#          codex 宛て = Codex引き渡しプロンプト.md（## B. 今回の依頼）   claude 宛て = build-codex/Claude引き渡しプロンプト.md（## B. 今回の依頼）
#   auto : 自動連携（このスクリプト群）の作業
#          codex 宛て = tools/自動連携_Codex依頼.md（## 今回の依頼）      claude 宛て = tools/自動連携_Claude依頼.md（## 今回の依頼）
#
# 起動条件（すべて満たしたときだけ CLI を起動する）:
#   1. 依頼セクションのハッシュが前回処理したものと違う
#   2. ファイルの更新時刻から StableSec 秒以上経っている（書きかけ・Dropbox 同期中を拾わない）
#   3. 依頼の先頭行が「引き渡し不要」ではない
#   4. 自分側の QUESTIONS.md が依頼ファイルより新しくない（人間の回答待ちでない）
#   5. 当日の起動回数（チャネル別）が MaxRunsPerDay 未満（両側に見張りがあるときの無限往復防止）
#   6. ロックファイルがない（同じ CLI の二重起動防止。ロックは Agent ごとに 1 つ＝チャネルをまたいで同時には動かない）
# 起動前にハッシュを「処理済み」として記録するので、同じ依頼で二度は動かない。

param(
  [Parameter(Mandatory = $true)][ValidateSet("codex", "claude")] [string]$Agent,
  [ValidateSet("dev", "auto")] [string[]]$Channel = @("dev", "auto"),
  [int]$IntervalSec = 60,
  [int]$StableSec = 60,
  [int]$MaxRunsPerDay = 10,
  [int]$TurnTimeoutMin = 45,
  [string]$CodexModel = "",
  [string]$ClaudeModel = "",
  [switch]$ClaudeSkipPermissions,
  [switch]$NoGit,
  [switch]$Once,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)   # ai-trader/
Set-Location $root
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$logDir  = Join-Path $root "logs\watch"
$lockFile = Join-Path $logDir "$Agent.lock"
$history = Join-Path $root "tools\pingpong_history.md"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# チャネル定義: 自分宛て（in）と相手宛て（out）のファイル、質問ファイル
function Get-ChannelSpec([string]$ch) {
  if ($ch -eq "dev") {
    $codexIn  = "Codex引き渡しプロンプト.md";              $claudeIn = "build-codex/Claude引き渡しプロンプト.md"
  } else {
    $codexIn  = "tools/自動連携_Codex依頼.md";            $claudeIn = "tools/自動連携_Claude依頼.md"
  }
  if ($Agent -eq "codex") { $in = $codexIn; $out = $claudeIn; $q = "build-codex/QUESTIONS.md" }
  else                    { $in = $claudeIn; $out = $codexIn; $q = "ops/QUESTIONS.md" }
  return [pscustomobject]@{
    name = $ch; inFile = $in; outFile = $out; qFile = $q
    inPath = (Join-Path $root ($in -replace "/", "\")); outPath = (Join-Path $root ($out -replace "/", "\")); qPath = (Join-Path $root ($q -replace "/", "\"))
    stateFile = (Join-Path $logDir "state_${ch}_$Agent.json")
  }
}

function Now-JST { (Get-Date).ToUniversalTime().AddHours(9).ToString("yyyy-MM-dd HH:mm") }
function Log([string]$msg) { Write-Host "[watch/$Agent $(Get-Date -Format 'HH:mm:ss')] $msg" }

# 依頼セクション: 「## B. 今回の依頼」（開発ループ）または「## 今回の依頼」（自動連携）から、次の ## 見出し／--- まで
function Get-Section-B([string]$path) {
  if (-not (Test-Path $path)) { return "" }
  $text = Get-Content -Raw -Encoding UTF8 $path
  $m = [regex]::Match($text, '(?ms)^## (?:B\. )?今回の依頼.*?(?=^## (?!B\. |今回の依頼)|^---\s*$|\z)')
  if ($m.Success) { return $m.Value } else { return "" }
}
function Get-Request-Line([string]$path) {
  $m = [regex]::Match((Get-Section-B $path), '(?m)^\s*今回の依頼[:：]\s*(.+?)\s*$')
  if ($m.Success) { return $m.Groups[1].Value } else { return "" }
}
function Get-B-Hash([string]$path) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  return [BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes((Get-Section-B $path)))).Replace("-", "").Substring(0, 12)
}
function Get-Mtime([string]$path) { if (Test-Path $path) { (Get-Item $path).LastWriteTimeUtc } else { [datetime]::MinValue } }

function Load-State([string]$file) {
  if (Test-Path $file) { return (Get-Content -Raw -Encoding UTF8 $file | ConvertFrom-Json) }
  return [pscustomobject]@{ lastHash = ""; date = ""; runsToday = 0; lastRunJST = "" }
}
function Save-State([string]$file, $s) { [IO.File]::WriteAllText($file, ($s | ConvertTo-Json), $utf8NoBom) }

function Ensure-Git {
  if ($NoGit) { return $false }
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return $false }
  if (-not (Test-Path (Join-Path $root ".git"))) {
    git init -q | Out-Null
    if (-not (Test-Path (Join-Path $root ".gitignore"))) {
      "logs/`nresults/`n__pycache__/`n*.pyc`n.env`n*.sqlite`n*.sqlite-journal`n.pytest_cache/`n" | Set-Content -Encoding UTF8 (Join-Path $root ".gitignore")
    }
    git add -A | Out-Null
    git -c user.name=watch -c user.email=watch@local commit -q -m "watch: initial snapshot" | Out-Null
  }
  return $true
}
function Git-Snapshot([string]$msg) {
  git add -A | Out-Null
  if (git status --porcelain) { git -c user.name=watch -c user.email=watch@local commit -q -m $msg | Out-Null; Log "git commit: $msg" }
}
function Append-History([string]$line) {
  if (-not (Test-Path $history)) {
    "# ピンポン／監視 実行履歴（自動追記。時刻は JST）`n`n| 開始 | 終了 | チャネル | 担当 | 依頼（先頭行） | 結果 |`n|---|---|---|---|---|---|" | Set-Content -Encoding UTF8 $history
  }
  Add-Content -Encoding UTF8 -Path $history -Value $line
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
- 人間の判断が必要な事項が出たら $($spec.qFile) に質問を書いて作業を止め、$($spec.outFile) は更新しないでください。
- 作業が完了し、次に相手へ渡す作業がある場合のみ $($spec.outFile) の「今回の依頼」セクションを更新してください（先頭行は「今回の依頼: …」）。
- 相手に渡す作業がなく全体が完了した場合は、$($spec.outFile) の先頭行を「今回の依頼: 引き渡し不要（理由）」にしてください。
- チャネル $ch 以外の引き渡しファイルは変更しないでください（開発ループと自動連携を混ぜない）。
- 受入テストの削除・skip・xfail・条件緩和、勝てるまでのパラメータ探索は禁止です。
- 報告の末尾は「報告の末尾」テンプレートに従ってください。
"@
  $promptFile = Join-Path $logDir "${ch}_${Agent}_${stamp}_prompt.txt"
  $outLog     = Join-Path $logDir "${ch}_${Agent}_${stamp}_stdout.txt"
  $errLog     = Join-Path $logDir "${ch}_${Agent}_${stamp}_stderr.txt"
  $lastFile   = Join-Path $logDir "${ch}_${Agent}_${stamp}_last.md"
  [IO.File]::WriteAllText($promptFile, $prompt, $utf8NoBom)

  if ($Agent -eq "codex") {
    if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { throw "codex が見つかりません（npm install -g @openai/codex）" }
    $cmd = "codex exec --sandbox workspace-write --skip-git-repo-check -C `"$root`" --output-last-message `"$lastFile`""
    if ($CodexModel) { $cmd += " -m $CodexModel" }
    $cmd += " -"
  } else {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { throw "claude が見つかりません（npm install -g @anthropic-ai/claude-code）" }
    $cmd = "claude -p --output-format text"
    if ($ClaudeModel) { $cmd += " --model $ClaudeModel" }
    if ($ClaudeSkipPermissions) { $cmd += " --dangerously-skip-permissions" }
    else { $cmd += " --permission-mode acceptEdits --allowedTools `"Read,Edit,Write,MultiEdit,Glob,Grep,Bash(python:*),Bash(python3:*),Bash(pytest:*),Bash(git:*),Bash(dir:*),Bash(ls:*),Bash(type:*),Bash(cat:*)`"" }
  }
  $cmdLine = "chcp 65001>nul && cd /d `"$root`" && $cmd < `"$promptFile`" > `"$outLog`" 2> `"$errLog`""
  Log "[$ch] 依頼: $summary"
  Log "[$ch] cmd : $cmd"
  if ($DryRun) { return "dryrun" }

  $startJ = Now-JST
  $start = Get-Date
  $bBefore = Get-B-Hash $spec.outPath
  $p = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmdLine -NoNewWindow -PassThru
  if (-not $p.WaitForExit($TurnTimeoutMin * 60 * 1000)) {
    try { Stop-Process -Id $p.Id -Force } catch {}
    Append-History "| $startJ | $(Now-JST) | $ch | $Agent | $summary | タイムアウト（$TurnTimeoutMin 分） |"
    return "timeout"
  }
  if ($Agent -eq "claude" -and (Test-Path $outLog)) { Copy-Item $outLog $lastFile -Force }
  $elapsed = [int]((Get-Date) - $start).TotalMinutes
  Log "[$ch] 終了 exit=$($p.ExitCode) ($elapsed 分)"

  if ((Get-Mtime $spec.qPath) -ge $start.ToUniversalTime()) {
    Append-History "| $startJ | $(Now-JST) | $ch | $Agent | $summary | 質問あり → $($spec.qFile)（人間待ち） |"
    return "question"
  }
  if ($p.ExitCode -ne 0) {
    Append-History "| $startJ | $(Now-JST) | $ch | $Agent | $summary | 異常終了 exit=$($p.ExitCode)（$errLog） |"
    return "error"
  }
  $next = Get-Request-Line $spec.outPath
  if ((Get-B-Hash $spec.outPath) -eq $bBefore) {
    Append-History "| $startJ | $(Now-JST) | $ch | $Agent | $summary | 完了したが $($spec.outFile) が未更新（引き渡しなし） |"
    return "nohandoff"
  }
  Append-History "| $startJ | $(Now-JST) | $ch | $Agent | $summary | 完了。次: $next |"
  return "ok"
}

# ---- 1 チャネル分の確認 ---------------------------------------------------------
function Check-Channel($spec) {
  $state = Load-State $spec.stateFile
  $today = (Get-Date).ToUniversalTime().AddHours(9).ToString("yyyy-MM-dd")
  if ($state.date -ne $today) { $state.date = $today; $state.runsToday = 0 }

  if (-not (Test-Path $spec.inPath)) { return $false }                              # そのチャネルの依頼ファイルがまだ無い
  $hash = Get-B-Hash $spec.inPath
  if ($hash -eq $state.lastHash) { return $false }                                   # 変化なし（静かに戻る）
  $age = ((Get-Date).ToUniversalTime() - (Get-Mtime $spec.inPath)).TotalSeconds
  if ($age -lt $StableSec) { Log "[$($spec.name)] 更新を検知しましたが書きかけの可能性（$([int]$age) 秒前）。待機"; return $false }
  $summary = Get-Request-Line $spec.inPath
  if (-not $summary) { Log "[$($spec.name)] 「今回の依頼: …」の行がありません。処理済み扱い"; $state.lastHash = $hash; Save-State $spec.stateFile $state; return $false }
  if ($summary -match "引き渡し不要") { Log "[$($spec.name)] 完了状態（引き渡し不要）: $summary"; $state.lastHash = $hash; Save-State $spec.stateFile $state; return $false }
  if ((Get-Mtime $spec.qPath) -gt (Get-Mtime $spec.inPath)) { Log "[$($spec.name)] $($spec.qFile) が依頼より新しいため人間の回答待ち。起動しません"; return $false }
  if ($state.runsToday -ge $MaxRunsPerDay) { Log "[$($spec.name)] 本日の起動上限（$MaxRunsPerDay 回）に到達。起動しません"; return $false }

  # 起動確定: 先に処理済みとして記録（同じ依頼で二度動かない）
  $state.lastHash = $hash; $state.runsToday = [int]$state.runsToday + 1; $state.lastRunJST = Now-JST
  Save-State $spec.stateFile $state
  [IO.File]::WriteAllText($lockFile, "$(Now-JST) [$($spec.name)] $summary", $utf8NoBom)
  try {
    $useGit = Ensure-Git
    $status = Invoke-Turn $spec $summary
    if ($useGit -and $status -ne "dryrun") { Git-Snapshot "watch $Agent [$($spec.name)]: $summary" }
    Log "[$($spec.name)] 結果: $status"
    if ($env:PINGPONG_NOTIFY_CMD) { try { cmd.exe /c "$env:PINGPONG_NOTIFY_CMD `"[$Agent/$($spec.name)] $status $summary`"" } catch {} }
  } finally {
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
  }
  return $true
}

function Check-Once {
  if (Test-Path $lockFile) { Log "ロックあり（作業中）: $lockFile"; return }
  foreach ($ch in $Channel) {
    $spec = Get-ChannelSpec $ch
    if (Check-Channel $spec) { return }     # 1 回の確認で起動するのは 1 チャネルまで
  }
}

# ---- メイン -----------------------------------------------------------------
$targets = ($Channel | ForEach-Object { (Get-ChannelSpec $_).inFile }) -join " / "
Log "見張り開始: $targets → 更新されたら $Agent を起動（間隔 $IntervalSec 秒、安定 $StableSec 秒、上限 $MaxRunsPerDay 回/日/チャネル）"
if ($Once) { Check-Once; exit 0 }
while ($true) {
  try { Check-Once } catch { Log "エラー: $($_.Exception.Message)"; Remove-Item $lockFile -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds $IntervalSec
}
