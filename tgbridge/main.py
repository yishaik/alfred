"""Wiring: build the Telegram application, the manager, scheduler and peer bus."""

import logging
from logging.handlers import RotatingFileHandler

from telegram import Update
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, MessageHandler,
                          MessageReactionHandler, filters)

from . import handlers
from .config import (APP_LOG_FILE, BOT_TOKEN, CHAT_ID, GROUP_ID, INBOX,
                     INVALID_DANGER_PATTERNS, LOCK_PORT, STATE_DIR, sweep_tmp,
                     system_drive_free_gb)
from .manager import AgentManager
from .peers import PeerBus
from .scheduler import Scheduler

log = logging.getLogger("bridge")


BOT_COMMANDS = [
    ("panel", "control panel"),
    ("status", "bridge status"),
    ("interrupt", "stop the current turn"),
    ("mute", "silence this topic (keeps the session)"),
    ("bg", "run a task in the background"),
    ("branch", "run a prompt two ways, compare"),
    ("merge", "two takes merged into one"),
    ("agents", "manage agents"),
    ("sessions", "resume a past conversation"),
    ("find", "search past conversations"),
    ("jobs", "scheduled reminders/prompts"),
    ("remind", "remind <when>|<text>"),
    ("fork", "branch this conversation"),
    ("auto", "toggle tap-to-approve"),
    ("secretary", "toggle secretary mode"),
    ("soul", "view/edit the agent's character"),
    ("remember", "pin a fact across sessions"),
    ("memory", "list what I remember"),
    ("forget", "drop a remembered item"),
    ("proactive", "toggle idle check-ins"),
    ("tts", "toggle voice replies"),
    ("voice", "pick the TTS voice"),
    ("digest", "what I did today"),
    ("watch", "watch a path/repo for changes"),
    ("audit", "recent tool calls"),
    ("logs", "recent warnings/errors"),
    ("restart", "restart Claude (resumes)"),
]


async def post_init(app):
    sweep_tmp()
    m = AgentManager(app)
    app.bot_data["mgr"] = m
    free = system_drive_free_gb()
    if free is not None and free < 2:
        try:
            await app.bot.send_message(
                CHAT_ID, f"🚨 system drive C: has only {free:.1f}GB free — "
                "expect Windows/Claude failures until space is freed.")
        except Exception:
            pass
    if INVALID_DANGER_PATTERNS:
        log.warning("invalid BRIDGE_DANGER_PATTERNS ignored: %s",
                    INVALID_DANGER_PATTERNS)
        try:
            await app.bot.send_message(
                CHAT_ID, "⚠️ some BRIDGE_DANGER_PATTERNS don't compile and "
                "are NOT active: " + "; ".join(INVALID_DANGER_PATTERNS)[:500])
        except Exception:
            pass
    m.scheduler = Scheduler(m)
    m.scheduler.start()
    m.peers = PeerBus(m)
    await m.peers.start()
    m.start_health_loop()
    m.start_proactive_loop()
    m.start_digest_loop()
    m.start_escalate_loop()
    m.start_dream_loop()
    m.start_watch_loop()
    try:
        await app.bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        log.exception("set_my_commands failed")
    try:
        INBOX.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    s = await m.session_for_agent(m.active)
    try:
        await app.bot.send_message(
            CHAT_ID,
            f"🤖 Claude bridge v2 online · agent {m.active} · "
            f"cwd {s.cfg.workdir} · model {s.model or 'default'}\n"
            "Type to chat, /panel for controls.",
            reply_markup=handlers.panel_kb(s))
    except Exception:
        log.exception("online message failed")


async def post_shutdown(app):
    m: AgentManager = app.bot_data.get("mgr")
    if m:
        if m.scheduler:
            m.scheduler.stop()
        if m.peers:
            await m.peers.stop()
        await m.stop_all()


def _disable_wmi_platform_lookup():
    """platform.system() on Windows runs a WMI query first; WMI hangs forever
    when the OS is degraded (observed with a full system drive), and PTB calls
    platform.system() inside run_polling. Raising OSError forces platform's
    registry/API fallback, which is instant and accurate."""
    import platform

    def _no_wmi(*_a, **_k):
        raise OSError("WMI lookup disabled by bridge")

    platform._wmi_query = _no_wmi


_lock_sock = None


def _acquire_singleton_lock() -> bool:
    """Bind a loopback port so only one bridge polls this bot. The OS frees the
    port the instant the process dies, so there's no stale-lock problem. Returns
    False if another instance already holds it."""
    global _lock_sock
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))   # no SO_REUSEADDR: a 2nd bind must fail
    except OSError:
        s.close()
        return False
    s.listen(1)
    _lock_sock = s   # keep a reference for the process lifetime
    return True


def main():
    _disable_wmi_platform_lookup()
    rot = RotatingFileHandler(APP_LOG_FILE, maxBytes=5 * 1024 * 1024,
                              backupCount=3, encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), rot])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit(
            "[bridge] BRIDGE_BOT_TOKEN / BRIDGE_CHAT_ID not set "
            f"(env or .env next to bridge.py; state dir {STATE_DIR})")
    if not _acquire_singleton_lock():
        log.error("another bridge instance already holds 127.0.0.1:%d — "
                  "exiting so we don't fight over getUpdates", LOCK_PORT)
        raise SystemExit(
            f"[bridge] another instance is already running (lock port "
            f"{LOCK_PORT}). Refusing to start a second poller.")

    auth = filters.Chat(CHAT_ID)
    if GROUP_ID:
        auth = auth | filters.Chat(GROUP_ID)

    app = (ApplicationBuilder().token(BOT_TOKEN)
           .post_init(post_init).post_shutdown(post_shutdown).build())

    cmds = {
        "start": handlers.cmd_start,
        "panel": handlers.cmd_panel, "menu": handlers.cmd_panel,
        "status": handlers.cmd_status,
        "restart": handlers.cmd_restart,
        "interrupt": handlers.cmd_interrupt,
        "stop": handlers.cmd_stop,
        "mute": handlers.cmd_mute,
        "unmute": handlers.cmd_unmute,
        "bg": handlers.cmd_bg,
        "branch": handlers.cmd_branch,
        "merge": handlers.cmd_merge,
        "kill": handlers.cmd_kill,
        "agents": handlers.cmd_agents,
        "newagent": handlers.cmd_newagent,
        "delagent": handlers.cmd_delagent,
        "auto": handlers.cmd_auto,
        "secretary": handlers.cmd_secretary,
        "soul": handlers.cmd_soul,
        "remember": handlers.cmd_remember,
        "memory": handlers.cmd_memory,
        "forget": handlers.cmd_forget,
        "proactive": handlers.cmd_proactive,
        "cwd": handlers.cmd_cwd,
        "bind": handlers.cmd_bind,
        "jobs": handlers.cmd_jobs,
        "remind": handlers.cmd_remind,
        "tts": handlers.cmd_tts,
        "voice": handlers.cmd_voice,
        "sessions": handlers.cmd_sessions,
        "fork": handlers.cmd_fork,
        "find": handlers.cmd_find,
        "digest": handlers.cmd_digest,
        "watch": handlers.cmd_watch,
        "unwatch": handlers.cmd_unwatch,
        "peers": handlers.cmd_peers,
        "audit": handlers.cmd_audit,
        "logs": handlers.cmd_logs,
    }
    for name, fn in cmds.items():
        app.add_handler(CommandHandler(name, fn, filters=auth))

    app.add_handler(MessageReactionHandler(handlers.on_reaction))
    app.add_handler(MessageHandler(
        auth & filters.UpdateType.EDITED_MESSAGE & filters.TEXT,
        handlers.on_edited))
    app.add_handler(MessageHandler(
        auth & (filters.VOICE | filters.AUDIO), handlers.on_voice))
    app.add_handler(MessageHandler(auth & filters.LOCATION, handlers.on_location))
    app.add_handler(MessageHandler(
        auth & (filters.PHOTO | filters.Document.ALL | filters.VIDEO),
        handlers.on_media))
    app.add_handler(MessageHandler(auth & filters.TEXT, handlers.on_text))
    app.add_handler(CallbackQueryHandler(handlers.on_callback))
    app.add_error_handler(handlers.on_error)

    print(f"[bridge] starting — chat={CHAT_ID} group={GROUP_ID or '-'}")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
