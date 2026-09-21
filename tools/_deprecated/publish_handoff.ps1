param([Parameter(Mandatory=$true)][ValidateSet('codex','claude')][string]$Agent,
      [string]$PythonExe = '', [switch]$DryRun)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'handoff_common.ps1')
$engineArgs = @('publish','--agent',$Agent)
if ($DryRun) { $engineArgs += '--dry-run' }
Invoke-HandoffEngine -EngineArgs $engineArgs -PythonExe $PythonExe