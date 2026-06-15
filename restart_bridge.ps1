# One-shot clean restart: kill any tangled supervisor/bridge tree and relaunch a
# single instance via the canonical launcher. Self-logs to restart.log.
$ErrorActionPreference = 'SilentlyContinue'
$root = 'D:\Projects\telegram-claude-bridge'
$log  = Join-Path $root 'restart.log'
function L($m) { "$(Get-Date -Format o)  $m" | Out-File -FilePath $log -Append -Encoding utf8 }

Start-Sleep -Seconds 12          # let the triggering reply flush to Telegram first
L '=== restart begin ==='

# 1. kill every python supervisor/bridge process (both .venv and uv copies)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'supervisor\.py|bridge\.py' } |
  ForEach-Object { L ("kill py  {0}  {1}" -f $_.ProcessId, $_.CommandLine); Stop-Process -Id $_.ProcessId -Force }

# 2. kill the outer start_bridge.bat loop(s) so they don't relaunch mid-cleanup
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
  Where-Object { $_.CommandLine -match 'start_bridge\.bat' } |
  ForEach-Object { L ("kill cmd {0}" -f $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }

Start-Sleep -Seconds 3

# 3. relaunch exactly one clean tree via the silent launcher
L 'launching start_bridge.vbs'
Start-Process wscript.exe -ArgumentList "`"$root\start_bridge.vbs`""

# 4. record the resulting process tree
Start-Sleep -Seconds 10
$after = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'supervisor\.py|bridge\.py' } |
  ForEach-Object { "  {0}  {1}" -f $_.ProcessId, $_.CommandLine }
L ("after restart:`n" + ($after -join "`n"))
L '=== restart end ==='
