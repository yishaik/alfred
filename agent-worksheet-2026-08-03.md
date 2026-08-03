# Agent worksheet — 2026-08-03

## Goal
Create a one-tap Telegram Managed Bot dedicated to Second Brain.

## Plan
- Add the Managed Bot request/creation handlers and secure provisioning.
- Provision a separate bridge instance, state directory and systemd service.
- Add deterministic Second Brain and here.now maintenance tools.
- Compile, test, install the dormant unit, and commit once.

## Baseline
`selftest.py` has six pre-existing failures: three stale bridge-tool assertions,
the singleton lock (the live bridge owns it), and two peer-protocol assertions.

## Deviations
- The live Alfred process is not restarted; per repository policy, the new
  startup invite will be delivered by the orchestrator after deployment.

## Verification
- All changed Python modules compile.
- Managed-button type and Bot API method signatures verified against PTB 22.8.
- `selftest.py`: same six baseline failures; no new failures.
- systemd unit installed and verified, intentionally not started before a token exists.
