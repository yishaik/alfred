#!/usr/bin/env python3
"""Escrow the Secretbox master key — separately from the vault it opens.

secretbackup.py ships vault.enc off-box but deliberately never the key: one
leaked age identity must not be enough to read the vault. This is the other
half. It age-encrypts ~/.secretbox/master.key to a DIFFERENT recipient (its
private half held by the owner somewhere else — password manager, hardware
key, paper) and writes it to a path OUTSIDE the backup repo and this repo.

The key file is only ever opened read-only by age itself; its bytes never pass
through argv, stdout, a log, or git. What is printed is a short SHA-256 prefix
of the raw key, so the owner can confirm off-box that the escrow decrypts to
the key this box actually uses:

    keyescrow.py --out /some/where/secretbox-master.key.age
    # then, OFF the box:
    age -d -i escrow-identity.txt secretbox-master.key.age | sha256sum | cut -c1-12

Setup (once, off-box):  age-keygen -o escrow-identity.txt, and put its public
line (age1...) into ~/alfred/keyescrow.recipient on the box.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ALFRED = Path("/home/ubuntu/alfred")
BACKUP_REPO = Path("/home/ubuntu/alfred-state-backup")
KEYFILE = Path(os.environ.get("SECRETBOX_KEYFILE", "/home/ubuntu/.secretbox/master.key"))
ESCROW_RECIPIENT_FILE = Path(os.environ.get("KEYESCROW_RECIPIENT", str(ALFRED / "keyescrow.recipient")))
BACKUP_RECIPIENT_FILE = Path(os.environ.get("SECRETBACKUP_RECIPIENT", str(ALFRED / "secretbackup.recipient")))
FORBIDDEN_DIRS = [Path(p) for p in os.environ.get(
    "KEYESCROW_FORBIDDEN", f"{BACKUP_REPO}:{ALFRED}").split(":") if p]


def _recipient(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text if text.startswith("age1") else ""


def key_fingerprint() -> str:
    """sha256 prefix of the raw key bytes — enough to match, useless to an attacker."""
    return "sha256:" + hashlib.sha256(KEYFILE.read_bytes()).hexdigest()[:12]


def escrow(out: Path) -> int:
    rcpt = _recipient(ESCROW_RECIPIENT_FILE)
    if not rcpt:
        print(f"keyescrow: no age recipient in {ESCROW_RECIPIENT_FILE}. Create an identity "
              f"OFF this box (age-keygen) and put its age1... line there.", file=sys.stderr)
        return 2
    if rcpt == _recipient(BACKUP_RECIPIENT_FILE):
        print("keyescrow: the escrow recipient is the same as the backup recipient — one "
              "identity would then open both vault and key. Use a separate identity.",
              file=sys.stderr)
        return 2
    out = out.expanduser().resolve()
    for d in FORBIDDEN_DIRS:
        try:
            out.relative_to(d.resolve())
        except ValueError:
            continue
        print(f"keyescrow: refusing to write inside {d} — the key must not live beside the "
              f"vault backups or in a git tree.", file=sys.stderr)
        return 2
    if out.exists():
        print(f"keyescrow: {out} exists; not overwriting.", file=sys.stderr)
        return 2
    try:
        if len(KEYFILE.read_bytes()) != 32:
            print(f"keyescrow: {KEYFILE} is not a 32-byte key; not escrowing.", file=sys.stderr)
            return 2
    except OSError as e:
        print(f"keyescrow: cannot read {KEYFILE}: {type(e).__name__}", file=sys.stderr)
        return 2

    os.umask(0o077)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".partial")
    r = subprocess.run(["age", "-r", rcpt, "-o", str(tmp), str(KEYFILE)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        print(f"keyescrow: age failed: {r.stderr.strip()}", file=sys.stderr)
        return 1
    tmp.chmod(0o600)
    os.replace(tmp, out)
    print(f"escrowed {KEYFILE} -> {out} (age, escrow recipient) · key {key_fingerprint()}")
    print("Move that file OFF this box, then verify there with:")
    print(f"  age -d -i <escrow-identity> {out.name} | sha256sum | cut -c1-12")
    print("It must print the 12 hex chars above. Then delete the copy on this box.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True,
                    help="where to write the encrypted key (outside the backup repo and ~/alfred)")
    return escrow(ap.parse_args().out)


if __name__ == "__main__":
    sys.exit(main())
