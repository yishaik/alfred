---
type: Module
title: voice.py — speech in & out
description: Voice transcription (OpenAI Whisper / Groq) and TTS replies (OpenAI voice notes or free edge-tts).
resource: tgbridge/voice.py
tags: [module, voice, stt, tts]
timestamp: 2026-06-17T00:00:00Z
---

# Responsibility
Speech I/O. Transcribes inbound voice notes and, when `/tts on`, speaks replies.

# Key behaviors
- STT: OpenAI Whisper first, Groq fallback (`OPENAI_API_KEY` / `GROQ_API_KEY`).
- TTS: OpenAI voice notes (opus) or free `edge-tts` audio.
- Voices via `BRIDGE_TTS_VOICE` / `BRIDGE_TTS_EDGE_VOICE`.

# Collaborators
[config](/components/config.md) · invoked by [handlers](/components/handlers.md) and [session](/components/session.md)
