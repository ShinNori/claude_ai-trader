param(
    [ValidateRange(1,1000)][int]$MaxRounds = 3,
    [ValidateSet('codex','claude')][string]$StartWith = 'codex',
    [ValidateRange(1,1440)][int]$TurnTimeoutMin = 45,
    [ValidateRange(0,86400)][int]$StableSec = 60,
    [ValidateRange(1,86400)][int]$IntervalSec = 60,
    [ValidateRange(1,1000)][int]$MaxRunsPerDay = 10,
    [string]$CodexModel = '', [string]$ClaudeModel = '',
    [string]$CodexExe = '', [string]$ClaudeExe = '',
    [string]$RuntimeHome = '', [string]$PythonExe = '',
    [switch]$DryRun, [switch]$NoGit
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'handoff_common.ps1')
$engineArgs = @('pingpong','--agent',$StartWith,'--rounds',"$MaxRounds",'--interval',"$IntervalSec",'--stable',"$StableSec",'--max-runs',"$MaxRunsPerDay",'--timeout',"$($TurnTimeoutMin*60)")
if ($DryRun) { $engineArgs += '--dry-run' }
foreach ($pair in @(@('--codex-model',$CodexModel),@('--claude-model',$ClaudeModel),@('--codex-exe',$CodexExe),@('--claude-exe',$ClaudeExe),@('--runtime',$RuntimeHome))) {
    if ($pair[1]) { $engineArgs += $pair }
}
Invoke-HandoffEngine -EngineArgs $engineArgs -PythonExe $PythonExe