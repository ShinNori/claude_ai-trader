param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))
# Offline evidence harness. No agent, Git, notification, or production state writes.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$utf8NoBom = New-Object Text.UTF8Encoding($false)
$bom = New-Object Text.UTF8Encoding($true)
$base = Join-Path ([IO.Path]::GetTempPath()) ('handoff-review-' + [guid]::NewGuid().ToString('N'))
$root = Join-Path $base '日本語 ! space'
New-Item -ItemType Directory -Path (Join-Path $root 'tools'),(Join-Path $root 'build-codex') -Force | Out-Null
$inName = 'Codex引き渡しプロンプト.md'
$outName = 'build-codex/Claude引き渡しプロンプト.md'
foreach ($name in 'watch_handoff.ps1','pingpong.ps1') {
  Copy-Item -LiteralPath (Join-Path $ProjectRoot "tools/$name") -Destination (Join-Path $root "tools/$name")
}
Copy-Item -LiteralPath (Join-Path $ProjectRoot $inName) -Destination (Join-Path $root $inName)
Copy-Item -LiteralPath (Join-Path $ProjectRoot $outName) -Destination (Join-Path $root $outName)
(Get-Item -LiteralPath (Join-Path $root $inName)).LastWriteTimeUtc = [datetime]::UtcNow.AddMinutes(-5)
$env:PINGPONG_NOTIFY_CMD = ''
Write-Output "PowerShell=$($PSVersionTable.PSVersion)"
Write-Output "Isolation=$root"
foreach ($name in 'watch_handoff.ps1','pingpong.ps1') {
  $path = Join-Path $root "tools/$name"
  $tokens=$null; $errors=$null
  [void][Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
  $bytes=[IO.File]::ReadAllBytes($path)
  Write-Output "PARSE $name BOM=$([BitConverter]::ToString($bytes[0..2])) errors=$(@($errors).Count)"
  foreach ($err in $errors) { Write-Output "line=$($err.Extent.StartLineNumber) id=$($err.ErrorId) text=$($err.Extent.Text)" }
}
$ps = Join-Path $PSHOME 'powershell.exe'
Write-Output 'COMMAND watch_handoff.ps1 -Agent codex -Once -DryRun -NoGit'
& $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'tools/watch_handoff.ps1') -Agent codex -Once -DryRun -NoGit
Write-Output "exit=$LASTEXITCODE"
$stateFile = Join-Path $root 'logs/watch/state_codex.json'
Write-Output ('State after DryRun: ' + [IO.File]::ReadAllText($stateFile))
Write-Output 'COMMAND watch_handoff.ps1 -Agent codex -Once -DryRun -NoGit (same B)'
& $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'tools/watch_handoff.ps1') -Agent codex -Once -DryRun -NoGit
Write-Output "exit=$LASTEXITCODE"
Write-Output 'COMMAND watch_handoff.ps1 -Agent codex -DryRun -NoGit (bounded to 2 seconds, same isolated state)'
$p=Start-Process -FilePath $ps -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+(Join-Path $root 'tools/watch_handoff.ps1')+'"'),'-Agent','codex','-DryRun','-NoGit') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $base 'continuous.txt') -RedirectStandardError (Join-Path $base 'continuous.err')
$ended=$p.WaitForExit(2000)
if (-not $ended) { Stop-Process -Id $p.Id -Force; $p.WaitForExit() }
Write-Output ([IO.File]::ReadAllText((Join-Path $base 'continuous.txt')))
Write-Output "continuous_returned_without_stop=$ended"
Write-Output 'COMMAND pingpong.ps1 -DryRun -NoGit -MaxRounds 1 (original bytes)'
$prev=$ErrorActionPreference; $ErrorActionPreference='Continue'
& $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'tools/pingpong.ps1') -DryRun -NoGit -MaxRounds 1 2>&1 | ForEach-Object { "$($_)" }
Write-Output "exit=$LASTEXITCODE"
$ErrorActionPreference=$prev

# Load only original watch function definitions; substitute all external actions.
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'tools/watch_handoff.ps1'),[ref]$tokens,[ref]$errors)
$defs=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)
foreach ($def in $defs) { Invoke-Expression $def.Extent.Text }
$Agent='codex'; $StableSec=60; $MaxRunsPerDay=10; $DryRun=$false; $NoGit=$true
$inFile=$inName; $inPath=Join-Path $root $inName; $outPath=Join-Path $root $outName
$qFile='build-codex/QUESTIONS.md'; $qPath=Join-Path $root $qFile
$lockFile=Join-Path $root 'logs/watch/codex.lock'
$script:launches=0
function Ensure-Git { return $false }
function Invoke-Turn([string]$summary) { $script:launches++; return 'stub' }
$script:checks=0
function Check([bool]$ok,[string]$name) { if (-not $ok) { throw "FAIL $name" }; $script:checks++; Write-Output "OBSERVED $name" }
function Put([string]$text) { [IO.File]::WriteAllText($inPath,$text,$utf8NoBom); (Get-Item $inPath).LastWriteTimeUtc=[datetime]::UtcNow.AddMinutes(-5) }
function Reset-State { Save-State ([pscustomobject]@{lastHash='';date='';runsToday=0;lastRunJST=''}) }
Reset-State
Put "## B. 今回の依頼`n今回の依頼: first`n`n### 保留中の依頼`n今回の依頼: old`n---`n"
$hash1=Get-B-Hash $inPath
Put "## B. 今回の依頼`n今回の依頼: first`n`n### 保留中の依頼`n今回の依頼: changed old`n---`n"
Check ((Get-B-Hash $inPath) -ne $hash1) 'pending-subsection changes active B hash'
Check ((Get-Request-Line $inPath) -eq 'first') 'multiple requests silently select first'
Put "## B. 今回の依頼`n本文のみ`n### 保留中の依頼`n今回の依頼: old`n---`n"
Check ((Get-Request-Line $inPath) -eq 'old') 'missing active request selects pending request'
Put "## A. fixed`n今回の依頼: stale`n"
Check ((Get-Request-Line $inPath) -eq 'stale') 'missing B falls back to entire file'
Put "## B. 今回の依頼`n今回の依頼:`ncontinued`n---`n"
Check ((Get-Request-Line $inPath) -eq 'continued') 'whitespace regex consumes newline after empty colon'
Put "## B. 今回の依頼`n今回の依頼: 引き渡し不要の検出を修正`n---`n"
$n=$script:launches; Check-Once
Check ($script:launches -eq $n) 'substring done detection suppresses real request'
Reset-State; Put "## B. 今回の依頼`n今回の依頼: incomplete document`n"
$n=$script:launches; Check-Once
Check ($script:launches -eq $n+1) 'old mtime permits incomplete document'
Check ((Load-State).runsToday -is [int]) 'ConvertFrom-Json numeric runsToday is Int32 on PS5.1'
Check-Once
Check ($script:launches -eq $n+1) 'immediate duplicate hash is suppressed'
Reset-State; Put "## B. 今回の依頼`n今回の依頼: A`n---`n"; Check-Once
Put "## B. 今回の依頼`n今回の依頼: B`n---`n"; Check-Once
$n=$script:launches
Put "## B. 今回の依頼`n今回の依頼: A`n---`n"; Check-Once
Check ($script:launches -eq $n+1) 'A then B then restored A launches A again'
Reset-State; Put "## B. 今回の依頼`n今回の依頼: needs answer`n---`n"
[IO.File]::WriteAllText($qPath,'UNRESOLVED',$utf8NoBom)
$n=$script:launches; Check-Once
Check ($script:launches -eq $n) 'newer question blocks startup'
(Get-Item $qPath).LastWriteTimeUtc=[datetime]::UtcNow.AddMinutes(-10)
Check-Once
Check ($script:launches -eq $n+1) 'newer handoff file bypasses unresolved question'
Reset-State; $MaxRunsPerDay=1
Put "## B. 今回の依頼`n今回の依頼: day1`n---`n"; Check-Once
Put "## B. 今回の依頼`n今回の依頼: day2`n---`n"
$n=$script:launches; Check-Once
Check ($script:launches -eq $n) 'daily cap blocks changed B'
$s=Load-State; $s.date='2000-01-01'; Save-State $s; Check-Once
Check ($script:launches -eq $n+1) 'next JST date resets cap and resumes'
Write-Output "OBSERVATION CHECKS=$script:checks (these reproduce defects, not acceptance passes)"

# Actual cmd.exe redirection, using a local echo helper instead of an LLM.
$helper=Join-Path $base 'echo.ps1'
[IO.File]::WriteAllText($helper,'[Console]::InputEncoding=[Text.Encoding]::UTF8; [Console]::OutputEncoding=[Text.Encoding]::UTF8; [Console]::Out.Write([Console]::In.ReadToEnd()); [Console]::Error.Write("stderr-ok")',$bom)
$inputFile=Join-Path $root 'prompt.txt'; $outputFile=Join-Path $root 'stdout.txt'; $errorFile=Join-Path $root 'stderr.txt'
[IO.File]::WriteAllText($inputFile,'日本語 ! prompt',$utf8NoBom)
$cmdLine="chcp 65001>nul && cd /d `"$root`" && `"$ps`" -NoProfile -File `"$helper`" < `"$inputFile`" > `"$outputFile`" 2> `"$errorFile`""
$p=Start-Process -FilePath 'cmd.exe' -ArgumentList '/c',$cmdLine -WindowStyle Hidden -PassThru
if (-not $p.WaitForExit(10000)) { Stop-Process -Id $p.Id -Force; throw 'echo helper timeout' }
Write-Output "REDIRECTION exit=$($p.ExitCode) stdout=$([IO.File]::ReadAllText($outputFile)) stderr=$([IO.File]::ReadAllText($errorFile))"
Write-Output 'No real agent invocation performed. Isolated evidence directory retained.'
