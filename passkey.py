"""Passkey (WebAuthn) verification for Secretbox.

Purpose:  Let the owner reveal a secret in the browser only after a fresh
          biometric check on their phone. The private key never leaves the
          authenticator, so a process on this box cannot forge the assertion
          (unlike the Tailscale-User-Login header, which a local client can set).
Inputs:   Browser WebAuthn responses (base64url), the stored credential list.
Outputs:  Verified credential records / new sign counts, or PasskeyError.
Deps:     stdlib + `cryptography` only (no py_webauthn / cbor2 on this box).
Note:     No import-time side effects — tests use it with synthetic keys.

Trust model:
- Registration is gated by a one-time code sent only to the owner's Telegram
  ops bot (EnrollCodes). Without it, a local agent could register a software
  authenticator of its own and then "reveal" with it.
- Every verification requires User Present AND User Verified (biometric/PIN),
  the exact RP ID hash and origin, and a single-use, purpose-bound challenge.
- Attestation statements are not verified: we do not need to know the device
  model; the enrollment code is what makes a registration trustworthy.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

FLAG_UP = 0x01   # user present
FLAG_UV = 0x04   # user verified (biometric / PIN)
FLAG_AT = 0x40   # attested credential data included
ALG_ES256, ALG_EDDSA, ALG_RS256 = -7, -8, -257
SUPPORTED_ALGS = (ALG_ES256, ALG_EDDSA, ALG_RS256)


class PasskeyError(Exception):
    """Any verification failure. The message is safe to show the user."""


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    if not isinstance(s, str):
        raise PasskeyError("malformed base64url field")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except Exception as exc:
        raise PasskeyError("malformed base64url field") from exc


# --- minimal CBOR decoder (definite lengths only; WebAuthn is canonical CBOR) -

def cbor_decode(data: bytes, pos: int = 0):
    """Decode one CBOR item at `pos`. Returns (value, next_pos)."""
    if pos >= len(data):
        raise PasskeyError("truncated CBOR")
    ib = data[pos]
    major, info = ib >> 5, ib & 0x1F
    pos += 1
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        if pos + size > len(data):
            raise PasskeyError("truncated CBOR")
        arg = int.from_bytes(data[pos:pos + size], "big")
        pos += size
    else:
        raise PasskeyError("unsupported CBOR encoding")
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major in (2, 3):
        if pos + arg > len(data):
            raise PasskeyError("truncated CBOR")
        raw = data[pos:pos + arg]
        return (raw if major == 2 else raw.decode("utf-8")), pos + arg
    if major == 4:
        out = []
        for _ in range(arg):
            v, pos = cbor_decode(data, pos)
            out.append(v)
        return out, pos
    if major == 5:
        out = {}
        for _ in range(arg):
            k, pos = cbor_decode(data, pos)
            v, pos = cbor_decode(data, pos)
            out[k] = v
        return out, pos
    if major == 6:                      # tag: ignore it, keep the item
        return cbor_decode(data, pos)
    if major == 7 and info in (20, 21, 22, 23):
        return {20: False, 21: True, 22: None, 23: None}[info], pos
    raise PasskeyError("unsupported CBOR item")


# --- COSE public keys ---------------------------------------------------------

def cose_to_public_key(cose: dict):
    """Return (public_key, alg) for an ES256 / EdDSA / RS256 COSE key."""
    if not isinstance(cose, dict):
        raise PasskeyError("malformed public key")
    kty, alg = cose.get(1), cose.get(3)
    try:
        if kty == 2 and alg == ALG_ES256 and cose.get(-1) == 1:
            x, y = cose[-2], cose[-3]
            nums = ec.EllipticCurvePublicNumbers(
                int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1())
            return nums.public_key(), alg
        if kty == 1 and alg == ALG_EDDSA and cose.get(-1) == 6:
            return ed25519.Ed25519PublicKey.from_public_bytes(cose[-2]), alg
        if kty == 3 and alg == ALG_RS256:
            n, e = cose[-1], cose[-2]
            nums = rsa.RSAPublicNumbers(int.from_bytes(e, "big"), int.from_bytes(n, "big"))
            return nums.public_key(), alg
    except (KeyError, TypeError, ValueError) as exc:
        raise PasskeyError("malformed public key") from exc
    raise PasskeyError("unsupported passkey algorithm")


def public_key_to_pem(key) -> str:
    return key.public_bytes(serialization.Encoding.PEM,
                            serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _verify_signature(pem: str, alg: int, signature: bytes, signed: bytes) -> None:
    key = serialization.load_pem_public_key(pem.encode())
    try:
        if alg == ALG_ES256:
            key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif alg == ALG_RS256:
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        elif alg == ALG_EDDSA:
            key.verify(signature, signed)
        else:
            raise PasskeyError("unsupported passkey algorithm")
    except InvalidSignature as exc:
        raise PasskeyError("passkey signature is not valid") from exc


# --- authenticator data / client data -----------------------------------------

def parse_auth_data(ad: bytes) -> dict:
    if len(ad) < 37:
        raise PasskeyError("authenticator data too short")
    out = {"rp_id_hash": ad[:32], "flags": ad[32],
           "sign_count": struct.unpack(">I", ad[33:37])[0]}
    if out["flags"] & FLAG_AT:
        if len(ad) < 55:
            raise PasskeyError("authenticator data too short")
        cred_len = struct.unpack(">H", ad[53:55])[0]
        cred_end = 55 + cred_len
        if cred_end > len(ad):
            raise PasskeyError("authenticator data too short")
        out["credential_id"] = ad[55:cred_end]
        out["cose_key"], _ = cbor_decode(ad, cred_end)
    return out


def _check_auth_data(info: dict, rp_id: str) -> None:
    if not hmac.compare_digest(info["rp_id_hash"], hashlib.sha256(rp_id.encode()).digest()):
        raise PasskeyError("passkey belongs to a different site")
    if not info["flags"] & FLAG_UP:
        raise PasskeyError("user presence was not confirmed")
    if not info["flags"] & FLAG_UV:
        raise PasskeyError("biometric / PIN verification is required")


def client_data_challenge(client_data_b64u: str) -> str:
    """The challenge the browser signed — used to look up the pending one."""
    try:
        cd = json.loads(unb64u(client_data_b64u))
        return str(cd["challenge"])
    except PasskeyError:
        raise
    except Exception as exc:
        raise PasskeyError("malformed client data") from exc


def _check_client_data(raw: bytes, typ: str, challenge: str, origin: str) -> None:
    try:
        cd = json.loads(raw)
    except Exception as exc:
        raise PasskeyError("malformed client data") from exc
    if cd.get("type") != typ:
        raise PasskeyError("wrong WebAuthn ceremony type")
    if not hmac.compare_digest(str(cd.get("challenge", "")), challenge):
        raise PasskeyError("challenge mismatch")
    if cd.get("origin") != origin:
        raise PasskeyError("request came from a different origin")
    if cd.get("crossOrigin"):
        raise PasskeyError("cross-origin requests are not allowed")


def verify_registration(attestation_object: str, client_data: str, challenge: str,
                        rp_id: str, origin: str) -> dict:
    """Verify navigator.credentials.create() output. Returns the record to store."""
    cd_raw = unb64u(client_data)
    _check_client_data(cd_raw, "webauthn.create", challenge, origin)
    att, _ = cbor_decode(unb64u(attestation_object))
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
        raise PasskeyError("malformed attestation")
    info = parse_auth_data(att["authData"])
    _check_auth_data(info, rp_id)
    if "credential_id" not in info:
        raise PasskeyError("no credential in attestation")
    key, alg = cose_to_public_key(info["cose_key"])
    return {"id": b64u(info["credential_id"]), "public_key": public_key_to_pem(key),
            "alg": alg, "sign_count": info["sign_count"]}


def verify_assertion(cred: dict, authenticator_data: str, client_data: str,
                     signature: str, challenge: str, rp_id: str, origin: str) -> int:
    """Verify navigator.credentials.get() output. Returns the new sign count."""
    cd_raw = unb64u(client_data)
    _check_client_data(cd_raw, "webauthn.get", challenge, origin)
    ad = unb64u(authenticator_data)
    info = parse_auth_data(ad)
    _check_auth_data(info, rp_id)
    _verify_signature(cred["public_key"], int(cred["alg"]), unb64u(signature),
                      ad + hashlib.sha256(cd_raw).digest())
    new, old = info["sign_count"], int(cred.get("sign_count", 0))
    # Synced passkeys (iCloud/Google) always report 0; a counter that moves
    # backwards on a device that does count is a cloned authenticator.
    if (new or old) and new <= old:
        raise PasskeyError("passkey counter went backwards — possible clone")
    return new


# --- short-lived server state -------------------------------------------------

class ChallengeStore:
    """Single-use challenges bound to a purpose string (e.g. 'reveal:REF')."""

    def __init__(self, ttl: float = 120.0, max_pending: int = 64):
        self.ttl, self.max_pending = ttl, max_pending
        self._items: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, purpose: str) -> str:
        ch = b64u(secrets.token_bytes(32))
        now = time.monotonic()
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v[1] > now}
            while len(self._items) >= self.max_pending:
                self._items.pop(next(iter(self._items)))
            self._items[ch] = (purpose, now + self.ttl)
        return ch

    def consume(self, challenge: str, purpose: str) -> bool:
        with self._lock:
            item = self._items.pop(challenge, None)
        return bool(item and item[1] > time.monotonic()
                    and hmac.compare_digest(item[0], purpose))


class EnrollCodes:
    """One active 8-digit enrollment code: 10-minute TTL, 5 attempts, single use."""

    def __init__(self, ttl: float = 600.0, attempts: int = 5, min_interval: float = 60.0):
        self.ttl, self.max_attempts, self.min_interval = ttl, attempts, min_interval
        self._code: str | None = None
        self._expires = self._issued = 0.0
        self._attempts = 0
        self._lock = threading.Lock()

    def issue(self) -> str | None:
        """New code, or None if one was issued under min_interval ago."""
        now = time.monotonic()
        with self._lock:
            if self._code and now - self._issued < self.min_interval:
                return None
            self._code = f"{secrets.randbelow(10 ** 8):08d}"
            self._issued, self._expires, self._attempts = now, now + self.ttl, 0
            return self._code

    def check(self, code: str) -> bool:
        with self._lock:
            if not self._code or time.monotonic() > self._expires:
                self._code = None
                return False
            self._attempts += 1
            ok = hmac.compare_digest(self._code, str(code).strip())
            if ok or self._attempts >= self.max_attempts:
                self._code = None            # used, or burned by guessing
            return ok


# --- credential store (inside ~/.secretbox/, 0600, atomic) --------------------

def load_credentials(path: Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        creds = data.get("credentials", [])
        return [c for c in creds if isinstance(c, dict) and c.get("id") and c.get("public_key")]
    except FileNotFoundError:
        return []
    except Exception:
        return []        # unreadable store = no passkeys = reveal stays closed


def save_credentials(path: Path, creds: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".passkeys.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"credentials": creds}, fh, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
