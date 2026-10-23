"""Read provider model catalogs without running a conversation turn."""
import asyncio
import json
import logging
import time

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


_openrouter_cache: list[tuple[str, str]] = []
_openrouter_cache_time: float = 0.0


def _fetch_openrouter_models(force_refresh: bool = False) -> list[tuple[str, str]]:
    """Dynamically fetch the latest frontier models from OpenRouter API.
    Groups by major frontier providers and extracts newest releases.
    Cached for 5 minutes unless force_refresh is True."""
    global _openrouter_cache, _openrouter_cache_time
    now = time.monotonic()
    if _openrouter_cache and not force_refresh and (now - _openrouter_cache_time < 300.0):
        return _openrouter_cache

    import urllib.request
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/models",
        headers={"User-Agent": "telegram-claude-bridge/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=6.0) as r:
            raw = json.loads(r.read()).get("data", [])
    except Exception as e:
        log.warning("OpenRouter dynamic model discovery failed: %s", e)
        if _openrouter_cache:
            return _openrouter_cache
        return []  # No fabricated model IDs when discovery is unavailable.

    notable_providers = {
        "anthropic": "Anthropic",
        "openai": "OpenAI",
        "google": "Google",
        "deepseek": "DeepSeek",
        "x-ai": "xAI",
        "mistralai": "Mistral",
        "qwen": "Qwen",
        "meta-llama": "Meta",
    }
    by_prov: dict[str, list] = {p: [] for p in notable_providers}
    for m in raw:
        mid = m.get("id", "")
        if not mid or ":batch" in mid or ":nitro" in mid or mid.startswith("~"):
            continue
        # Enforce Telegram 64-byte callback limit with "xmodel:" prefix
        if len(("xmodel:" + mid).encode("utf-8")) > 64:
            continue
        prov = mid.split("/")[0] if "/" in mid else ""
        if prov in by_prov:
            name = m.get("name", mid)
            short_name = name.split(":")[-1].strip()
            created = m.get("created", 0)
            by_prov[prov].append((mid, short_name, created))

    results = []
    for p in notable_providers:
        models = by_prov[p]
        models.sort(key=lambda x: x[2], reverse=True)
        for mid, short_name, _ in models[:3]:
            label = short_name
            if len(label) > 26:
                label = label[:24] + "…"
            results.append((mid, label))

    if results:
        _openrouter_cache = results
        _openrouter_cache_time = now
    return _openrouter_cache



_discovery_task = None


def openrouter_models() -> list[tuple[str, str]]:
    """Cache-only snapshot for synchronous keyboard rendering. Never does I/O."""
    return list(_openrouter_cache)


async def refresh_openrouter_models(force_refresh: bool = False):
    """Bounded urllib discovery runs outside the event loop; coalesce callers."""
    global _discovery_task
    if _discovery_task is None or _discovery_task.done():
        _discovery_task = asyncio.create_task(asyncio.to_thread(_fetch_openrouter_models, force_refresh))
    return await asyncio.shield(_discovery_task)
