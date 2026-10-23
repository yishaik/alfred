#!/usr/bin/env python3
"""Disaster-recovery drill for the Secretbox vault — run it OFF the box.

Proves that the two independently held artifacts are enough to get the vault
back: a secretbackup bundle (age, backup identity) carries vault.enc, and the
key escrow (age, a DIFFERENT identity — see keyescrow.py) carries master.key.

Nothing decrypted touches disk. Every age call's output is captured into this
process's memory; only vault.enc is read out of the bundle (the other members
are plaintext config and are never extracted); the vault is decrypted in
memory and only its entry count and a few hashes are printed. No value, no
key, no key name.

    secretdrill.py --bundle secrets-YYYYMMDD.age --bundle-identity backup-id.txt \\
                   --escrow secretbox-master.key.age --escrow-identity escrow-id.txt \\
                   --expect 115

Without the backup identity at hand, --vault-enc FILE drills the escrow half
alone against a copy of the ciphertext.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

_VAULT_MAGIC = b"SBX1"
_VAULT_AAD = b"secretbox-vault-v1"


def _h(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()[:12]


def age_decrypt(path: Path, identity: Path) -> bytes:
    r = subprocess.run(["age", "-d", "-i", str(identity), str(path)], capture_output=True)
    if r.returncode != 0:
        sys.exit(f"drill: age could not decrypt {path.name} with {identity.name}: "
                 f"{r.stderr.decode(errors='replace').strip()[:200]}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--bundle", type=Path)
    src.add_argument("--vault-enc", type=Path)
    ap.add_argument("--bundle-identity", type=Path)
    ap.add_argument("--escrow", type=Path, required=True)
    ap.add_argument("--escrow-identity", type=Path, required=True)
    ap.add_argument("--expect", type=int)
    a = ap.parse_args()

    if a.bundle:
        if not a.bundle_identity:
            ap.error("--bundle needs --bundle-identity")
        with tarfile.open(fileobj=io.BytesIO(age_decrypt(a.bundle, a.bundle_identity))) as tar:
            names = tar.getnames()
            if "vault.enc" not in names:
                sys.exit(f"drill: FAIL — bundle has no vault.enc (members: {', '.join(names)})")
            blob = tar.extractfile("vault.enc").read()
        print(f"bundle {a.bundle.name}: opened with backup identity; members: {', '.join(names)}")
    else:
        blob = a.vault_enc.read_bytes()
        print(f"vault.enc {a.vault_enc.name}: ciphertext copy (bundle layer NOT exercised)")
    print(f"vault.enc {len(blob)} bytes {_h(blob)}")

    key = age_decrypt(a.escrow, a.escrow_identity)
    if len(key) != 32:
        sys.exit(f"drill: FAIL — escrow decrypts to {len(key)} bytes, expected a 32-byte key")
    print(f"escrow {a.escrow.name}: opened with escrow identity; key {_h(key)}")

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if blob[:4] != _VAULT_MAGIC:
        sys.exit("drill: FAIL — vault.enc has no SBX1 header")
    try:
        vault = json.loads(AESGCM(key).decrypt(blob[4:16], blob[16:], _VAULT_AAD))
    except Exception:
        sys.exit("drill: FAIL — escrowed key does not open this vault")
    projects = vault.get("projects", {})
    n = sum(len([e for e in p.get("entries", {}).values() if "value" in e])
            for p in projects.values())
    del vault, key
    print(f"vault opened: {n} entries in {len(projects)} projects")
    if a.expect is not None and n != a.expect:
        print(f"drill: FAIL — expected {a.expect} entries")
        return 1
    print("drill: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
