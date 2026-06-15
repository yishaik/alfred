"""Voice in/out.

In:  transcription via OpenAI Whisper (OPENAI_API_KEY) or Groq (GROQ_API_KEY).
Out: TTS via OpenAI (opus → real Telegram voice note) or the free edge-tts
     package (mp3 → audio message) when installed.
"""

import logging
import tempfile

import httpx

from .config import (GROQ_API_KEY, OPENAI_API_KEY, TMP_DIR, TTS_EDGE_VOICE,
                     TTS_VOICE)

log = logging.getLogger("bridge.voice")


def available() -> bool:
    return bool(OPENAI_API_KEY or GROQ_API_KEY)


async def transcribe(path: str) -> str | None:
    if OPENAI_API_KEY:
        return await _whisper("https://api.openai.com/v1/audio/transcriptions",
                              OPENAI_API_KEY, "whisper-1", path)
    if GROQ_API_KEY:
        return await _whisper("https://api.groq.com/openai/v1/audio/transcriptions",
                              GROQ_API_KEY, "whisper-large-v3-turbo", path)
    return None


async def _whisper(url: str, key: str, model: str, path: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            with open(path, "rb") as fh:
                r = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    data={"model": model},
                    files={"file": (path.rsplit("\\", 1)[-1], fh, "audio/ogg")})
        if r.status_code == 200:
            return (r.json().get("text") or "").strip() or None
        log.warning("transcription HTTP %s: %.300s", r.status_code, r.text)
    except Exception as e:
        log.warning("transcription failed: %s", e)
    return None


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #
def tts_available() -> bool:
    if OPENAI_API_KEY:
        return True
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


async def synthesize(text: str) -> tuple[str, bool] | None:
    """Return (path, is_voice_note). is_voice_note=True means OGG/Opus
    (Telegram voice bubble); False means mp3 (audio message)."""
    text = text.strip()[:1500]
    if not text:
        return None
    if OPENAI_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={"model": "gpt-4o-mini-tts", "voice": TTS_VOICE,
                          "input": text, "response_format": "opus"})
            if r.status_code == 200:
                fd = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False,
                                                 dir=str(TMP_DIR))
                fd.write(r.content)
                fd.close()
                return fd.name, True
            log.warning("openai tts HTTP %s: %.200s", r.status_code, r.text)
        except OSError:
            raise   # disk full etc. — the session auto-disables TTS on this
        except Exception as e:
            log.warning("openai tts failed: %s", e)
    try:
        import edge_tts
        fd = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False,
                                         dir=str(TMP_DIR))
        fd.close()
        await edge_tts.Communicate(text, TTS_EDGE_VOICE).save(fd.name)
        return fd.name, False
    except ImportError:
        return None
    except OSError:
        raise   # disk full etc. — the session auto-disables TTS on this
    except Exception as e:
        log.warning("edge-tts failed: %s", e)
        return None
