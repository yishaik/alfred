"""Creation and secure provisioning of Alfred-managed Telegram bots.

Purpose: Turn Telegram's Managed Bot request button into an isolated bridge.
Inputs:  A ManagedBotCreated service message from the authenticated owner.
Outputs: A mode-0600 env file, agent state, and a started systemd instance.
Security: The child token is never logged or included in a Telegram response.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

from telegram import (KeyboardButton, KeyboardButtonRequestManagedBot,
                      ReplyKeyboardMarkup)

from .config import CHAT_ID, ROOT

log = logging.getLogger("bridge.managed_bots")

REQUEST_ID = 20260803
ENV_FILE = ROOT / ".env-secondbrain"
STATE_DIR = ROOT / "state-secondbrain"
WORKDIR = Path("/home/ubuntu/git/second-brain")
SERVICE = "alfred-secondbrain.service"


def request_keyboard() -> ReplyKeyboardMarkup:
    button = KeyboardButton(
        "🧠 צור את בוט ה־Second Brain",
        request_managed_bot=KeyboardButtonRequestManagedBot(
            request_id=REQUEST_ID,
            suggested_name="Second Brain",
            suggested_username="MySecondBrainBot"))
    return ReplyKeyboardMarkup([[button]], resize_keyboard=True,
                               one_time_keyboard=True)


def _atomic_private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_agent_state() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    soul = {
        "display_name": "Second Brain", "emoji": "🧠",
        "role": "מנהל הידע האישי והאתר של ישי",
        "tone": "ישיר, בהיר ומבוסס מקורות",
        "values": ["דיוק", "שמירת מקור", "עדכון רציף"], "quirks": [],
        "notes": (
            "Search, read and explain the local Second Brain. For every URL, "
            "use fetch_content so X Reader saves the full source, then run "
            "ingest_second_brain. Maintain the here.now wiki with "
            "publish_second_brain_site and report failures clearly. Never "
            "invent knowledge that is not present in the sources.")}
    data = {"active": "main", "agents": {"main": {
        "workdir": str(WORKDIR), "model": "", "soul": soul,
        "secretary": False, "auto_approve": True, "tts": False,
        "voice": "", "proactive": False, "always_allow": []}}}
    _atomic_private_write(STATE_DIR / "agents.json",
                          json.dumps(data, ensure_ascii=False, indent=2) + "\n")


async def provision(bot, managed_user_id: int) -> None:
    """Claim a managed token and start its isolated service."""
    token = await bot.get_managed_bot_token(managed_user_id)
    if not token or ":" not in token:
        raise RuntimeError("Telegram returned an invalid managed-bot token")
    await bot.set_managed_bot_access_settings(
        managed_user_id, is_access_restricted=True,
        added_user_ids=[CHAT_ID])
    env = "\n".join([
        f"BRIDGE_BOT_TOKEN={token}", f"BRIDGE_CHAT_ID={CHAT_ID}",
        f"BRIDGE_WORKDIR={WORKDIR}", f"BRIDGE_STATE_DIR={STATE_DIR}",
        "BRIDGE_LOCK_PORT=49516",
        "BRIDGE_CLAUDE_BIN=/home/ubuntu/.local/bin/claude",
        "BRIDGE_PEER_NAME=secondbrain", "BRIDGE_PEER_PORT=0",
        "BRIDGE_INSTANCE_NAME=secondbrain", "",
    ])
    _atomic_private_write(ENV_FILE, env)
    _write_agent_state()
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", "systemctl", "enable", "--now", SERVICE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    output, _ = await proc.communicate()
    if proc.returncode:
        log.error("managed bot service failed to start: %s",
                  (output or b"").decode("utf-8", "replace")[:500])
        raise RuntimeError("the bot was created but its service did not start")


def needs_creation() -> bool:
    return not ENV_FILE.exists()
