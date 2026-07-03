# One-shot clean restart of the bridge: kill any tangled supervisor/bridge tree
# and relaunch exactly one instance via the canonical launcher. Self-logs to
# restart.log. With -Tidy it also prunes orphan session ids from state (pure
# PowerShell, no node). Health check: exactly one owner of the lock port 49517
# - the "duplicate tree" collapse_dupes.ps1 chased was a misdiagnosis (the uv
# wrapper chain is normal), so it and tidy_restart.ps1 were retired.
param([switch]$Tidy)

$ErrorActionPreference = 'SilentlyContinue'
$root = 'D:\Projects\telegram-claude-bridge'
$log  = Join-Path $root 'restart.log'
function L($m) { "$(Get-Date -Format o)  $m" | Out-File -FilePath $log -Append -Encoding utf8 }

# Tell the bridge, once it comes back, that this was a deliberate restart so it
# posts the "back online" notice (B1). Written before we kill anything.
try { New-Item -ItemType File -Path (Join-Path $root 'state\.restart-pending') -Force | Out-Null } catch {}

Start-Sleep -Seconds 12          # let the triggering reply flush to Telegram first
L ('=== restart begin' + $(if ($Tidy) { ' (tidy)' } else { '' }) + ' ===')

# 1. kill every python supervisor/bridge process (both .venv and uv copies)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'supervisor\.py|bridge\.py' } |
  ForEach-Object { L ("kill py  {0}  {1}" -f $_.ProcessId, $_.CommandLine); Stop-Process -Id $_.ProcessId -Force }

# 2. kill the outer start_bridge.bat/.vbs wrappers so they don't relaunch mid-cleanup
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'start_bridge\.(bat|vbs)' } |
  ForEach-Object { L ("kill wrap {0}" -f $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }

Start-Sleep -Seconds 3

# 2b. tidy: prune orphan session ids now that nothing is writing sessions.json.
# Keep main@p, main@t0, and <agent>@t<tid> for every current topic binding.
if ($Tidy) {
  try {
    $sf = Join-Path $root 'state\sessions.json'
    $tf = Join-Path $root 'state\topics.json'
    $sessions = Get-Content $sf -Raw | ConvertFrom-Json
    $topics   = if (Test-Path $tf) { Get-Content $tf -Raw | ConvertFrom-Json } else { $null }
    $keep = [System.Collections.Generic.HashSet[string]]::new()
    [void]$keep.Add('main@p'); [void]$keep.Add('main@t0')
    if ($topics) { foreach ($p in $topics.PSObject.Properties) { [void]$keep.Add(($p.Value + '@t' + $p.Name)) } }
    $out = [ordered]@{}; $dropped = @()
    foreach ($p in $sessions.PSObject.Properties) {
      if ($keep.Contains($p.Name)) { $out[$p.Name] = $p.Value } else { $dropped += $p.Name }
    }
    ($out | ConvertTo-Json -Depth 5) | Out-File -FilePath $sf -Encoding utf8
    L ("tidy pruned sessions.json - kept {0} dropped {1}" -f $out.Count, ($dropped -join ','))
  } catch { L ("tidy prune failed: {0}" -f $_) }
}

# 3. relaunch exactly one clean tree via the silent launcher
L 'launching start_bridge.vbs'
Start-Process wscript.exe -ArgumentList "`"$root\start_bridge.vbs`""

# 4. health check: exactly one process should own the lock port 49517
Start-Sleep -Seconds 10
$owner = (Get-NetTCPConnection -LocalPort 49517 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count
$after = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'supervisor\.py|bridge\.py' } |
  ForEach-Object { "  {0}  {1}" -f $_.ProcessId, $_.CommandLine }
L ("lock-port 49517 listeners: $owner (want 1)")
L ("after restart:`n" + ($after -join "`n"))
L '=== restart end ==='
