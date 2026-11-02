"""Passkey (WebAuthn) gate for Secretbox reveal — synthetic authenticators only."""
import ast
import hashlib
import html
import io
import json
import os
import struct
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import passkey as pk  # noqa: E402

RP = "alfred.example.ts.net"
ORIGIN = f"https://{RP}"


# ---- a tiny CBOR encoder + software authenticator (test-only) --------------

def cbor(v) -> bytes:
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
    if isinstance(v, bool):
        return bytes([0xF5 if v else 0xF4])
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return head(3, len(b)) + b
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(v)


class Authenticator:
    def __init__(self, kind="es256", cred_id=b"synthetic-cred-1"):
        self.kind, self.cred_id, self.count = kind, cred_id, 0
        if kind == "es256":
            self.key = ec.generate_private_key(ec.SECP256R1())
            n = self.key.public_key().public_numbers()
            self.cose = {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"),
                         -3: n.y.to_bytes(32, "big")}
        else:
            self.key = ed25519.Ed25519PrivateKey.generate()
            from cryptography.hazmat.primitives import serialization as s
            raw = self.key.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)
            self.cose = {1: 1, 3: -8, -1: 6, -2: raw}

    def _cd(self, typ, challenge, origin=ORIGIN):
        return json.dumps({"type": typ, "challenge": challenge, "origin": origin}).encode()

    def register(self, challenge, *, rp=RP, origin=ORIGIN, flags=0x45):
        ad = (hashlib.sha256(rp.encode()).digest() + bytes([flags]) + struct.pack(">I", 0)
              + bytes(16) + struct.pack(">H", len(self.cred_id)) + self.cred_id + cbor(self.cose))
        att = cbor({"fmt": "none", "attStmt": {}, "authData": ad})
        return {"attestationObject": pk.b64u(att),
                "clientDataJSON": pk.b64u(self._cd("webauthn.create", challenge, origin))}

    def assert_(self, challenge, *, rp=RP, origin=ORIGIN, flags=0x05, count=None, tamper=False):
        self.count = self.count + 1 if count is None else count
        ad = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        cd = self._cd("webauthn.get", challenge, origin)
        signed = ad + hashlib.sha256(cd).digest()
        sig = (self.key.sign(signed, ec.ECDSA(hashes.SHA256())) if self.kind == "es256"
               else self.key.sign(signed))
        if tamper:
            sig = sig[:-1] + bytes([sig[-1] ^ 1])
        return {"credentialId": pk.b64u(self.cred_id), "authenticatorData": pk.b64u(ad),
                "clientDataJSON": pk.b64u(cd), "signature": pk.b64u(sig)}


def enrolled(kind="es256"):
    a = Authenticator(kind)
    r = a.register("reg-challenge")
    cred = pk.verify_registration(r["attestationObject"], r["clientDataJSON"],
                                  "reg-challenge", RP, ORIGIN)
    return a, cred


# ---- verifier ---------------------------------------------------------------

@pytest.mark.parametrize("kind,alg", [("es256", -7), ("ed25519", -8)])
def test_registration_and_assertion_roundtrip(kind, alg):
    a, cred = enrolled(kind)
    assert cred["alg"] == alg and cred["id"] == pk.b64u(a.cred_id)
    r = a.assert_("ch")
    assert pk.verify_assertion(cred, r["authenticatorData"], r["clientDataJSON"],
                               r["signature"], "ch", RP, ORIGIN) == 1


@pytest.mark.parametrize("kw,why", [
    ({"origin": "https://evil.example"}, "origin"),
    ({"rp": "evil.example"}, "different site"),
    ({"flags": 0x41}, "biometric"),        # UP+AT, no UV
])
def test_registration_rejected(kw, why):
    r = Authenticator().register("c", **kw)
    with pytest.raises(pk.PasskeyError, match=why):
        pk.verify_registration(r["attestationObject"], r["clientDataJSON"], "c", RP, ORIGIN)


def test_registration_wrong_challenge_rejected():
    r = Authenticator().register("real")
    with pytest.raises(pk.PasskeyError, match="challenge"):
        pk.verify_registration(r["attestationObject"], r["clientDataJSON"], "other", RP, ORIGIN)


@pytest.mark.parametrize("kw,expect_ch,why", [
    ({"tamper": True}, "ch", "signature"),
    ({}, "different-challenge", "challenge"),
    ({"origin": "https://evil.example"}, "ch", "origin"),
    ({"rp": "evil.example"}, "ch", "different site"),
    ({"flags": 0x01}, "ch", "biometric"),   # present but not verified
])
def test_assertion_rejected(kw, expect_ch, why):
    a, cred = enrolled()
    r = a.assert_("ch", **kw)
    with pytest.raises(pk.PasskeyError, match=why):
        pk.verify_assertion(cred, r["authenticatorData"], r["clientDataJSON"],
                            r["signature"], expect_ch, RP, ORIGIN)


def test_assertion_with_another_key_rejected():
    _, cred = enrolled()
    impostor = Authenticator()            # same cred id, different private key
    r = impostor.assert_("ch")
    with pytest.raises(pk.PasskeyError, match="signature"):
        pk.verify_assertion(cred, r["authenticatorData"], r["clientDataJSON"],
                            r["signature"], "ch", RP, ORIGIN)


def test_counter_regression_rejected_but_zero_counters_ok():
    a, cred = enrolled()
    cred["sign_count"] = 5
    r = a.assert_("ch", count=3)
    with pytest.raises(pk.PasskeyError, match="clone"):
        pk.verify_assertion(cred, r["authenticatorData"], r["clientDataJSON"],
                            r["signature"], "ch", RP, ORIGIN)
    cred["sign_count"] = 0                 # synced passkeys always report 0
    r = a.assert_("ch2", count=0)
    assert pk.verify_assertion(cred, r["authenticatorData"], r["clientDataJSON"],
                               r["signature"], "ch2", RP, ORIGIN) == 0


def test_challenges_are_single_use_and_purpose_bound():
    s = pk.ChallengeStore()
    ch = s.issue("reveal:A")
    assert not s.consume(ch, "reveal:B")   # wrong purpose burns it
    assert not s.consume(ch, "reveal:A")
    ch = s.issue("reveal:A")
    assert s.consume(ch, "reveal:A")
    assert not s.consume(ch, "reveal:A")   # replay
    s2 = pk.ChallengeStore(ttl=-1)
    assert not s2.consume(s2.issue("x"), "x")   # expired


def test_enroll_code_single_use_attempt_limited_and_rate_limited():
    c = pk.EnrollCodes(min_interval=0)
    code = c.issue()
    assert len(code) == 8 and code.isdigit()
    assert c.check(code) and not c.check(code)
    code = c.issue()
    for _ in range(5):
        c.check("not-it")
    assert not c.check(code)               # burned by guessing
    r = pk.EnrollCodes(min_interval=60)
    assert r.issue() and r.issue() is None


def test_credential_store_private_and_atomic(tmp_path):
    f = tmp_path / "sb" / "passkeys.json"
    pk.save_credentials(f, [{"id": "x", "public_key": "pem", "alg": -7}])
    # Windows uses ACLs; POSIX permission bits are meaningful on POSIX only.
    if os.name == "posix":
        assert (f.stat().st_mode & 0o777) == 0o600
    assert pk.load_credentials(f)[0]["id"] == "x"
    f.write_text("{garbage")
    assert pk.load_credentials(f) == []    # unreadable store keeps reveal closed


# ---- HTTP handler (compiled from source; no live secretbox state) ----------

@pytest.fixture
def server(tmp_path):
    src = ROOT / "secretbox.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Handler")
    resolve_reveal = Mock(return_value=("synthetic-secret-value", "vault", None))
    audit = Mock()
    notify = Mock(return_value=True)
    ns = dict(BaseHTTPRequestHandler=BaseHTTPRequestHandler, json=json, pk=pk,
              EXPECT_LOGIN="owner@example.invalid", token_eq=lambda a, b: a == b,
              esc=html.escape, RP_ID=RP, RP_ORIGIN=ORIGIN,
              PASSKEY_FILE=tmp_path / "passkeys.json",
              PK_CHALLENGES=pk.ChallengeStore(), PK_CODES=pk.EnrollCodes(min_interval=0),
              resolve_reveal=resolve_reveal, audit_reveal=audit, ops_notify=notify,
              PK_SCRIPT="")
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(src), "exec"), ns)

    def post(route, body, headers=None, raw=None):
        h = object.__new__(ns["Handler"])
        data = raw if raw is not None else json.dumps(body).encode()
        hdrs = {"Content-Type": "application/json", "Content-Length": str(len(data)),
                "Tailscale-User-Login": "owner@example.invalid"}
        hdrs.update(headers or {})
        h.headers, h.path, h.rfile = hdrs, "/secrets/passkey/" + route, io.BytesIO(data)
        h.client_address = ("127.0.0.1", 1)
        h._json, h.send_error = Mock(), Mock()
        h.do_POST()
        if h.send_error.called:
            return h.send_error.call_args.args[0], None
        return h._json.call_args.args

    return SimpleNamespace(post=post, ns=ns, resolve=resolve_reveal, audit=audit,
                           notify=notify)


def _enroll(server):
    a, cred = enrolled()
    cred.update(label="phone", created="2026-11-02")
    pk.save_credentials(server.ns["PASSKEY_FILE"], [cred])
    return a


def test_http_reveal_happy_path(server):
    a = _enroll(server)
    code, out = server.post("auth/begin", {"action": "reveal", "ref": "P/KEY"})
    assert code == 200
    body = a.assert_(out["publicKey"]["challenge"])
    body["ref"] = "P/KEY"
    code, out = server.post("reveal", body)
    assert code == 200 and out["value"] == "synthetic-secret-value"
    server.resolve.assert_called_once_with("P/KEY")
    assert server.audit.call_args.args[0] == "passkey:phone"


def test_http_reveal_forged_header_without_assertion_refused(server):
    _enroll(server)
    code, out = server.post("reveal", {"ref": "P/KEY", "clientDataJSON": pk.b64u(b"{}")})
    assert code == 403
    server.resolve.assert_not_called()


def test_http_challenge_bound_to_ref(server):
    a = _enroll(server)
    _, out = server.post("auth/begin", {"action": "reveal", "ref": "P/HARMLESS"})
    body = a.assert_(out["publicKey"]["challenge"])
    body["ref"] = "P/OTHER"                # reuse the assertion for another key
    code, _ = server.post("reveal", body)
    assert code == 403
    server.resolve.assert_not_called()


def test_http_assertion_replay_refused(server):
    a = _enroll(server)
    _, out = server.post("auth/begin", {"action": "reveal", "ref": "P/KEY"})
    body = dict(a.assert_(out["publicKey"]["challenge"]), ref="P/KEY")
    assert server.post("reveal", body)[0] == 200
    assert server.post("reveal", body)[0] == 403
    assert server.resolve.call_count == 1


def test_http_no_passkey_means_no_reveal(server):
    code, _ = server.post("auth/begin", {"action": "reveal", "ref": "P/KEY"})
    assert code == 403


def test_http_requires_json_content_type(server):
    code, _ = server.post("reveal", None, headers={"Content-Type": "text/plain"}, raw=b"x")
    assert code == 415


def test_http_registration_needs_the_telegram_code(server):
    assert server.post("register/begin", {"code": "00000000"})[0] == 403
    assert server.post("enroll/code", {})[0] == 200
    sent = server.notify.call_args.args[0]
    code = next(w for w in sent.split() if w.isdigit() and len(w) == 8)
    status, out = server.post("register/begin", {"code": code})
    assert status == 200
    a = Authenticator(cred_id=b"phone-2")
    reg = a.register(out["publicKey"]["challenge"])
    assert server.post("register/complete", dict(reg, label="new phone"))[0] == 200
    creds = pk.load_credentials(server.ns["PASSKEY_FILE"])
    assert [c["label"] for c in creds] == ["new phone"]
    # The code is single use: a second registration attempt is refused.
    assert server.post("register/begin", {"code": code})[0] == 403


def test_http_register_complete_without_begin_refused(server):
    reg = Authenticator().register("never-issued")
    assert server.post("register/complete", reg)[0] == 403
    assert pk.load_credentials(server.ns["PASSKEY_FILE"]) == []


def test_http_wrong_identity_still_refused(server):
    code, _ = server.post("auth/begin", {"action": "reveal", "ref": "P/KEY"},
                          headers={"Tailscale-User-Login": "intruder@example.invalid"})
    assert code == 403


def test_http_delete_requires_assertion(server):
    a = _enroll(server)
    cid = pk.b64u(a.cred_id)
    assert server.post("delete", {"id": cid, "clientDataJSON": pk.b64u(b"{}")})[0] == 403
    _, out = server.post("auth/begin", {"action": "delete", "id": cid})
    body = dict(a.assert_(out["publicKey"]["challenge"]), id=cid)
    assert server.post("delete", body)[0] == 200
    assert pk.load_credentials(server.ns["PASSKEY_FILE"]) == []
