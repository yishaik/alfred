# Registers the Telegram-Claude bridge to start at boot, WITHOUT requiring a
# login. Run this once. It self-elevates (a UAC prompt will appear) because
# creating an "at startup / run whether logged on or not" task needs admin.
#
#   Right-click this file is not enough for .ps1 - use install_autostart.bat,
#   or run:  powershell -ExecutionPolicy Bypass -File install_autostart.ps1

$ErrorActionPreference = "Stop"

# --- self-elevate ---------------------------------------------------------- #
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)
            ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Requesting administrator rights (UAC)..."
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"")
    return
}

# --- task definition ------------------------------------------------------- #
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ERROR: $py not found. Create the venv first:" -ForegroundColor Red
    Write-Host "  uv venv --python 3.13 --python-preference only-managed .venv"
    Write-Host "  uv pip install --python .venv\Scripts\python.exe -r requirements.txt"
    Read-Host "Press Enter to close"; return
}
$user = "$($env:USERDOMAIN)\$($env:USERNAME)"

$action   = New-ScheduledTaskAction -Execute $py -Argument "-u supervisor.py" -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT1M"   # let networking come up first
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "ClaudeTelegramBridge" -Action $action `
    -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Telegram-Claude bridge: starts at boot (no login required)." `
    -Force | Out-Null

# The boot task supersedes the old login Startup shortcut; disable it so the two
# don't race for the single-instance lock (the loser would retry-loop forever).
$lnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\ClaudeTelegramBridge.lnk"
if (Test-Path $lnk) {
    Rename-Item $lnk "$lnk.disabled" -Force -ErrorAction SilentlyContinue
    Write-Host "Disabled the old login Startup shortcut (boot task replaces it)."
}

Write-Host "`nDone. The bridge will now start ~1 min after every boot, even at" -ForegroundColor Green
Write-Host "the lock screen (no login needed), running as $user." -ForegroundColor Green
Write-Host "`nStart it now without rebooting:  schtasks /run /tn ClaudeTelegramBridge"
Write-Host "Remove autostart later:          schtasks /delete /tn ClaudeTelegramBridge /f"
Read-Host "`nPress Enter to close"
