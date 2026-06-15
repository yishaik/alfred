"""Bot-to-bot transport across processes/machines.

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

import httpx

from .config import PEER_BIND, PEER_NAME, PEER_PORT, PEER_TOKEN, PEERS

log = logging.getLogger("bridge.peers")

_MAX_BODY = 64 * 1024


class PeerBus:
    def __init__(self, mgr):
        self.mgr = mgr
        self._server: asyncio.Server | None = None
        self._http = httpx.AsyncClient(timeout=15)

    def known(self, name: str) -> bool:
        return name in PEERS

    async def start(self):
        if PEER_PORT and PEER_TOKEN:
            # loopback unless BRIDGE_PEER_BIND says otherwise — an all-interfaces
            # listener should be a deliberate choice, not the default
            self._server = await asyncio.start_server(
                self._handle, host=PEER_BIND, port=PEER_PORT)
            log.info("peer bus listening on %s:%d as %r",
                     PEER_BIND, PEER_PORT, PEER_NAME)
        elif PEER_PORT:
            log.warning("BRIDGE_PEER_PORT set but BRIDGE_PEER_TOKEN empty — "
                        "refusing to listen unauthenticated")

    async def stop(self):
        if self._server:
            self._server.close()
        await self._http.aclose()

    async def send(self, peer: str, src_agent: str, text: str, hop: int) -> bool:
        url = PEERS.get(peer)
        if not url:
            return False
        try:
            r = await self._http.post(url + "/msg", json={
                "token": PEER_TOKEN, "from": f"{PEER_NAME}/{src_agent}",
                "agent": "", "text": text, "hop": hop})
            return r.status_code == 200
        except Exception as e:
            log.warning("peer send to %s failed: %s", peer, e)
            return False

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
            if body.get("token") != PEER_TOKEN:
                await self._respond(writer, 403, {"ok": False})
                return
            await self._respond(writer, 200, {"ok": True})
            await self.mgr.on_peer_message(
                str(body.get("from", "?"))[:60],
                str(body.get("agent", ""))[:30],
                str(body.get("text", ""))[:8000],
                int(body.get("hop", 0)))
        except Exception as e:
            log.debug("peer request rejected: %s", e)
            try:
                await self._respond(writer, 400, {"ok": False})
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
