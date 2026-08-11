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
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ALFRED = Path("/home/ubuntu/alfred")
BACKUP_REPO = Path("/home/ubuntu/alfred-state-backup")
RECIPIENT_FILE = ALFRED / "secretbackup.recipient"

#: (path on disk, name inside the archive). Anything here is unobtainable by any
#: other means — not derivable, not in a repo, not re-fetchable without the
#: owner going and re-issuing it by hand.
SOURCES: list[tuple[Path, str]] = [
    (ALFRED / "vault.json", "vault.json"),
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
  vault.json     Secretbox vault — every credential grouped by project
  alfred.env     alfred runtime secrets: bot tokens, peer tokens, model keys
  alfredos.env   /etc/alfred/alfred.env — Alfred Operator OS
  pages.json     hand-written entries of the tailnet pages list
  oci/           OCI CLI auth

To rebuild the box: clone the alfred and alfredos repos, restore these files to
the paths above, install the systemd units, done.
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


def build_tar(dest: Path) -> list[str]:
    included: list[str] = []
    with tarfile.open(dest, "w") as tar:
        for path, name in SOURCES:
            # /etc/alfred is root-only, so even the existence check raises for
            # an unprivileged run — "cannot look" is not "not there".
            try:
                present = path.is_file()
            except PermissionError:
                present = subprocess.run(["sudo", "test", "-f", str(path)]).returncode == 0
            # A missing source is worth reporting but must not abort the run:
            # a backup of five of six files beats no backup at all.
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
                import io
                tar.addfile(info, io.BytesIO(data))
            included.append(name)
        info = tarfile.TarInfo("README.txt")
        blob = README.encode()
        info.size = len(blob)
        import io
        tar.addfile(info, io.BytesIO(blob))
    return included


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, help="write here instead of the backup repo")
    args = ap.parse_args()

    recipient = read_recipient()
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    target = args.out or (BACKUP_REPO / f"secrets-{stamp}.age")

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "bundle.tar"
        included = build_tar(raw)
        if not included:
            sys.exit("nothing to back up — refusing to write an empty bundle")

        os.umask(0o077)
        enc = subprocess.run(["age", "-r", recipient, "-o", str(target), str(raw)],
                             capture_output=True, text=True)
        if enc.returncode != 0:
            sys.exit(f"age failed: {enc.stderr.strip()}")

    target.chmod(0o600)
    size = target.stat().st_size
    print(f"wrote {target} ({size} bytes) — {len(included)} file(s): {', '.join(included)}")

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
        push = subprocess.run(["git", "-C", str(BACKUP_REPO), "push", "-q"],
                              capture_output=True, text=True)
        print("pushed" if push.returncode == 0 else f"push failed: {push.stderr.strip()}")
    else:
        print("no change to commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
