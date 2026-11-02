# Secretbox passkey PR — 2026-11-02

## Scope

User explicitly requested push and a PR for the implemented passkey feature.
Use an isolated branch from current origin/master, porting the committed
Secretbox implementation at 2ece54a. The deployed branch includes unrelated
unpublished changes; do not push that branch wholesale or modify live services.

The upstream Secretbox predates the deployed UI/storage refactor. This port
therefore includes its Secretbox-only prerequisites: grouped key editor,
anti-clobber storage/rotation, encrypted-vault helpers and legacy reveal denial.
They are visible in the diff, not separate changes to the bridge, backup tooling,
model routing or other applications. No live credentials/state are included.

## Feature

A fresh WebAuthn assertion with user presence and verification is required for
each reveal/delete action. Challenges are single-use and bound to exact refs.
Registration requires an 8-digit ops notification code (10 minutes, five tries,
one use). Page-password display uses the same assertion gate. Old GET/form
reveal remains denied. Browser overlay clears displayed values after 60 seconds.

## Validation and portability

191 pytest tests pass, including the synthetic passkey tests. All 308 selftest
checks pass with an isolated singleton port, temporary state and absent env file.
Python source compiles and git diff --check passes. Explicit cryptography runtime
dependency added. POSIX mode-bit assertion runs only on POSIX, retaining store
content/atomic-write checks on Windows. Windows ACL protection is not validated.
Tests use synthetic authenticators and inert controllers; physical phone
registration/reveal is not established by these tests.

## Limits

The feature uses the existing custom CBOR/WebAuthn verifier with cryptography,
not the unimplemented py_webauthn/SQLite session-login design in a separate
scratch worktree. This PR gates raw reveal, not every page/editor request.
Same-UID code/storage tampering and a privileged broker remain open. Native
approval parity, child environment, durable delivery/reconciliation are outside
scope. No service restart, live notification or deployment in this PR task.

## CI follow-up

Windows CI exposed locale-dependent decoding in the controller test fixture.
Read Python source explicitly as UTF-8 rather than Windows cp1252. This changes
only test loading; runtime source and the live checkout remain untouched.
