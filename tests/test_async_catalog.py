import asyncio
import threading
from tgbridge import model_catalog, handlers


def test_keyboard_is_cache_only(monkeypatch):
    monkeypatch.setattr(model_catalog, "_openrouter_cache", [("openai/fixture", "Fixture")])
    monkeypatch.setattr(model_catalog, "_fetch_openrouter_models", lambda *a: (_ for _ in ()).throw(AssertionError("network")))
    kb = handlers.xmodels_kb()
    assert kb.inline_keyboard[0][0].callback_data == "xmodel:openai/fixture"


def test_discovery_is_nonblocking_and_concurrent_calls_share_work(monkeypatch):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        calls = []
        def fetch(force):
            calls.append(force)
            entered.set()
            assert release.wait(2)
            return [("openai/fixture", "Fixture")]
        monkeypatch.setattr(model_catalog, "_fetch_openrouter_models", fetch)
        monkeypatch.setattr(model_catalog, "_discovery_task", None)
        first = asyncio.create_task(model_catalog.refresh_openrouter_models())
        second = asyncio.create_task(model_catalog.refresh_openrouter_models())
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set() and not first.done()
        finally:
            release.set()
        assert await first == await second
        assert calls == [False]
    asyncio.run(scenario())
