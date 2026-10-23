import asyncio
import sys
from unittest.mock import AsyncMock
import pytest
from tgbridge import codex_engine


def test_real_codex_child_is_reaped_on_interrupt(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(codex_engine, "_argv", lambda *a: [sys.executable, "-c", "import time; time.sleep(60)"])
        monkeypatch.setattr(codex_engine, "default_model", lambda: None)
        task = asyncio.create_task(codex_engine.run_turn("cancel-fixture", "hi", str(tmp_path)))
        for _ in range(100):
            if "cancel-fixture" in codex_engine._running:
                break
            await asyncio.sleep(.01)
        assert await codex_engine.interrupt("cancel-fixture")
        assert task.cancelled()
        assert "cancel-fixture" not in codex_engine._running
        assert not await codex_engine.interrupt("cancel-fixture")
    asyncio.run(scenario())


def test_real_codex_child_timeout_cleans_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_engine, "_argv", lambda *a: [sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(codex_engine, "default_model", lambda: None)
    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(codex_engine.run_turn("timeout-fixture", "hi", str(tmp_path), timeout=.05))
    assert "timeout-fixture" not in codex_engine._running
