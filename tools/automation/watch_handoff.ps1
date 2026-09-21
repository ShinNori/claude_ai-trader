param(
 [ValidateSet('codex','claude')][string]$Agent='codex',
 [ValidateRange(1,86400)][int]$IntervalSec=60,
 [ValidateRange(0,86400)][int]$StableSec=60,
 [ValidateRange(1,1000)][int]$MaxRunsPerDay=10,
 [ValidateRange(1,1440)][int]$TurnTimeoutMin=45,
 [string]$CodexModel='', [string]$ClaudeModel='',
 [string]$CodexExe='', [string]$ClaudeExe='',
 [string]$RuntimeHome='', [string]$PythonExe='',
 [string]$Channel='auto,dev',
 [switch]$Once, [switch]$DryRun, [switch]$NoGit, [switch]$NoStatusFile
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'handoff_common.ps1')
$engineArgs=@('watch','--channel',$Channel,'--agent',$Agent,'--interval',"$IntervalSec",'--stable',"$StableSec",'--max-runs',"$MaxRunsPerDay",'--timeout',"$($TurnTimeoutMin*60)")
if ($Once) { $engineArgs+='--once' }
if ($DryRun) { $engineArgs+='--dry-run' }
if ($NoStatusFile) { $engineArgs+='--no-status-file' }
foreach ($pair in @(@('--codex-model',$CodexModel),@('--claude-model',$ClaudeModel),@('--codex-exe',$CodexExe),@('--claude-exe',$ClaudeExe),@('--runtime',$RuntimeHome))) {
 if ($pair[1]) { $engineArgs+=$pair }
}
Invoke-HandoffEngine -EngineArgs $engineArgs -PythonExe $PythonExe
