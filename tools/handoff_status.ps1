param([string]$PythonExe = '', [string]$RuntimeHome = '', [switch]$Resume)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'handoff_common.ps1')
$mode = if ($Resume) { 'resume' } else { 'status' }
$engineArgs = @($mode)
if ($RuntimeHome) { $engineArgs += @('--runtime',$RuntimeHome) }
Invoke-HandoffEngine -EngineArgs $engineArgs -PythonExe $PythonExe