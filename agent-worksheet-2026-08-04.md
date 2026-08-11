# Agent worksheet — 2026-08-04

## Plan

- Scope the timezone change to Alfred, leaving the host on UTC.
- Apply `TZ=Asia/Jerusalem` to every Alfred bridge service.
- Reload systemd configuration without restarting any live bridge.
- Verify the effective service environment.

## Result

- Added the shared `systemd/timezone.conf` drop-in source.
- Installed it for `alfred`, `alfred-tlvquest`, `alfred-storycut`, and
  `alfred-secondbrain`.
- `systemctl show` reports `TZ=Asia/Jerusalem` for all four services.
- No service was restarted; the setting takes effect on each service's next
  start, preserving the active conversation.

## Verification

- Baseline `selftest.py`: 6 existing failures (bridge-tool exposure, singleton
  lock while the live bridge owns it, and peer-bus assertions). No Python code
  was changed by this task.

## Deviations

- The Linux host differs from the Windows-oriented repository instructions, so
  the equivalent `.venv/bin/python` command was used for the baseline selftest.
