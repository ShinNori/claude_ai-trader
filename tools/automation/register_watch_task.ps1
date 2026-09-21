# DryRun never registers, launches an agent, or creates logs/state.
param(
    [switch]$Unregister, [switch]$DryRun, [switch]$Replace, [switch]$RegisterOnly,
    [ValidateSet('codex','claude')][string]$Agent = 'codex',
    [ValidateSet('auto','dev','auto,dev','dev,auto')][string]$Channel = 'auto,dev',
    [ValidateRange(1,1000)][int]$MaxRunsPerDay = 10,
    [string]$PythonExe = '', [string]$CodexExe = '', [string]$ClaudeExe = '',
    [string]$RuntimeHome = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'handoff_common.ps1')
$name = "ai-trader handoff watch $Agent"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$description = "ai-trader handoff: $root"
if ($Unregister) {
    if ($DryRun) { [pscustomobject]@{Operation='unregister';TaskName=$name} | ConvertTo-Json; exit 0 }
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        if ($task.Description -ne $description) { throw 'Task belongs to another or an older registration; inspect it in Task Scheduler.' }
        if ($task.State -eq 'Running') { throw 'Stop the task and confirm child processes have ended before unregistering.' }
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    Write-Output "Unregistered: $name"
    exit 0
}
$PythonExe = Resolve-HandoffPython -PythonExe $PythonExe
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$previewArgs = @('watch','--channel',$Channel,'--agent',$Agent,'--dry-run')
if ($RuntimeHome) { $previewArgs += @('--runtime',$RuntimeHome) }
if ($CodexExe) { $previewArgs += @('--codex-exe',$CodexExe) }
if ($ClaudeExe) { $previewArgs += @('--claude-exe',$ClaudeExe) }
$previews = @(& $PythonExe (Join-Path $PSScriptRoot 'codex_engine.py') @previewArgs)
if ($LASTEXITCODE -ne 0) { throw 'Handoff preview failed. No task was registered.' }
$decoded = @($previews | ForEach-Object { $_ | ConvertFrom-Json })
foreach ($preview in $decoded) {
    if (-not $preview.cli_found) { throw "CLI unavailable: $($preview.cli_error)" }
    if ($preview.blocked -or $preview.question) { throw 'Resolve the reported block/questions before registration.' }
}
$cliPath = $decoded[0].command[0]
$runtimeBase = Split-Path -Parent (Split-Path -Parent $decoded[0].runtime)
$log = Join-Path $decoded[0].runtime "watch_$Agent.log"
$watch = Join-Path $PSScriptRoot 'watch_handoff.ps1'
function Quote-HandoffLiteral([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }
$arguments = "-Agent $Agent -Channel $(Quote-HandoffLiteral $Channel) -MaxRunsPerDay $MaxRunsPerDay" +
    " -PythonExe $(Quote-HandoffLiteral $PythonExe) -RuntimeHome $(Quote-HandoffLiteral $runtimeBase)" +
    " -$($Agent.Substring(0,1).ToUpper()+$Agent.Substring(1))Exe $(Quote-HandoffLiteral $cliPath)"
# Quote every variable literal, then encode. Preserve the watcher's exit code.
$body = "`$ErrorActionPreference='Stop'`n" +
    "try {`n[IO.Directory]::CreateDirectory($(Quote-HandoffLiteral (Split-Path $log))) | Out-Null`n" +
    "& $(Quote-HandoffLiteral $watch) $arguments *>> $(Quote-HandoffLiteral $log)`nexit `$LASTEXITCODE`n} catch { Write-Error `$_; exit 1 }"
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$actionArgs = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand $encoded"
$plan = [pscustomobject]@{Operation='register'; TaskName=$name; Execute=$powershell; Arguments=$actionArgs;
    WorkingDirectory=$root; Log=$log; Channel=$Channel; StartNow=(-not $RegisterOnly); Preview=$decoded}
if ($DryRun) { $plan | ConvertTo-Json -Depth 8; exit 0 }
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Description -ne $description) { throw 'Task name belongs to another or an older registration; inspect it first.' }
    if ($existing.State -eq 'Running') { throw 'Stop the running task before replacing it.' }
    if (-not $Replace) { throw 'Task already exists. Use -Replace for an intentional update.' }
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powershell -WorkingDirectory $root -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
# No retries after a safety stop. Login can start the task, but durable blocks remain.
Register-ScheduledTask -TaskName $name -Description $description -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force:$Replace | Out-Null
if (-not $RegisterOnly) { Start-ScheduledTask -TaskName $name }
Write-Output "Registered: $name (start requested: $(-not $RegisterOnly)). Check task status and $log"
