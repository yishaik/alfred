"""Config & persistent state for the bridge.

Secrets come from the environment or the .env file next to bridge.py
(never hardcoded: the bot token gates prompt injection into an elevated
Claude, i.e. it is as sensitive as a shell on this machine).
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
STATE_DIR.mkdir(exist_ok=True)

# All bridge temp files live on the project drive — the system drive filling
# up must not break TTS / file sending / the claude subprocess.
TMP_DIR = STATE_DIR / "tmp"
TMP_DIR.mkdir(exist_ok=True)


def sweep_tmp(max_age_hours: float = 24.0) -> None:
    """Remove stale files from our own tmp dir (startup hygiene)."""
    import time
    cutoff = time.time() - max_age_hours * 3600
    try:
        for p in TMP_DIR.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def system_drive_free_gb() -> float | None:
    import shutil as _sh
    try:
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        return _sh.disk_usage(drive).free / 2**30
    except OSError:
        return None


def _load_env(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines, # comments); real env wins."""
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            ln = raw.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, _, v = ln.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_env(ROOT / ".env")


def _try_keyring(name: str) -> str:
    try:
        import keyring  # optional dependency
        return keyring.get_password("telegram-claude-bridge", name) or ""
    except Exception:
        return ""


BOT_TOKEN = os.environ.get("BRIDGE_BOT_TOKEN", "") or _try_keyring("bot_token")
CHAT_ID = int(os.environ.get("BRIDGE_CHAT_ID", "0"))          # owner private chat
GROUP_ID = int(os.environ.get("BRIDGE_GROUP_ID", "0"))        # optional forum supergroup (threaded mode)
WORKDIR = os.environ.get("BRIDGE_WORKDIR", r"D:\Projects")
MODEL = os.environ.get("BRIDGE_MODEL", "")                    # "" = Claude default
CLAUDE_BIN = os.environ.get("BRIDGE_CLAUDE_BIN", "")          # "" = let the SDK find it

# Voice transcription (first key found wins)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
TTS_VOICE = os.environ.get("BRIDGE_TTS_VOICE", "alloy")          # OpenAI voice name
TTS_EDGE_VOICE = os.environ.get("BRIDGE_TTS_EDGE_VOICE", "en-US-AriaNeural")

# Bot-to-bot (cross-process) transport
PEER_PORT = int(os.environ.get("BRIDGE_PEER_PORT", "0"))      # 0 = HTTP listener off
PEER_TOKEN = os.environ.get("BRIDGE_PEER_TOKEN", "")
# loopback by default; set 0.0.0.0 explicitly to accept remote peers
PEER_BIND = os.environ.get("BRIDGE_PEER_BIND", "127.0.0.1")

# Single-instance guard: the bridge binds this loopback port at startup so a
# second copy (a double-launched .vbs/supervisor) can't poll the same bot
# token and trigger Telegram "Conflict: terminated by other getUpdates".
LOCK_PORT = int(os.environ.get("BRIDGE_LOCK_PORT", "49517"))
PEER_NAME = os.environ.get("BRIDGE_PEER_NAME", "bridge")      # how this bridge introduces itself
# "alice=http://host:9001;bob=http://host2:9002"
PEERS: dict[str, str] = {}
for _part in os.environ.get("BRIDGE_PEERS", "").split(";"):
    if "=" in _part:
        _n, _, _u = _part.partition("=")
        if _n.strip() and _u.strip():
            PEERS[_n.strip()] = _u.strip().rstrip("/")

# --------------------------------------------------------------------------- #
# Rate limits — these exist to prevent infinite loops (bot<->bot ping-pong,
# runaway schedulers, crash-restart storms) and Telegram API floods.
# --------------------------------------------------------------------------- #
MAX_HOPS = int(os.environ.get("BRIDGE_MAX_HOPS", "4"))                 # bot->bot relay depth
BOT_TURNS_PER_HOUR = int(os.environ.get("BRIDGE_BOT_TURNS_PER_HOUR", "30"))   # non-human-triggered turns / agent
PAIR_MSGS_PER_5MIN = int(os.environ.get("BRIDGE_PAIR_MSGS_PER_5MIN", "10"))   # msgs per (src,dst) agent pair
MIN_RECUR_MINUTES = int(os.environ.get("BRIDGE_MIN_RECUR_MINUTES", "15"))     # floor for recurring jobs
MAX_JOBS = int(os.environ.get("BRIDGE_MAX_JOBS", "50"))
TURN_WARN_SECONDS = int(os.environ.get("BRIDGE_TURN_WARN_SECONDS", "600"))    # watchdog "still running" ping
SEND_MIN_INTERVAL = 1.05      # seconds between Telegram sends per chat (~Telegram's 1 msg/s)
EDIT_MIN_INTERVAL = 1.5       # seconds between streaming draft edits

# Proactive idle check-ins (opt-in per agent via /proactive)
PROACTIVE_IDLE_HOURS = float(os.environ.get("BRIDGE_PROACTIVE_IDLE_HOURS", "6"))
# do-not-disturb window "start-end" in 24h hours; may wrap midnight (default 22-8)
def _parse_quiet(raw: str) -> tuple[int, int]:
    try:
        a, _, b = raw.partition("-")
        return int(a) % 24, int(b) % 24
    except ValueError:
        return 22, 8
PROACTIVE_QUIET_START, PROACTIVE_QUIET_END = \
    _parse_quiet(os.environ.get("BRIDGE_PROACTIVE_QUIET", "22-8"))

# Ops
HEALTH_TIME = os.environ.get("BRIDGE_HEALTH_TIME", "09:00")      # "" disables the daily report
DIGEST_TIME = os.environ.get("BRIDGE_DIGEST_TIME", "")           # "" = off; e.g. "20:00" for an evening recap
ESCALATE_MINUTES = float(os.environ.get("BRIDGE_ESCALATE_MINUTES", "10"))  # 0 = off
DREAM_TIME = os.environ.get("BRIDGE_DREAM_TIME", "")             # "" = off; early-morning brief, e.g. "06:00"
WATCH_MINUTES = float(os.environ.get("BRIDGE_WATCH_MINUTES", "5"))  # passive-watcher poll interval; 0 = off
MONTHLY_BUDGET_USD = float(os.environ.get("BRIDGE_MONTHLY_BUDGET_USD", "0"))  # 0 = off
CONTEXT_WARN_PCT = float(os.environ.get("BRIDGE_CONTEXT_WARN_PCT", "70"))
SHOW_DIFFS = os.environ.get("BRIDGE_SHOW_DIFFS", "1") not in ("0", "false", "off")
# extra danger regexes for the guardrail, ";"-separated


def parse_danger_patterns(raw: str) -> tuple[list[str], list[str]]:
    """Split and compile-check user regexes -> (valid, invalid)."""
    import re as _re
    valid, invalid = [], []
    for p in raw.split(";"):
        if not p.strip():
            continue
        try:
            _re.compile(p, _re.IGNORECASE)
            valid.append(p)
        except _re.error:
            invalid.append(p)
    return valid, invalid


EXTRA_DANGER_PATTERNS, INVALID_DANGER_PATTERNS = \
    parse_danger_patterns(os.environ.get("BRIDGE_DANGER_PATTERNS", ""))

# Output shaping
TG_MAX = 4000                 # safe message length (hard limit 4096)
FILE_THRESHOLD = 3500         # longer replies are sent as a document instead of split spam
BATCH_WINDOW = 0.35           # seconds to coalesce non-streamed output lines
PERMISSION_TIMEOUT = 600      # seconds to wait for an approval tap before denying

INBOX = Path(WORKDIR) / "inbox"
IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Persistent state files
AGENTS_FILE = STATE_DIR / "agents.json"
SESSIONS_FILE = STATE_DIR / "sessions.json"
JOBS_FILE = STATE_DIR / "jobs.json"
COSTS_FILE = STATE_DIR / "costs.json"
TOPICS_FILE = STATE_DIR / "topics.json"
MEMORY_FILE = STATE_DIR / "memory.json"          # {agent: [memory items]}
WATCHERS_FILE = STATE_DIR / "watchers.json"      # passive-watcher targets
TODOS_FILE = STATE_DIR / "todos.json"            # the /todo Kanban list
AUDIT_FILE = STATE_DIR / "audit.jsonl"
APP_LOG_FILE = STATE_DIR / "bridge-app.log"
BACKUP_DIR = STATE_DIR / "backup"
LEGACY_SESSION_FILE = ROOT / "session_id.txt"   # migrated into sessions.json for "main"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def authorized_chat(chat_id: int) -> bool:
    return chat_id == CHAT_ID or (GROUP_ID and chat_id == GROUP_ID)


import re as _re

_DANGEROUS_WORKDIRS = [
    _re.compile(r"^[a-z]:[\\/]?$", _re.I),            # a bare drive root (C:\)
    _re.compile(r"^[a-z]:[\\/]windows", _re.I),        # the Windows dir
    _re.compile(r"^[a-z]:[\\/]program files", _re.I),  # Program Files
]


def is_dangerous_workdir(path: str) -> bool:
    """True for paths an agent's cwd should never be set to: a network share,
    a bare drive root, or a system directory. Pure — used to gate /cwd and
    /newagent so a typo can't aim the agent at C:\\Windows."""
    p = (path or "").strip().strip('"')
    if not p:
        return True
    if p.startswith("\\\\") or p.startswith("//"):     # UNC / network share
        return True
    return any(rx.match(p) for rx in _DANGEROUS_WORKDIRS)
