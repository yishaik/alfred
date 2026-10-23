"""Secret backup: the vault must be in every bundle, or the run must fail.

Everything here runs in a temporary directory with a throwaway age identity, a
throwaway master key and canary values — the live vault, the live master key
and the backup repo are never touched. The canary is asserted absent from every
byte the backup produces or prints, so a plaintext leak fails the suite.

    python3 -m unittest tests.test_secretbackup -v
"""
import atexit
import contextlib
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="sbx-test-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
# secretbox reads these at import — point it at the sandbox BEFORE importing,
# so the fixture is written by the real production code path.
os.environ["SECRETBOX_VAULT_ENC"] = str(TMP / "vault.enc")
os.environ["SECRETBOX_VAULT"] = str(TMP / "vault.json")
os.environ["SECRETBOX_KEYFILE"] = str(TMP / "keys" / "master.key")

import secretbox        # noqa: E402
import secretbackup     # noqa: E402
import keyescrow        # noqa: E402

CANARY = "CANARY-" + secrets.token_hex(16)


def _age_identity(name: str) -> tuple[Path, str]:
    ident = TMP / f"{name}.identity"
    subprocess.run(["age-keygen", "-o", str(ident)], capture_output=True, check=True)
    pub = next(l.split(": ", 1)[1] for l in ident.read_text().splitlines()
               if l.startswith("# public key: "))
    return ident, pub


@unittest.skipUnless(shutil.which("age") and shutil.which("age-keygen"),
                     "age/age-keygen not installed (optional; CI runners lack it)")
class Sandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert str(secretbox.VAULT_ENC).startswith(str(TMP)), "refusing: secretbox not sandboxed"
        cls.ident, pub = _age_identity("backup")
        (TMP / "secretbackup.recipient").write_text(pub + "\n")
        secretbox.master_key(create=True)
        cls.key = secretbox.KEYFILE.read_bytes()
        cls.vault = {"version": 1, "projects": {
            "alpha": {"description": "", "entries": {
                "API_KEY": {"value": CANARY + "-a", "status": "unverified"},
                "OTHER": {"value": CANARY + "-b", "status": "unverified"}}},
            "beta": {"description": "", "entries": {
                "TOKEN": {"value": CANARY + "-c", "status": "unverified"}}}}}
        secretbox.save_vault(cls.vault)
        cls.pristine = secretbox.VAULT_ENC.read_bytes()
        (TMP / "extra.env").write_text(f"SOME_TOKEN={CANARY}-env\n")

    def setUp(self):
        secretbox.VAULT_ENC.write_bytes(self.pristine)
        secretbox.KEYFILE.write_bytes(self.key)
        self.patch = {
            "VAULT_ENC": secretbox.VAULT_ENC, "KEYFILE": secretbox.KEYFILE,
            "RECIPIENT_FILE": TMP / "secretbackup.recipient",
            "BACKUP_REPO": TMP / "repo",
            "SOURCES": [(TMP / "extra.env", "alfred.env"), (TMP / "absent", "pages.json")],
        }
        self._saved = {k: getattr(secretbackup, k) for k in self.patch}
        for k, v in self.patch.items():
            setattr(secretbackup, k, v)

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(secretbackup, k, v)

    def run_backup(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        old = sys.argv
        sys.argv = ["secretbackup.py", *argv]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = secretbackup.main()
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 1
        finally:
            sys.argv = old
        text = out.getvalue() + err.getvalue()
        self.assertNotIn(CANARY, text, "plaintext secret reached backup output")
        self.assertNotIn(self.key.hex(), text, "master key reached backup output")
        return code, text

    def assert_no_plaintext(self, blob: bytes):
        self.assertNotIn(CANARY.encode(), blob)
        self.assertNotIn(self.key, blob)
        self.assertNotIn(self.key.hex().encode(), blob)

    # --- the fix ---------------------------------------------------------

    def test_restore_round_trip(self):
        """Backup -> age-decrypt -> untar into a fresh dir -> vault opens with the
        (separately escrowed) key and equals the original, entry for entry."""
        bundle = TMP / "rt.age"
        bundle.unlink(missing_ok=True)
        code, text = self.run_backup("--out", str(bundle))
        self.assertEqual(code, 0, text)
        self.assertIn("vault 3 entries verified", text)
        self.assert_no_plaintext(bundle.read_bytes())

        restore = Path(tempfile.mkdtemp(dir=TMP, prefix="restore-"))
        tar_bytes = subprocess.run(["age", "-d", "-i", str(self.ident), str(bundle)],
                                   capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            names = tar.getnames()
            tar.extractall(restore, filter="data")
        self.assertIn("vault.enc", names)
        self.assertNotIn("vault.json", names)
        # master key must not be in the archive under any name
        self.assertNotIn(self.key, tar_bytes)
        # tar holds the vault as ciphertext only (the optional .env source is plaintext
        # by design — the whole tar is age-encrypted; that is what the bundle check covers)
        self.assertNotIn(CANARY.encode(), (restore / "vault.enc").read_bytes())
        self.assertEqual((restore / "vault.enc").read_bytes(), self.pristine)

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        blob = (restore / "vault.enc").read_bytes()
        restored = json.loads(AESGCM(self.key).decrypt(blob[4:16], blob[16:], b"secretbox-vault-v1"))
        self.assertEqual(restored, self.vault)

    def test_missing_vault_fails(self):
        secretbox.VAULT_ENC.unlink()
        out = TMP / "missing.age"
        code, text = self.run_backup("--out", str(out))
        self.assertEqual(code, 1)
        self.assertIn("SECRET BACKUP FAILED", text)
        self.assertIn("missing", text)
        self.assertFalse(out.exists(), "a bundle without the vault was written")

    def test_unreadable_vault_fails(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        secretbox.VAULT_ENC.chmod(0o000)
        try:
            code, text = self.run_backup("--check")
        finally:
            secretbox.VAULT_ENC.chmod(0o600)
        self.assertEqual(code, 1)
        self.assertIn("unreadable", text)

    def test_corrupt_vault_fails(self):
        b = bytearray(self.pristine)
        b[40] ^= 0x01
        secretbox.VAULT_ENC.write_bytes(bytes(b))
        code, text = self.run_backup("--check")
        self.assertEqual(code, 1)
        self.assertIn("failed authenticated decryption", text)

    def test_truncated_and_foreign_files_fail(self):
        for bad in (b"", b"SBX1", b'{"projects": {}}'):
            secretbox.VAULT_ENC.write_bytes(bad)
            code, text = self.run_backup("--check")
            self.assertEqual(code, 1, bad)

    def test_wrong_master_key_fails(self):
        secretbox.KEYFILE.write_bytes(os.urandom(32))
        code, text = self.run_backup("--check")
        self.assertEqual(code, 1)
        self.assertIn("not this master key", text)

    def test_empty_vault_fails(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        n = os.urandom(12)
        secretbox.VAULT_ENC.write_bytes(b"SBX1" + n + AESGCM(self.key).encrypt(
            n, json.dumps({"version": 1, "projects": {}}).encode(), b"secretbox-vault-v1"))
        code, text = self.run_backup("--check")
        self.assertEqual(code, 1)
        self.assertIn("EMPTY", text)

    def test_master_key_in_sources_refused(self):
        secretbackup.SOURCES = [*self.patch["SOURCES"], (secretbox.KEYFILE, "oops.key")]
        code, text = self.run_backup("--check")
        self.assertEqual(code, 1)
        self.assertIn("master key", text)

    def test_failure_never_prunes_or_commits(self):
        """14-day retention must not rotate good bundles out while the vault is broken."""
        repo = self.patch["BACKUP_REPO"]
        repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for d in range(1, 16):
            (repo / f"secrets-202609{d:02d}.age").write_bytes(b"old")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "seed"], check=True)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout
        secretbox.VAULT_ENC.unlink()
        code, _ = self.run_backup()
        self.assertEqual(code, 1)
        self.assertEqual(len(list(repo.glob("secrets-*.age"))), 15)
        self.assertEqual(subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                        capture_output=True, text=True).stdout, head)

    def test_check_writes_nothing(self):
        before = sorted(p.name for p in TMP.iterdir())
        code, text = self.run_backup("--check")
        self.assertEqual(code, 0, text)
        self.assertIn("check ok", text)
        self.assertEqual(sorted(p.name for p in TMP.iterdir()), before)

    def test_constants_match_secretbox(self):
        self.assertEqual(secretbackup._VAULT_MAGIC, secretbox._VAULT_MAGIC)
        self.assertEqual(secretbackup._VAULT_AAD, secretbox._VAULT_AAD)

    def test_production_sources_exclude_vault_json_and_key(self):
        names = [n for _, n in self._saved["SOURCES"]]
        paths = [str(p) for p, _ in self._saved["SOURCES"]]
        self.assertNotIn("vault.json", names)
        self.assertFalse(any(p.endswith("master.key") or ".secretbox" in p for p in paths))

    # --- key escrow ------------------------------------------------------

    def _escrow(self, out, rcpt_text, forbidden=()):
        (TMP / "keyescrow.recipient").write_text(rcpt_text + "\n")
        keyescrow.KEYFILE = secretbox.KEYFILE
        keyescrow.ESCROW_RECIPIENT_FILE = TMP / "keyescrow.recipient"
        keyescrow.BACKUP_RECIPIENT_FILE = TMP / "secretbackup.recipient"
        keyescrow.FORBIDDEN_DIRS = [Path(p) for p in forbidden]
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = keyescrow.escrow(Path(out))
        text = o.getvalue() + e.getvalue()
        self.assertNotIn(self.key.hex(), text)
        return code, text

    def test_escrow_round_trip_and_key_untouched(self):
        ident, pub = _age_identity("escrow")
        st = secretbox.KEYFILE.stat()
        out = TMP / "offbox" / "master.key.age"
        code, text = self._escrow(out, pub, forbidden=[TMP / "repo"])
        self.assertEqual(code, 0, text)
        self.assertNotIn(self.key, out.read_bytes())
        got = subprocess.run(["age", "-d", "-i", str(ident), str(out)],
                             capture_output=True, check=True).stdout
        self.assertEqual(got, self.key)
        # the live key is only read: same bytes, same inode, same mtime
        st2 = secretbox.KEYFILE.stat()
        self.assertEqual((st.st_ino, st.st_mtime_ns), (st2.st_ino, st2.st_mtime_ns))
        self.assertEqual(secretbox.KEYFILE.read_bytes(), self.key)
        # the backup identity cannot open the escrow
        self.assertNotEqual(subprocess.run(["age", "-d", "-i", str(self.ident), str(out)],
                                           capture_output=True).returncode, 0)

    def test_drill_recovers_from_bundle_plus_escrow(self):
        """The off-box drill: only the two artifacts and their two identities."""
        bundle = TMP / "drill.age"
        bundle.unlink(missing_ok=True)
        self.assertEqual(self.run_backup("--out", str(bundle))[0], 0)
        ident, pub = _age_identity("drill-escrow")
        escrow = TMP / "drill-offbox" / "master.key.age"
        self.assertEqual(self._escrow(escrow, pub)[0], 0)
        r = subprocess.run([sys.executable, str(ROOT / "secretdrill.py"),
                            "--bundle", str(bundle), "--bundle-identity", str(self.ident),
                            "--escrow", str(escrow), "--escrow-identity", str(ident),
                            "--expect", "3"], capture_output=True, text=True)
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("vault opened: 3 entries in 2 projects", out)
        self.assertNotIn(CANARY, out)
        self.assertNotIn(self.key.hex(), out)
        # swapped identities must not work — each artifact has its own key
        r = subprocess.run([sys.executable, str(ROOT / "secretdrill.py"),
                            "--bundle", str(bundle), "--bundle-identity", str(ident),
                            "--escrow", str(escrow), "--escrow-identity", str(self.ident)],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_escrow_refuses_backup_recipient(self):
        pub = (TMP / "secretbackup.recipient").read_text().strip()
        code, text = self._escrow(TMP / "x.age", pub)
        self.assertEqual(code, 2)
        self.assertIn("same as the backup recipient", text)

    def test_escrow_refuses_backup_repo(self):
        _, pub = _age_identity("escrow2")
        code, text = self._escrow(TMP / "repo" / "k.age", pub, forbidden=[TMP / "repo"])
        self.assertEqual(code, 2)
        self.assertFalse((TMP / "repo" / "k.age").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
