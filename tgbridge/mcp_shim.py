#!/usr/bin/env python3
"""A stdio MCP server that forwards to the running bridge.

Purpose:  Give any engine the bridge's tools, over the one protocol they all speak.
Inputs:   MCP JSON-RPC on stdin.
Outputs:  MCP JSON-RPC on stdout; tool calls forwarded to toolrpc.
Deps:     stdlib only, so it starts fast and cannot drift from the bridge's venv.
Updated:  2026-08-02

This file deliberately knows NOTHING about which tools exist. It asks the
bridge on every tools/list, so a tool added to bridgetools.py is available to
every engine the next time it lists — no edit here, no config change, no
restart of anything but the engine's own session. That was the requirement:
future tools have to work too.

Register with Codex:
    codex mcp add bridge -- python3 /home/ubuntu/alfred/tgbridge/mcp_shim.py

The agent whose session the tools bind to comes from BRIDGE_AGENT, so one shim
binary serves alfred, tlvquest and storycut without duplication.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PORT = os.environ.get("BRIDGE_TOOLRPC_PORT", "49620")
BASE = f"http://127.0.0.1:{PORT}"
AGENT = os.environ.get("BRIDGE_AGENT", "main")
STATE = Path(os.environ.get("BRIDGE_STATE_DIR", "/home/ubuntu/alfred/state"))
PROTOCOL = "2024-11-05"


def _token() -> str:
    try:
        return (STATE / "toolrpc.token").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _post(path: str, payload: dict, timeout: float = 300.0) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps({**payload, "agent": AGENT}).encode(),
        headers={"content-type": "application/json",
                 "authorization": "Bearer " + _token()},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _reply(rid, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    _send(msg)


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method, rid = msg.get("method"), msg.get("id")

        if method == "initialize":
            _reply(rid, {"protocolVersion": PROTOCOL,
                         "capabilities": {"tools": {"listChanged": True}},
                         "serverInfo": {"name": "bridge", "version": "1.0.0"}})
        elif method == "notifications/initialized":
            continue                       # a notification: no id, no reply
        elif method == "tools/list":
            try:
                _reply(rid, {"tools": _post("/tools/list", {}, timeout=30).get("tools", [])})
            except Exception as e:
                # An empty list beats an error here: the engine keeps working
                # with its own tools instead of failing to start because the
                # bridge happened to be restarting.
                print(f"bridge shim: tools/list failed: {e}", file=sys.stderr)
                _reply(rid, {"tools": []})
        elif method == "tools/call":
            params = msg.get("params") or {}
            try:
                out = _post("/tools/call", {"name": params.get("name"),
                                            "arguments": params.get("arguments") or {}})
            except Exception as e:
                _reply(rid, {"content": [{"type": "text",
                                          "text": f"bridge unreachable: {e}"}],
                             "isError": True})
                continue
            if "error" in out:
                _reply(rid, {"content": [{"type": "text", "text": str(out["error"])}],
                             "isError": True})
            else:
                # The bridge returns the MCP content shape already, because the
                # tools were written against it.
                _reply(rid, out.get("result") or {"content": []})
        elif rid is not None:
            _reply(rid, error={"code": -32601, "message": f"unknown method {method}"})


if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
