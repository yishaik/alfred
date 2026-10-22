"""Read provider model catalogs without running a conversation turn."""
import asyncio
import json
import logging

log = logging.getLogger("bridge.models")


def rows(models):
    result, seen = [], set()
    for model in models or []:
        if not isinstance(model, dict) or model.get("hidden"):
            continue
        mid = model.get("value") or model.get("model") or model.get("id")
        if not isinstance(mid, str) or not mid or mid in seen:
            continue
        # Telegram callback payload limit is 64 bytes (prefix included).
        if len(("cmodel:" + mid).encode()) > 64:
            continue
        label = model.get("displayName") or model.get("display_name") or model.get("label") or mid
        result.append((mid, label))
        seen.add(mid)
    return result


async def codex_models():
    from .codex_engine import CODEX_BIN
    proc = await asyncio.create_subprocess_exec(
        CODEX_BIN, "app-server", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

    async def request(mid, method, params):
        proc.stdin.write((json.dumps(dict(id=mid, method=method, params=params)) + "\n").encode())
        await proc.stdin.drain()
        while line := await proc.stdout.readline():
            obj = json.loads(line)
            if obj.get("id") == mid:
                if "error" in obj:
                    raise RuntimeError("model discovery rejected")
                return obj.get("result", {})
        raise RuntimeError("model discovery closed")

    async def discover():
        await request(1, "initialize", {"clientInfo": {"name": "alfred-model-picker", "version": "1.0"}})
        proc.stdin.write(b'{"method":"initialized"}\n')
        await proc.stdin.drain()
        found, cursor, mid = [], None, 2
        while True:
            params = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            page = await request(mid, "model/list", params)
            found.extend(page.get("data", []))
            cursor = page.get("nextCursor")
            if not cursor:
                return rows(found)
            mid += 1
    try:
        return await asyncio.wait_for(discover(), 15)
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


async def refresh(session):
    from . import antigravity_engine, codex_engine
    engine = session.engine
    catalog = getattr(session, "model_catalog", {})
    try:
        if engine == "codex":
            found = await codex_models()
        elif engine == "antigravity":
            found = await asyncio.to_thread(antigravity_engine.list_models, True)
        elif session.client and session.connected:
            info = await session.client.get_server_info()
            found = rows((info or {}).get("models", []))
        else:
            found = []
        if found:
            catalog[engine] = found
    except Exception:
        log.warning("model discovery failed for %s; retaining fallback catalog", engine, exc_info=True)
    if not catalog.get(engine):
        catalog[engine] = (codex_engine.list_models() if engine == "codex" else
                           [] if engine == "antigravity" else
                           [("opus", "Opus (alias)"), ("sonnet", "Sonnet (alias)"),
                            ("haiku", "Haiku (alias)")])
    session.model_catalog = catalog
