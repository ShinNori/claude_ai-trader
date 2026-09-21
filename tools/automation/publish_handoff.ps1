param([Parameter(Mandatory=$true)][ValidateSet('codex','claude')][string]$Agent,
      [string]$PythonExe = '', [ValidateSet('auto','dev')][string]$Channel = 'auto', [switch]$DryRun)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'handoff_common.ps1')
$engineArgs = @('publish','--channel',$Channel,'--agent',$Agent)
if ($DryRun) { $engineArgs += '--dry-run' }
Invoke-HandoffEngine -EngineArgs $engineArgs -PythonExe $PythonExe