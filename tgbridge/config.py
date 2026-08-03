"""Config & persistent state for the bridge.

Purpose:  Loads secrets/settings from env/.env/keyring; owns every state-file read+write.
Inputs:   Environment, the .env beside bridge.py, keyring, state/*.json on disk.
Outputs:  Module-level constants (token, chat ids, paths, caps) + load_json/save_json.
Key fns:  load_json, save_json, authorized_chat, is_dangerous_workdir, sweep_tmp.
Deps:     keyring (optional). No bridge-internal deps — everything else imports this.
Note:     _SAVE_LOCK serializes state writes; save_json is the single writer.
Updated:  2026-07-31

Secrets come from the environment or the .env file next to bridge.py
(never hardcoded: the bot token gates prompt injection into an elevated
Claude, i.e. it is as sensitive as a shell on this machine).
"""

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# BRIDGE_STATE_DIR lets a second bridge process (a dedicated per-project agent
# with its own bot token) keep its own sessions, memory and KB instead of
# fighting the main instance over these files. Unset = the original path, so
# existing installs are unaffected.
_state_override = os.environ.get("BRIDGE_STATE_DIR", "").strip()
STATE_DIR = Path(_state_override).expanduser() if _state_override else ROOT / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# All bridge temp files live on the project drive — the system drive filling
# up must not break TTS / file sending / the claude subprocess.
TMP_DIR = STATE_DIR / "tmp"
TMP_DIR.mkdir(exist_ok=True)


# Never sweep these — they are live working dirs, not leaked scratch.
TMP_KEEP = {"claude"}


def sweep_tmp(max_age_hours: float = 72.0) -> int:
    """Remove stale entries from our own tmp dir.

    Every agent subprocess inherits TEMP=this dir, so it accumulates whole
    DIRECTORIES (browser profiles, PyInstaller _MEI* extractions, clones) — not
    just files. Sweeping files only let it grow to ~7 GB unnoticed, so this
    removes stale directories too. Returns the number of entries removed.
    """
    import time, shutil
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    try:
        for p in TMP_DIR.iterdir():
            if p.name in TMP_KEEP:
                continue
            try:
                if p.stat().st_mtime >= cutoff:
                    continue
                if p.is_file():
                    p.unlink()
                    removed += 1
                elif p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                    removed += 1
            except OSError:
                pass  # in use by a running agent — try again next sweep
    except OSError:
        pass
    return removed


def system_drive_free_gb() -> float | None:
    """Free space on the OS volume, in GB. None if it can't be determined.

    Windows uses %SystemDrive% (C:\\); POSIX uses /. Without the POSIX branch
    this probed a literal "C:\\" on macOS/Linux, raised OSError and always
    returned None — silently disabling the low-disk startup warning.
    """
    import shutil as _sh
    try:
        if os.name == "nt":
            target = os.environ.get("SystemDrive", "C:") + "\\"
        else:
            target = "/"
        return _sh.disk_usage(target).free / 2**30
    except OSError:
        return None


def _load_env(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines, # comments).

    A NON-EMPTY .env value overrides an inherited/leaked OS-env var of the same
    name — .env is the intended source of truth, and a stale shadowing var (e.g.
    an old OPENROUTER_API_KEY exported at logon) must not silently win. An EMPTY
    .env value (`KEY=`) is a placeholder: it never clobbers an existing env var,
    so `setdefault` is used there."""
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            ln = raw.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, _, v = ln.partition("=")
            k, v = k.strip(), v.strip()
            if v:
                os.environ[k] = v          # .env wins over a stale OS-env value
            else:
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass


# BRIDGE_ENV_FILE points a second bridge process at its own config. Without it
# every instance would load ROOT/.env — and since a non-empty .env value wins
# over the OS env, a dedicated agent would silently inherit the MAIN bot token
# and then fight the main bridge over getUpdates.
_env_file = os.environ.get("BRIDGE_ENV_FILE", "").strip()
_load_env(Path(_env_file).expanduser() if _env_file else ROOT / ".env")

# The claude subprocess inherits our os.environ, and an API key there BEATS the
# Max subscription OAuth in ~/.claude/.credentials.json — so a stray sk-ant-…
# (ours came from a kitchen-sink .env) silently diverts every session onto
# pay-as-you-go Console billing. With an empty balance that surfaced in Telegram
# as "credit balance is too low to access the Anthropic API". Nothing in the
# bridge reads these (the model router uses OPENROUTER_/GEMINI_ keys), so drop
# them unconditionally: the subscription must always win.
for _v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
           "ANTHROPIC_MODEL"):
    os.environ.pop(_v, None)

# The Claude Agent SDK derives the `initialize` handshake timeout from this env
# var read out of OUR process's environment (claude_agent_sdk/client.py reads
# os.environ — NOT the per-session ClaudeAgentOptions.env, which only reaches
# the subprocess). A cold-start claude.exe (freshly extracted after a reboot,
# resuming a large session) can take well over the 60s default to answer
# `initialize`; that surfaced as recurring "Control request timeout: initialize"
# start failures. Set it here so the handshake actually gets the headroom; the
# session also forwards the same value to the subprocess. setdefault keeps any
# real env / .env override the user set.
CLAUDE_INIT_TIMEOUT_MS = os.environ.setdefault(
    "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "180000")


def _try_keyring(name: str) -> str:
    try:
        import keyring  # optional dependency
        return keyring.get_password("telegram-claude-bridge", name) or ""
    except Exception:
        return ""


BOT_TOKEN = os.environ.get("BRIDGE_BOT_TOKEN", "") or _try_keyring("bot_token")
CHAT_ID = int(os.environ.get("BRIDGE_CHAT_ID", "0"))          # owner private chat
GROUP_ID = int(os.environ.get("BRIDGE_GROUP_ID", "0"))        # optional forum supergroup (threaded mode)
_DEFAULT_WORKDIR = r"D:\Projects" if os.name == "nt" else str(Path.home() / "projects")
WORKDIR = os.environ.get("BRIDGE_WORKDIR", _DEFAULT_WORKDIR)
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
# The loopback lock only catches a duplicate on THIS machine. A copy running
# elsewhere (another laptop, a cloud deploy) still steals getUpdates, and the
# two pollers then trade updates forever — this instance stays up but receives
# almost nothing. After this many seconds of unbroken conflict, give the token
# to the other instance and exit with CONFLICT_EXIT_RC so the supervisor waits
# it out instead of respawning a bridge that can't hear anything. 0 = never.
CONFLICT_EXIT_SECS = int(os.environ.get("BRIDGE_CONFLICT_EXIT_SECS", "600"))
CONFLICT_EXIT_RC = 75            # EX_TEMPFAIL; supervisor.py maps it to a long wait
CONFLICT_WINDOW_SECS = 120       # no conflict for this long = the fight is over
PEER_NAME = os.environ.get("BRIDGE_PEER_NAME", "bridge")      # how this bridge introduces itself
# "alice=http://host:9001;bob=http://host2:9002"
PEERS: dict[str, str] = {}
for _part in os.environ.get("BRIDGE_PEERS", "").split(";"):
    if "=" in _part:
        _n, _, _u = _part.partition("=")
        if _n.strip() and _u.strip():
            PEERS[_n.strip()] = _u.strip().rstrip("/")

# Per-peer tokens: "alfred-cloud=tok;tlvquest=tok;robin=tok".
#
# With ONE shared token the `from` field on an inbound message is self-declared
# and therefore worth nothing: any holder of the token can claim to be any peer,
# which also makes per-peer secret grants a label rather than a control. With a
# table, the sender's identity is DERIVED from which token it presented, so it
# cannot be claimed at all.
#
# BRIDGE_PEER_TOKEN stays as the legacy shared secret and is still accepted
# inbound, so peers can be migrated one at a time instead of all at once. Drop
# it once every peer appears in the table.
PEER_TOKENS: dict[str, str] = {}
for _part in os.environ.get("BRIDGE_PEER_TOKENS", "").split(";"):
    if "=" in _part:
        _n, _, _t = _part.partition("=")
        if _n.strip() and _t.strip():
            PEER_TOKENS[_n.strip()] = _t.strip()

# What we present when sending. Our own entry if we have one, else the legacy
# shared token, so an un-migrated bridge keeps talking.
PEER_SELF_TOKEN = PEER_TOKENS.get(PEER_NAME) or PEER_TOKEN

# Peers running the pre-per-peer code. They authenticate an inbound message by
# comparing it to their OWN single token, so they must be sent that token rather
# than ours — otherwise they reject us. It costs nothing here and saves a code
# change on a machine we do not control. Because such a peer's token is now
# unique to it, its messages still identify it unambiguously.
LEGACY_PEERS = {p.strip() for p in
                os.environ.get("BRIDGE_PEER_LEGACY", "").replace(";", ",").split(",")
                if p.strip()}

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
MEMORY_FILE = STATE_DIR / "memory.json"          # legacy flat store (migrated)
KB_DIR = STATE_DIR / "kb"                        # per-agent Napkin vaults: kb/<agent>/
WATCHERS_FILE = STATE_DIR / "watchers.json"      # passive-watcher targets
TODOS_FILE = STATE_DIR / "todos.json"            # the /todo Kanban list
EXPENSES_FILE = STATE_DIR / "expenses.json"      # the /expense ledger
CONTACTS_FILE = STATE_DIR / "contacts.json"      # the /contact book
AUDIT_FILE = STATE_DIR / "audit.jsonl"
APP_LOG_FILE = STATE_DIR / "bridge-app.log"
BACKUP_DIR = STATE_DIR / "backup"
LEGACY_SESSION_FILE = ROOT / "session_id.txt"   # migrated into sessions.json for "main"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# One lock funnels every state write. save_json is the single writer for all
# state files, so serializing it here covers concurrent async handlers AND the
# scheduler thread (the file I/O below is synchronous) — a second writer can't
# interleave write_text/replace and leave a half-written or clobbered file.
_SAVE_LOCK = threading.Lock()


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    with _SAVE_LOCK:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


def authorized_chat(chat_id: int) -> bool:
    return chat_id == CHAT_ID or (GROUP_ID and chat_id == GROUP_ID)


import re as _re

_DANGEROUS_WORKDIRS = [
    # Windows
    _re.compile(r"^[a-z]:[\\/]?$", _re.I),            # a bare drive root (C:\)
    _re.compile(r"^[a-z]:[\\/]windows", _re.I),        # the Windows dir
    _re.compile(r"^[a-z]:[\\/]program files", _re.I),  # Program Files
    # POSIX (macOS/Linux) — same intent: filesystem root and system trees.
    # Trailing (?:/|$) so /usr matches but /Users does not.
    _re.compile(r"^/$"),                               # filesystem root
    _re.compile(r"^/(System|Library|Applications|bin|sbin|usr|etc|var|dev|proc|boot)(?:/|$)"),
    _re.compile(r"^/private(?:/|$)"),                  # macOS /private/etc, /private/var
    _re.compile(r"^/Volumes/?$"),                      # the mount root itself
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
