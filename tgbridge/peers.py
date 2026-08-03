"""Bot-to-bot transport across processes/machines.

Purpose:  Bot-to-bot transport across processes — Telegram bots cannot message each other.
Inputs:   POST /msg carrying the shared token, or send() naming a configured peer.
Outputs:  HTTP deliveries to peers; inbound messages fed to the named local agent.
Key fns:  PeerBus.start/stop/send/known/diagnostics.
Deps:     httpx, config (BRIDGE_PEER_* env).
Note:     The hop count travels with the message so a relay loop cannot run away.
Updated:  2026-07-31

Telegram bots cannot message each other, so bridges talk over a tiny
token-authenticated HTTP bus instead:

    POST /msg  {"token": "...", "from": "alice", "agent": "main",
                "text": "...", "hop": 2}

Configure with BRIDGE_PEER_PORT (listen), BRIDGE_PEER_TOKEN (shared secret),
BRIDGE_PEERS ("name=http://host:port;..."), BRIDGE_PEER_NAME (our name).
Hop counts travel with each message; the manager enforces MAX_HOPS and
per-pair rate limits on both ends, so two bridges can't ping-pong forever.
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import httpx

import hmac

from .config import (LEGACY_PEERS, PEER_BIND, PEER_NAME, PEER_PORT,
                     PEER_SELF_TOKEN, PEER_TOKEN, PEER_TOKENS, PEERS)

log = logging.getLogger("bridge.peers")

_MAX_BODY = 64 * 1024

# Undelivered messages survive a restart of either side: an in-memory queue
# would be emptied by the very event that most often causes the failure.
QUEUE_FILE = Path(os.environ.get("BRIDGE_STATE_DIR", "/home/ubuntu/alfred/state")) / "peer-queue.json"
QUEUE_MAX = int(os.environ.get("BRIDGE_PEER_QUEUE_MAX", "200"))
RETRY_MAX = int(os.environ.get("BRIDGE_PEER_RETRY_MAX", "60"))
FLUSH_EVERY = float(os.environ.get("BRIDGE_PEER_FLUSH_SECS", "60"))


def _load_queue() -> list:
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_queue(q: list) -> None:
    try:
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(QUEUE_FILE.parent))
        os.write(fd, json.dumps(q).encode())
        os.close(fd)
        os.replace(tmp, QUEUE_FILE)
    except Exception as e:
        log.warning("could not persist peer queue: %s", e)


def identify(token: str) -> tuple[str | None, bool]:
    """Map a presented token to the peer that owns it.

    Returns (peer_name, verified). `verified` is False for the legacy shared
    token, which proves only "some peer", not which one — the caller must then
    fall back to the self-declared name and treat it as unverified. Once every
    peer has its own entry, BRIDGE_PEER_TOKEN can be dropped and this returns
    (None, False) for anything unrecognised."""
    if not token:
        return None, False
    for name, tok in PEER_TOKENS.items():
        if hmac.compare_digest(token, tok):
            return name, True
    if PEER_TOKEN and hmac.compare_digest(token, PEER_TOKEN):
        return None, False          # authentic, but anonymous
    return None, False


class PeerBus:
    def __init__(self, mgr):
        self.mgr = mgr
        self._server: asyncio.Server | None = None
        self._http = httpx.AsyncClient(timeout=15)
        self._flusher: asyncio.Task | None = None

    def known(self, name: str) -> bool:
        return name in PEERS

    async def start(self):
        # Gate on having ANY credential — per-peer table or the old shared
        # secret. Checking only PEER_TOKEN silently refused to listen once the
        # shared secret was retired, which takes the bus down without an error.
        if PEER_PORT and (PEER_TOKENS or PEER_TOKEN):
            # loopback unless BRIDGE_PEER_BIND says otherwise — an all-interfaces
            # listener should be a deliberate choice, not the default
            self._server = await asyncio.start_server(
                self._handle, host=PEER_BIND, port=PEER_PORT)
            log.info("peer bus listening on %s:%d as %r",
                     PEER_BIND, PEER_PORT, PEER_NAME)
        elif PEER_PORT:
            log.warning("BRIDGE_PEER_PORT set but no BRIDGE_PEER_TOKENS or "
                        "BRIDGE_PEER_TOKEN — refusing to listen unauthenticated")
        # Retry queued outbound messages regardless of whether we listen:
        # sending and receiving are independent, and a bridge with inbound
        # disabled still has undelivered messages to flush.
        self._flusher = asyncio.create_task(self._flush_loop())

    async def stop(self):
        if self._flusher:
            self._flusher.cancel()
        if self._server:
            self._server.close()
        await self._http.aclose()

    async def _reachable(self, url: str, timeout: float = 3.0) -> bool:
        """Cheap TCP connect to a peer's host:port — true if it accepts."""
        from urllib.parse import urlparse
        u = urlparse(url)
        if not u.hostname:
            return False
        port = u.port or (443 if u.scheme == "https" else 80)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(u.hostname, port), timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    async def diagnostics(self) -> str:
        """Human-readable peer-bus health for the /peers command."""
        lines = [f"🔌 𝗽𝗲𝗲𝗿 𝗯𝘂𝘀 — this bridge is '{PEER_NAME}'"]
        if self._server:
            lines.append(f"📡 listening on {PEER_BIND}:{PEER_PORT}")
        elif PEER_PORT:
            lines.append("⚠️ listener off (no BRIDGE_PEER_TOKEN)")
        else:
            lines.append("📡 listener off (inbound disabled)")
        if not PEERS:
            lines.append("no outbound peers configured (BRIDGE_PEERS)")
            return "\n".join(lines)
        results = await asyncio.gather(
            *(self._reachable(url) for url in PEERS.values()))
        lines.append(f"peers ({len(PEERS)}):")
        for (name, url), ok in zip(PEERS.items(), results):
            lines.append(f"  {'🟢' if ok else '🔴'} {name} — {url}")
        return "\n".join(lines)

    async def send(self, peer: str, src_agent: str, text: str, hop: int) -> bool:
        """Deliver now, or queue and keep trying.

        The bus was fire-and-forget: a peer that was restarting, or a laptop
        that was closed, simply lost the message, and neither side ever knew.
        For two agents meant to coordinate without a human relaying, silent
        loss is the failure that matters — it is indistinguishable from the
        other one choosing not to answer, which is exactly the ambiguity that
        wasted a day here.
        """
        if await self._deliver(peer, src_agent, text, hop):
            return True
        self._enqueue(peer, src_agent, text, hop)
        return False

    def _enqueue(self, peer: str, src_agent: str, text: str, hop: int) -> None:
        q = _load_queue()
        q.append({"peer": peer, "from_agent": src_agent, "text": text, "hop": hop,
                  "queued": time.time(), "tries": 0})
        # Bounded: a peer down for a week should not turn into an inbox that
        # floods it on reconnect.
        _save_queue(q[-QUEUE_MAX:])
        log.info("queued message for %s (%d waiting)", peer, len(q))

    async def _deliver(self, peer: str, src_agent: str, text: str, hop: int) -> bool:
        url = PEERS.get(peer)
        if not url:
            return False
        try:
            # A pre-per-peer peer checks the token against its own, so send it
            # the one it expects; everyone else gets ours, which is what lets
            # them derive who we are.
            tok = (PEER_TOKENS.get(peer) if peer in LEGACY_PEERS else None) or PEER_SELF_TOKEN
            r = await self._http.post(url + "/msg", json={
                "token": tok, "from": f"{PEER_NAME}/{src_agent}",
                "agent": "", "text": text, "hop": hop})
            return r.status_code == 200
        except Exception as e:
            log.warning("peer send to %s failed: %s", peer, e)
            return False

    async def _flush_loop(self):
        """Retry queued messages while their peer is unreachable.

        Backs off per message rather than per peer, so one poisoned message
        cannot hold up the rest, and gives up after RETRY_MAX so a permanently
        dead address does not retry forever.
        """
        while True:
            try:
                await asyncio.sleep(FLUSH_EVERY)
                q = _load_queue()
                if not q:
                    continue
                still = []
                for m in q:
                    if m["tries"] >= RETRY_MAX:
                        log.warning("dropping message to %s after %d tries",
                                    m["peer"], m["tries"])
                        continue
                    if await self._deliver(m["peer"], m["from_agent"], m["text"], m["hop"]):
                        log.info("delivered queued message to %s after %d tries",
                                 m["peer"], m["tries"] + 1)
                        continue
                    m["tries"] += 1
                    still.append(m)
                _save_queue(still)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("peer flush loop error: %s", e)

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            lines = head.decode("latin-1").split("\r\n")
            method, path = lines[0].split(" ")[:2]
            clen = 0
            for ln in lines[1:]:
                if ln.lower().startswith("content-length:"):
                    clen = int(ln.split(":", 1)[1].strip())
            if method != "POST" or path != "/msg" or not (0 < clen <= _MAX_BODY):
                await self._respond(writer, 404, {"ok": False})
                return
            body = json.loads(await asyncio.wait_for(
                reader.readexactly(clen), timeout=10))
            presented = str(body.get("token", ""))
            who, verified = identify(presented)
            legacy_ok = bool(PEER_TOKEN) and hmac.compare_digest(presented, PEER_TOKEN)
            if not verified and not legacy_ok:
                await self._respond(writer, 403, {"ok": False})
                return
            await self._respond(writer, 200, {"ok": True})

            # Identity comes from the token when we can derive it. The claimed
            # `from` is kept only for the agent suffix (alice/main) and only
            # when unverified — it is sender-controlled, so it must never be
            # what a rate limit or a grant is keyed on.
            claimed = str(body.get("from", "?"))[:60]
            if verified:
                suffix = claimed.partition("/")[2]
                origin = f"{who}/{suffix}" if suffix else who
            else:
                origin = claimed
                log.warning("peer msg authenticated with the LEGACY shared "
                            "token; identity %r is unverified", claimed)
            await self.mgr.on_peer_message(
                origin,
                str(body.get("agent", ""))[:30],
                str(body.get("text", ""))[:8000],
                int(body.get("hop", 0)),
                verified=verified)
        except Exception as e:
            log.warning("peer message failed: %s", e)
            try:
                await self._respond(writer, 500, {"ok": False})
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _respond(self, writer, code: int, obj: dict):
        body = json.dumps(obj).encode()
        writer.write(
            f"HTTP/1.1 {code} {'OK' if code == 200 else 'NO'}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n".encode() + body)
        await writer.drain()
