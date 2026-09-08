# tools/register_watch_task.ps1 — Codex 側の見張りをタスクスケジューラに登録（ログオン時に自動起動・非表示）
#   登録:   powershell -ExecutionPolicy Bypass -File tools\register_watch_task.ps1
#   解除:   powershell -ExecutionPolicy Bypass -File tools\register_watch_task.ps1 -Unregister
#   確認:   Get-ScheduledTask -TaskName "ai-trader watch codex"
param([switch]$Unregister, [string]$Agent = "codex")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$name = "ai-trader watch $Agent"
if ($Unregister) {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "解除しました: $name"; exit 0
}
$script = Join-Path $root "tools\watch_handoff.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Agent $Agent" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -StartWhenAvailable
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "登録して起動しました: $name（ログ: logs\watch\）"
