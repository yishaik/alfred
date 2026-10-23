# Bridge watchdog. Runs every minute from the ClaudeBridgeWatchdog scheduled
# task. Relaunches the bridge ONLY when nothing is running, so it can never
# fight the boot task (ClaudeTelegramBridge) or a hand-started tree - a second
# live instance would lose the token tug-of-war and exit rc=75.
#
# Why this exists: on 2026-08-12 the whole tree (cmd -> supervisor -> bridge)
# was killed at 01:11 and stayed dead for 21h. supervisor.py only covers
# bridge.py dying, start_bridge.bat only covers supervisor dying, and
# restart.ps1 runs *inside* the tree it kills - so it dies at its own step 1,
# before it can relaunch. Nothing outside the tree was watching. Now something
# is.
$ErrorActionPreference = 'SilentlyContinue'
$root = 'D:\Projects\telegram-claude-bridge'
$log  = Join-Path $root 'watchdog.log'

function L($m) { "$(Get-Date -Format o)  $m" | Out-File -FilePath $log -Append -Encoding utf8 }

# A bridge is "up" if any python is running supervisor.py or bridge.py. Match on
# the command line rather than a pid file so a hand-started tree counts too.
function Get-BridgeProcs {
  @(Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'supervisor\.py|bridge\.py' })
}
$alive = Get-BridgeProcs

# A deliberate restart (restart.ps1) only drops a flag and returns - it must not
# kill the tree it is running inside. We do the kill here instead, because this
# task is spawned by the Task Scheduler service and so survives it.
$req = Join-Path $root 'state\.restart-requested'
if (Test-Path $req) {
  L 'restart requested - killing the tree'
  Remove-Item $req -Force
  foreach ($p in $alive) { L ("kill py  {0}" -f $p.ProcessId); Stop-Process -Id $p.ProcessId -Force }
  Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'start_bridge\.(bat|vbs)' } |
    ForEach-Object { L ("kill wrap {0}" -f $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }
  Start-Sleep -Seconds 3
  $alive = Get-BridgeProcs                # fall through to the relaunch below
}

if ($alive.Count -gt 0) { exit 0 }      # healthy: stay silent, no log spam

# Trim the log before writing, so a long outage can't grow it without bound.
try {
  if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) {
    (Get-Content $log -Tail 200) | Out-File -FilePath $log -Encoding utf8
  }
} catch {}

L 'bridge is DOWN - relaunching via start_bridge.vbs'
Start-Process wscript.exe -ArgumentList "`"$root\start_bridge.vbs`""

Start-Sleep -Seconds 20
$after = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
           Where-Object { $_.CommandLine -match 'supervisor\.py|bridge\.py' } |
           ForEach-Object { "  {0}  {1}" -f $_.ProcessId, $_.CommandLine })
if ($after.Count -gt 0) { L ("relaunched ok:`n" + ($after -join "`n")) }
else { L 'relaunch FAILED - no supervisor/bridge process after 20s' }
