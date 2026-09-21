param([string]$PythonExe = '', [string]$RuntimeHome = '', [switch]$Resume, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'handoff_common.ps1')
$mode = if ($Resume) { 'resume' } else { 'status' }
$engineArgs = @($mode)
if ($RuntimeHome) { $engineArgs += @('--runtime',$RuntimeHome) }
if ($DryRun) { $engineArgs += '--dry-run' }
Invoke-HandoffEngine -EngineArgs $engineArgs -PythonExe $PythonExe