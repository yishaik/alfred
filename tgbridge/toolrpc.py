"""Loopback RPC that exposes the bridge's tools to a process outside it.

Purpose:  Let any engine — not just Claude's in-process SDK — call bridge tools.
Inputs:   POST /tools/list and /tools/call on 127.0.0.1, bearer-authenticated.
Outputs:  Tool descriptors, and tool results.
Key fns:  ToolRPC.start/stop, and the two handlers.
Deps:     bridgetools (the single source of truth for what a tool is).
Updated:  2026-08-02

The problem this solves
-----------------------
Bridge tools are Python closures over a live session: send_file writes to that
session's outbox, remember writes to that agent's vault. They cannot be handed
to a separate process as code. But Codex — and any future engine — can only
load MCP servers over stdio, i.e. as a separate process.

So the tools stay where they are and the *call* travels instead. mcp_shim.py
runs as the stdio server the engine talks to, and forwards here, where the
session actually lives.

Why the tool list is served rather than declared: a tool added to
bridgetools.py must work in every engine without a second edit. The shim asks
this endpoint what exists at the moment it is asked, so a new tool is visible
to Codex the next time it lists tools — no shim change, no config change.

Bound to loopback and bearer-authenticated. Loopback alone would already mean
"anything running as any user on this box", and these tools send the owner
files and read their memory.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import typing
from pathlib import Path

from . import bridgetools

log = logging.getLogger("bridge.toolrpc")

HOST = "127.0.0.1"
PORT = int(os.environ.get("BRIDGE_TOOLRPC_PORT", "49620"))
TOKEN_FILE = Path(os.environ.get("BRIDGE_STATE_DIR", "/home/ubuntu/alfred/state")) / "toolrpc.token"
_MAX_BODY = 1024 * 1024


def token() -> str:
    """A per-install secret, generated once. The shim reads the same file."""
    try:
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t
    except FileNotFoundError:
        pass
    t = secrets.token_urlsafe(24)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(t, encoding="utf-8")
    TOKEN_FILE.chmod(0o600)
    return t


def _json_schema(input_schema) -> dict:
    """Turn the SDK's {name: Annotated[type, "desc"]} into JSON Schema.

    Engines other than Claude need real JSON Schema; the SDK form is Python
    typing objects, which do not survive a process boundary.
    """
    if isinstance(input_schema, dict) and input_schema.get("type") == "object":
        return input_schema            # already JSON Schema
    props, required = {}, []
    for key, ann in (input_schema or {}).items():
        py, desc = ann, ""
        if typing.get_origin(ann) is typing.Annotated:
            args = typing.get_args(ann)
            py = args[0]
            desc = next((a for a in args[1:] if isinstance(a, str)), "")
        kind = {str: "string", int: "integer", float: "number",
                bool: "boolean", list: "array", dict: "object"}.get(py, "string")
        props[key] = {"type": kind}
        if desc:
            props[key]["description"] = desc
        required.append(key)
    return {"type": "object", "properties": props, "required": required}


class ToolRPC:
    def __init__(self, mgr):
        self.mgr = mgr
        self._server: asyncio.Server | None = None
        self._token = token()

    async def start(self):
        self._server = await asyncio.start_server(self._handle, host=HOST, port=PORT)
        log.info("tool RPC on %s:%d (%d tools)", HOST, PORT, len(bridgetools.TOOL_NAMES) or 12)

    async def stop(self):
        if self._server:
            self._server.close()

    async def _session_for(self, agent: str):
        """Tools are bound to a session, so resolve one — creating it if the
        engine asked before the agent had a turn."""
        return await self.mgr.session_for_agent(agent if agent in self.mgr.agents
                                                else self.mgr.active)

    async def _list(self, agent: str) -> dict:
        s = await self._session_for(agent)
        return {"tools": [{"name": t.name,
                           "description": t.description,
                           "inputSchema": _json_schema(t.input_schema)}
                          for t in bridgetools.build_tools(s)]}

    async def _call(self, agent: str, name: str, args: dict) -> dict:
        s = await self._session_for(agent)
        tools = {t.name: t for t in bridgetools.build_tools(s)}
        t = tools.get(name)
        if not t:
            return {"error": f"no such tool: {name}",
                    "available": sorted(tools)}
        try:
            return {"result": await t.handler(args or {})}
        except Exception as e:
            log.warning("tool %s failed: %s", name, e)
            return {"error": f"{type(e).__name__}: {e}"}

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            lines = head.decode("latin-1").split("\r\n")
            method, path = lines[0].split(" ")[:2]
            clen, auth = 0, ""
            for ln in lines[1:]:
                low = ln.lower()
                if low.startswith("content-length:"):
                    clen = int(ln.split(":", 1)[1].strip())
                elif low.startswith("authorization:"):
                    auth = ln.split(":", 1)[1].strip().removeprefix("Bearer ").strip()
            if method != "POST" or not (0 < clen <= _MAX_BODY):
                await self._respond(writer, 404, {"error": "POST only"})
                return
            if not hmac.compare_digest(auth, self._token):
                await self._respond(writer, 403, {"error": "bad tool-rpc token"})
                return
            body = json.loads(await asyncio.wait_for(reader.readexactly(clen), timeout=10))
            agent = str(body.get("agent") or "")
            if path.endswith("/tools/list"):
                await self._respond(writer, 200, await self._list(agent))
            elif path.endswith("/tools/call"):
                await self._respond(writer, 200, await self._call(
                    agent, str(body.get("name") or ""), body.get("arguments") or {}))
            else:
                await self._respond(writer, 404, {"error": "unknown path"})
        except Exception as e:
            log.warning("tool rpc request failed: %s", e)
            try:
                await self._respond(writer, 500, {"error": str(e)[:200]})
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _respond(self, writer, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        writer.write(f"HTTP/1.1 {code} OK\r\nContent-Type: application/json\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()
