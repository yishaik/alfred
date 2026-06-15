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
# Selectable voices per backend (for the /voice picker). OpenAI names are flat;
# edge names carry locale + "Neural" so we can tell the two apart at synth time.
OPENAI_VOICES = ["alloy", "ash", "coral", "echo", "fable", "nova", "onyx",
                 "sage", "shimmer", "verse"]
EDGE_VOICES = ["en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural",
               "en-GB-RyanNeural", "en-GB-SoniaNeural",
               "he-IL-AvriNeural", "he-IL-HilaNeural"]


def tts_available() -> bool:
    if OPENAI_API_KEY:
        return True
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def active_backend() -> str | None:
    """Which TTS engine voices should be offered for — the one that will
    actually render. OpenAI wins when keyed; edge is the free fallback."""
    if OPENAI_API_KEY:
        return "openai"
    try:
        import edge_tts  # noqa: F401
        return "edge"
    except ImportError:
        return None


def list_voices() -> tuple[str | None, list[str]]:
    """(backend, voice names) for the active engine; ([] if none available)."""
    backend = active_backend()
    if backend == "openai":
        return backend, OPENAI_VOICES
    if backend == "edge":
        return backend, EDGE_VOICES
    return None, []


def default_voice(backend: str | None = None) -> str:
    backend = backend or active_backend()
    return TTS_VOICE if backend == "openai" else TTS_EDGE_VOICE


async def synthesize(text: str, voice: str = "") -> tuple[str, bool] | None:
    """Return (path, is_voice_note). is_voice_note=True means OGG/Opus
    (Telegram voice bubble); False means mp3 (audio message). `voice` overrides
    the configured default for the active backend."""
    text = text.strip()[:1500]
    if not text:
        return None
    if OPENAI_API_KEY:
        # an edge voice name (…Neural) makes no sense here — fall back to default
        ovoice = voice if (voice and "Neural" not in voice) else TTS_VOICE
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={"model": "gpt-4o-mini-tts", "voice": ovoice,
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
        # only honour an edge-shaped voice name on this backend
        evoice = voice if (voice and "Neural" in voice) else TTS_EDGE_VOICE
        fd = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False,
                                         dir=str(TMP_DIR))
        fd.close()
        await edge_tts.Communicate(text, evoice).save(fd.name)
        return fd.name, False
    except ImportError:
        return None
    except OSError:
        raise   # disk full etc. — the session auto-disables TTS on this
    except Exception as e:
        log.warning("edge-tts failed: %s", e)
        return None
