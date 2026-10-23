#!/usr/bin/env python3
"""Encrypted off-box backup of everything the box cannot re-derive.

The daily state backup deliberately excludes secrets, because it is mirrored to
GitHub. That was the right call for the zip and the wrong outcome overall: it
left vault.json — the one file on this box with no other copy anywhere — as the
only thing not backed up. On 2026-08-05 a bad write erased it and there was
nothing to restore from.

So: same offsite repo, but the payload is age-encrypted first. The private key
lives with the owner, not on this box, which means a GitHub compromise yields a
blob and a box compromise yields nothing new.

    secretbackup.py            # write today's bundle into the backup repo
    secretbackup.py --out P    # write it somewhere else instead (no commit)
    secretbackup.py --check    # build + verify the bundle in a temp dir, write nothing

The vault is the one source that is REQUIRED. Since the 2026-09-24 migration it
is vault.enc (AES-GCM, see secretbox.py), and the bundle carries the ciphertext
exactly as it sits on disk. The master key that opens it is deliberately NOT in
this bundle — whoever holds the age key for these bundles must not also hold the
vault key. It is escrowed separately by keyescrow.py, to a different recipient.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ALFRED = Path("/home/ubuntu/alfred")
BACKUP_REPO = Path("/home/ubuntu/alfred-state-backup")
RECIPIENT_FILE = ALFRED / "secretbackup.recipient"

# Mirrors secretbox.py (same env overrides, same on-disk format). Duplicated
# rather than imported so a broken web service cannot also break the backup;
# tests/test_secretbackup.py asserts the two stay identical.
VAULT_ENC = Path(os.environ.get("SECRETBOX_VAULT_ENC", str(ALFRED / "vault.enc")))
KEYFILE = Path(os.environ.get("SECRETBOX_KEYFILE", "/home/ubuntu/.secretbox/master.key"))
_VAULT_MAGIC = b"SBX1"
_VAULT_AAD = b"secretbox-vault-v1"
VAULT_ARCNAME = "vault.enc"

#: (path on disk, name inside the archive). Anything here is unobtainable by any
#: other means — not derivable, not in a repo, not re-fetchable without the
#: owner going and re-issuing it by hand.
#: The vault is not listed here: it is required, verified, and added by
#: build_tar() itself. A missing optional source is skipped with a note.
SOURCES: list[tuple[Path, str]] = [
    (ALFRED / ".env", "alfred.env"),
    (Path("/etc/alfred/alfred.env"), "alfredos.env"),
    (ALFRED / "pages.json", "pages.json"),
    (Path.home() / ".oci" / "config", "oci/config"),
    (Path.home() / ".oci" / "oci_api_key.pem", "oci/oci_api_key.pem"),
]

README = """Alfred secret backup
====================

Decrypt with the private key you were given (keep it in a password manager,
NOT next to this file):

    age -d -i alfred-secrets.key secrets-YYYYMMDD.age | tar x

Contents
  vault.enc      Secretbox vault, still AES-GCM encrypted. Opening it needs
                 ~/.secretbox/master.key, which is NOT in this bundle — recover
                 it from the separate key escrow (keyescrow.py, own age key).
  alfred.env     alfred runtime secrets: bot tokens, peer tokens, model keys
  alfredos.env   /etc/alfred/alfred.env — Alfred Operator OS
  pages.json     hand-written entries of the tailnet pages list
  oci/           OCI CLI auth

To rebuild the box: clone the alfred and alfredos repos, restore these files to
the paths above (vault.enc -> ~/alfred/vault.enc, escrowed key ->
~/.secretbox/master.key, 0600 in a 0700 dir), install the systemd units, done.
"""


def read_recipient() -> str:
    if not RECIPIENT_FILE.is_file():
        sys.exit(
            f"{RECIPIENT_FILE} is missing. Create a keypair with `age-keygen`, "
            f"keep the private half OFF this box, and write the public half "
            f"(age1...) into that file."
        )
    text = RECIPIENT_FILE.read_text(encoding="utf-8").strip()
    if not text.startswith("age1"):
        sys.exit(f"{RECIPIENT_FILE} does not contain an age recipient")
    return text


class VaultError(RuntimeError):
    """The vault cannot be backed up. Always fatal — never a skip."""


def _count_entries(vault: dict) -> int:
    return sum(len([e for e in p.get("entries", {}).values() if "value" in e])
               for p in vault.get("projects", {}).values())


def verify_vault_blob(blob: bytes, key: bytes | None = None) -> int:
    """Prove a vault.enc blob is whole: right header, AEAD tag checks out under
    the master key, decrypts to a vault with at least one entry. Returns the
    entry count. Raises VaultError naming WHAT failed — never a value. The
    plaintext exists only inside this function and is dropped on return."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < 4 + 12 + 16 or blob[:4] != _VAULT_MAGIC:
        raise VaultError(f"{VAULT_ARCNAME} has no SBX1 header ({len(blob)} bytes) — not a secretbox vault")
    if key is None:
        try:
            key = KEYFILE.read_bytes()
        except OSError as e:
            raise VaultError(f"cannot read the master key to verify the vault: {type(e).__name__}") from None
    if len(key) != 32:
        raise VaultError(f"master key is {len(key)} bytes, expected 32")
    try:
        plain = AESGCM(key).decrypt(blob[4:16], blob[16:], _VAULT_AAD)
    except Exception:
        raise VaultError(f"{VAULT_ARCNAME} failed authenticated decryption — corrupt, or not this master key") from None
    try:
        n = _count_entries(json.loads(plain.decode("utf-8")))
    except Exception:
        raise VaultError(f"{VAULT_ARCNAME} decrypts but is not a valid vault document") from None
    finally:
        del plain
    if n == 0:
        raise VaultError(f"{VAULT_ARCNAME} decrypts to an EMPTY vault — refusing to back up nothing over something")
    return n


def read_vault() -> tuple[bytes, int]:
    """Read vault.enc ONCE and verify those exact bytes, so what is checked is
    what is archived (no window for a write between check and tar)."""
    try:
        blob = VAULT_ENC.read_bytes()
    except FileNotFoundError:
        raise VaultError(f"{VAULT_ENC} is missing — the vault would not be in this backup") from None
    except OSError as e:
        raise VaultError(f"{VAULT_ENC} is unreadable: {type(e).__name__}") from None
    return blob, verify_vault_blob(blob)


def _guard_no_master_key() -> None:
    """The master key must never share an artifact with the vault it opens."""
    try:
        kf = KEYFILE.resolve()
    except OSError:
        return
    for path, _ in SOURCES:
        try:
            same = path.resolve() == kf or (path.exists() and kf.exists() and path.samefile(kf))
        except OSError:
            same = False
        if same:
            raise VaultError(f"{path} is the vault master key — it must not be in this bundle")


def build_tar(dest: Path) -> tuple[list[str], int]:
    """Returns (included names, vault entry count). Raises VaultError before
    anything is written if the vault cannot be read and verified."""
    _guard_no_master_key()
    vault_blob, n_entries = read_vault()
    included: list[str] = []
    import io
    with tarfile.open(dest, "w") as tar:
        info = tarfile.TarInfo(VAULT_ARCNAME)
        info.size = len(vault_blob)
        info.mode = 0o600
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(vault_blob))
        included.append(VAULT_ARCNAME)
        for path, name in SOURCES:
            # /etc/alfred is root-only, so even the existence check raises for
            # an unprivileged run — "cannot look" is not "not there".
            try:
                present = path.is_file()
            except PermissionError:
                present = subprocess.run(["sudo", "test", "-f", str(path)]).returncode == 0
            # A missing OPTIONAL source is worth reporting but must not abort
            # the run. (The vault is not optional — see read_vault.)
            if not present:
                print(f"  skip {name} (not present)", file=sys.stderr)
                continue
            try:
                tar.add(path, arcname=name)
            except PermissionError:
                # /etc/alfred/alfred.env is root-owned 0600 by design.
                data = subprocess.run(["sudo", "cat", str(path)],
                                      capture_output=True, check=True).stdout
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(data))
            included.append(name)
        info = tarfile.TarInfo("README.txt")
        blob = README.encode()
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
    return included, n_entries


def verify_tar(raw: Path, expect_entries: int) -> None:
    """Restore-side check on the archive itself: the vault read back OUT of the
    tar must still decrypt to the same number of entries, and the master key
    must not be in it."""
    with tarfile.open(raw) as tar:
        names = tar.getnames()
        if VAULT_ARCNAME not in names:
            raise VaultError("bundle was built without vault.enc")
        try:
            key_bytes = KEYFILE.read_bytes()
        except OSError:
            key_bytes = None
        for m in tar.getmembers():
            if m.isfile() and key_bytes is not None and m.size == len(key_bytes):
                if tar.extractfile(m).read() == key_bytes:
                    raise VaultError(f"bundle member {m.name} is the master key — refusing")
        n = verify_vault_blob(tar.extractfile(VAULT_ARCNAME).read(), key_bytes)
    if n != expect_entries:
        raise VaultError(f"vault in bundle has {n} entries, on disk {expect_entries}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, help="write here instead of the backup repo")
    ap.add_argument("--check", action="store_true",
                    help="build and verify the bundle in a temp dir; encrypt and commit nothing")
    args = ap.parse_args()

    # Any VaultError is fatal: non-zero exit, so systemd marks the unit failed
    # and OnFailure= announces it on the ops bot. Crucially this happens BEFORE
    # the prune below, so a run of failed days can never rotate the last good
    # vault-bearing bundles out of the repo.
    try:
        return _run(args)
    except VaultError as e:
        print(f"SECRET BACKUP FAILED: {e}", file=sys.stderr)
        return 1


def _run(args) -> int:
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "bundle.tar"
            included, n = build_tar(raw)
            verify_tar(raw, n)
        print(f"check ok — vault.enc verified ({n} entries, round-trips out of the "
              f"archive); {len(included)} file(s): {', '.join(included)}; nothing written")
        return 0

    recipient = read_recipient()
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    target = args.out or (BACKUP_REPO / f"secrets-{stamp}.age")

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "bundle.tar"
        included, n_entries = build_tar(raw)
        verify_tar(raw, n_entries)

        os.umask(0o077)
        enc = subprocess.run(["age", "-r", recipient, "-o", str(target), str(raw)],
                             capture_output=True, text=True)
        if enc.returncode != 0:
            sys.exit(f"age failed: {enc.stderr.strip()}")

    target.chmod(0o600)
    size = target.stat().st_size
    print(f"wrote {target} ({size} bytes) — {len(included)} file(s): {', '.join(included)}"
          f" · vault {n_entries} entries verified")

    if args.out:
        return 0

    # Keep one bundle per day and never more than 14, so the repo does not grow
    # without bound.
    bundles = sorted(BACKUP_REPO.glob("secrets-*.age"))
    for old in bundles[:-14]:
        old.unlink()
        subprocess.run(["git", "-C", str(BACKUP_REPO), "rm", "--cached", "-q", old.name],
                       capture_output=True)

    subprocess.run(["git", "-C", str(BACKUP_REPO), "add", "-A", "--", "secrets-*.age"],
                   capture_output=True)
    committed = subprocess.run(
        ["git", "-C", str(BACKUP_REPO), "commit", "-q", "-m",
         f"secrets bundle {stamp}"], capture_output=True, text=True)
    if committed.returncode == 0:
        # The remote can gain commits from elsewhere (a PR, a CI change). A plain
        # push is then rejected as non-fast-forward and every later bundle stays
        # local, so rebase onto the remote first. Backup files never conflict:
        # each commit only adds or removes its own dated files.
        subprocess.run(["git", "-C", str(BACKUP_REPO), "pull", "--rebase", "-q"],
                       capture_output=True, text=True)
        push = subprocess.run(["git", "-C", str(BACKUP_REPO), "push", "-q"],
                              capture_output=True, text=True)
        print("pushed" if push.returncode == 0 else f"push failed: {push.stderr.strip()}")
    else:
        print("no change to commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
