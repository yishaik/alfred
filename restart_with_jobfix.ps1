# One-shot: stop the bridge, repair jobs.json while nothing holds it in memory,
# then relaunch. The running scheduler keeps jobs in RAM and rewrites the file on
# save, so patching it while the bridge is up gets clobbered — the repair has to
# happen in the window between kill and start.
$ErrorActionPreference = 'SilentlyContinue'
$root = 'D:\Projects\telegram-claude-bridge'
$log  = Join-Path $root 'restart.log'
function L($m) { "$(Get-Date -Format o)  [jobfix] $m" | Out-File -FilePath $log -Append -Encoding utf8 }

L "=== restart begin (with job repair) ==="

# 1) stop everything (same targets restart.ps1 uses)
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'supervisor\.py|bridge\.py' } |
  ForEach-Object { L "kill pid $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force }
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'start_bridge\.(bat|vbs)' } |
  ForEach-Object { L "kill wrapper pid $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 4

# 2) repair jobs.json now that no process owns it
& "$root\.venv\Scripts\python.exe" -c @"
import json, pathlib
root = pathlib.Path(r'$root')
jp = root / 'state' / 'jobs.json'
d = json.loads(jp.read_text(encoding='utf-8'))
jobs = d['jobs'] if isinstance(d, dict) and 'jobs' in d else d
seq = jobs if isinstance(jobs, list) else list(jobs.values())
agents = set(json.loads((root/'state'/'agents.json').read_text(encoding='utf-8'))['agents'])
topics = set(json.loads((root/'state'/'topics.json').read_text(encoding='utf-8')))
n = 0
for j in seq:
    if j.get('agent') and j['agent'] not in agents:
        j['agent'] = 'main'; n += 1
    t = j.get('thread_id')
    if t is not None and str(t) not in topics:
        j['thread_id'] = None; n += 1
jp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
print(f'repaired {n} field(s)')
"@ 2>&1 | ForEach-Object { L $_ }

# 3) relaunch through the canonical launcher
L "relaunching"
Start-Process wscript.exe -ArgumentList "`"$root\start_bridge.vbs`"" -WindowStyle Hidden
Start-Sleep -Seconds 20
$listeners = (Get-NetTCPConnection -LocalPort 49517 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count
L "port 49517 listeners = $listeners (expect 1)"
L "=== restart end ==="
