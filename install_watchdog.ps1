# Registers ClaudeBridgeWatchdog: runs watchdog.ps1 every minute, which
# relaunches the bridge if (and only if) nothing is running, and services
# restart requests dropped by restart.ps1. Safe to re-run; -Force replaces.
# No elevation needed - this task runs as the interactive user.
$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:\Projects\telegram-claude-bridge\watchdog.ps1"' `
  -WorkingDirectory 'D:\Projects\telegram-claude-bridge'

$trg = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"
$trg.Delay = 'PT2M'          # let the boot task's own 1-min delay win the race
$trg.Repetition = (New-ScheduledTaskTrigger -Once -At ([datetime]'2026-01-01T00:00:00') `
  -RepetitionInterval (New-TimeSpan -Minutes 1)).Repetition

$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -Hidden

Register-ScheduledTask -TaskName 'ClaudeBridgeWatchdog' -Action $act -Trigger $trg -Settings $set `
  -Description 'Relaunches the Telegram<->Claude bridge within ~1 min if nothing is running.' -Force | Out-Null

Write-Host 'ClaudeBridgeWatchdog registered.'
Get-ScheduledTask -TaskName 'ClaudeBridgeWatchdog' | Select-Object TaskName, State
