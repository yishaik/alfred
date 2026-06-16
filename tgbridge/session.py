"""One AgentSession = one long-lived Claude Agent SDK client bound to a
Telegram route (chat or forum topic).

Reliability rules baked in:
  * queued user turns survive crashes/restarts (re-fed, never silently dropped)
  * crash restarts back off exponentially; after repeated fast failures the
    session id is dropped so a corrupt resume can't crash-loop forever
  * a watchdog pings the user when a turn runs unusually long
  * non-human turns (scheduler, bot-to-bot) draw from an hourly budget
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                              ClaudeSDKClient, PermissionResultAllow,
                              PermissionResultDeny, RateLimitEvent,
                              ResultMessage, StreamEvent, SystemMessage,
                              TaskNotificationMessage, TaskProgressMessage,
                              TaskStartedMessage, TextBlock, ToolResultBlock,
                              ToolUseBlock, UserMessage)
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup,
                      ReactionTypeEmoji)
from telegram.constants import ChatAction

from . import bridgetools, guards, markers, voice
import os

from . import metrics
from .config import (BOT_TURNS_PER_HOUR, CHAT_ID, CLAUDE_BIN, CONTEXT_WARN_PCT,
                     MODEL, PERMISSION_TIMEOUT, TMP_DIR, TURN_WARN_SECONDS,
                     WORKDIR)
from .fmt import (SEP, fmt_duration, format_error, format_output,
                  format_tool_lines, summarize_tool, tool_icon)
from .mood import Mood
from .outbox import Outbox
from .soul import Soul
from .ratelimit import Backoff, TokenBucket

FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

log = logging.getLogger("bridge.session")


def _tok(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _pretty_model(m: str) -> str:
    """claude-opus-4-8 / full ids -> a short friendly label."""
    low = (m or "").lower()
    for key, label in (("opus", "Opus"), ("sonnet", "Sonnet"),
                       ("haiku", "Haiku"), ("fable", "Fable")):
        if key in low:
            return label
    return m


def _ctx_bar(pct: float, segs: int = 10) -> str:
    """A 10-segment gauge like ▰▰▰▰▱▱▱▱▱▱ for the context footer."""
    filled = max(0, min(segs, round(pct / 100 * segs)))
    return "▰" * filled + "▱" * (segs - filled)


def _cost_emoji(cost: float) -> str:
    """Traffic-light cue for a turn's cost: cheap 💚 / moderate 💛 / pricey ❤️."""
    if cost < 0.02:
        return "💚"
    if cost < 0.10:
        return "💛"
    return "❤️"

# Read-only / harmless tools that never need a tap even with approvals on.
SAFE_TOOLS = ["Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite",
              "Task", "NotebookRead", "ListMcpResourcesTool", "TaskList",
              "TaskGet"]

BRIDGE_PROMPT = """You are reachable through a Telegram bridge: your text is shown to a single \
user in Telegram. Phone-friendly output, please: short paragraphs, no wide tables.

⚠️ UNAVAILABLE IN THIS BRIDGE: /resume (no pause/resume in async bridge) and /stop \
(use /interrupt instead, or just /panel). Never offer these to the user.

ASKING THE USER TO CHOOSE: prefer the AskUserQuestion tool over prose questions. \
The bridge renders it as native Telegram buttons. The answer reaches you one of \
two ways, depending on bridge mode: (a) inside the tool's permission-denial \
message as "[bridge] user answered: ..." — that denial is the TRANSPORT, not an \
error; or (b) the tool errors with "Answer questions?" — also EXPECTED: stop, \
wait, and the tapped answer arrives as the next user message. Never retry the \
tool or apologize for these errors.

QUESTION RULES (strict): ask ONE question at a time — a single question per \
AskUserQuestion call, never a bundle; if you need several answers, ask them one \
by one, each after the previous answer arrives. The bridge waits for the user's \
tap indefinitely. NEVER proceed on an assumed or default answer, and never ask \
a question in prose and then continue without the reply.

BRIDGE TOOLS (mcp__bridge__*): use these for bridge actions — send_file (deliver \
a file/photo to the user), send_buttons (tappable quick replies; use whenever a \
short menu of likely next actions exists), message_agent (talk to another \
agent/bot — hop-capped and rate-limited; NEVER reply to a bot message just to \
acknowledge or thank: no ping-pong), schedule / unschedule / list_jobs \
(reminders to the user, or future prompts to yourself for follow-ups and \
digests; when formats: "2026-01-31 15:00", "15:00", "+30m", "daily 09:00", \
"weekly mon 09:00", "weekdays 08:30", append " until 2026-12-31" to cap a \
recurrence). Bot messages arrive as "[bot-msg from <name> hop=N]".

Legacy text markers (⟦SEND:path⟧, ⟦BUTTONS:a|b⟧, ⟦TO:agent|msg⟧, \
⟦REMIND:when|text⟧, ⟦SCHEDULE:when|prompt⟧, ⟦UNSCHEDULE:id⟧ — each on its own \
line at the END of the reply) still work, but prefer the tools.

RECEIVING FILES: when the user sends a photo/file/voice note, the bridge saves it \
and tells you the path (voice notes arrive pre-transcribed). Use Read on the path.

MEMORY (mcp__bridge__remember / recall / forget): you carry long-term memory \
across sessions. Anything under "WHAT YOU REMEMBER" below was saved earlier — \
treat it as known. When you learn a durable fact about the user or the work \
(a preference, a decision, an open loop, a contact), save it with `remember`; \
`recall` before asking the user to repeat something; `forget` what's wrong or \
done. Save sparingly and only what's worth carrying — not transient chatter."""

SECRETARY_PROMPT = """
SECRETARY MODE is ON. You are also the user's personal secretary:
- Track their tasks, reminders and follow-ups via the schedule tool.
- When they mention a commitment with a time, proactively offer (via AskUserQuestion
  or ⟦BUTTONS⟧) to set a reminder.
- As decisions, open loops, and contacts come up, save them with the `remember`
  tool so they survive into later sessions; drop them with `forget` once done
  or wrong. `recall` to check what you already know before asking again.
- Be terse and action-oriented; confirm scheduled items with a one-liner."""


@dataclass
class AgentConfig:
    name: str
    workdir: str = WORKDIR
    model: str = MODEL
    soul: Soul = field(default_factory=Soul)
    secretary: bool = False
    auto_approve: bool = True
    tts: bool = False
    voice: str = ""             # TTS voice override ("" = backend default)
    proactive: bool = False
    always_allow: list = field(default_factory=list)

    def to_dict(self):
        return {"workdir": self.workdir, "model": self.model,
                "soul": self.soul.to_dict(), "secretary": self.secretary,
                "auto_approve": self.auto_approve, "tts": self.tts,
                "voice": self.voice, "proactive": self.proactive,
                "always_allow": sorted(self.always_allow)}

    @classmethod
    def from_dict(cls, name, d):
        # migration: an agent saved before the character sheet kept a free-text
        # `persona` string — fold it into the soul's notes so nothing is lost.
        if "soul" in d:
            soul = Soul.from_dict(d["soul"])
        else:
            soul = Soul(notes=d.get("persona", ""))
        return cls(name=name, workdir=d.get("workdir", WORKDIR),
                   model=d.get("model", MODEL), soul=soul,
                   secretary=bool(d.get("secretary")),
                   auto_approve=bool(d.get("auto_approve", True)),
                   tts=bool(d.get("tts")), voice=d.get("voice", ""),
                   proactive=bool(d.get("proactive")),
                   always_allow=list(d.get("always_allow", [])))


@dataclass
class TurnSource:
    kind: str = "user"          # user | bot | sched
    hop: int = 0
    origin: str = ""            # who sent it (bot/peer name)


class AgentSession:
    def __init__(self, mgr, cfg: AgentConfig, skey: str, sid: int,
                 chat_id: int, thread_id: int | None):
        self.mgr = mgr
        self.cfg = cfg
        self.skey = skey
        self.sid = sid                       # short id for callback data
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.outbox = Outbox(mgr.bot, chat_id, thread_id, prefix_fn=self._prefix)
        self.client: ClaudeSDKClient | None = None
        self.connected = False
        self.busy = False
        self.pending: deque[tuple[str, TurnSource]] = deque(maxlen=64)
        self.turn_started = 0.0
        self.turn_source = TurnSource()
        self.session_id: str | None = mgr.session_ids.get(skey)
        self.model = cfg.model
        self.slash_commands: list[str] = []
        self.stderr_tail: deque[str] = deque(maxlen=40)
        self.backoff = Backoff()
        self.mood = Mood()
        self.bot_turn_bucket = TokenBucket(BOT_TURNS_PER_HOUR, 3600.0)
        self.always_allow: set[str] = set(cfg.always_allow)
        # interactive state
        self.questions: dict[int, dict] = {}
        self.qcounter = 0
        self._q_lock = asyncio.Lock()   # one live question at a time
        self.kb_store: dict[int, list[str]] = {}
        self.kbcounter = 0
        self.perms: dict[int, dict] = {}
        self.pcounter = 0
        self._stopping = False
        self._consumer: asyncio.Task | None = None
        self._typing: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._crash_task: asyncio.Task | None = None
        self._last_rl_note = 0.0
        self._last_warn = 0.0
        self._turn_text_streamed = False
        # proactive idle check-ins: stamp of last user activity, an "armed"
        # flag reset whenever the user speaks (one ping per idle stretch), and
        # a per-turn flag set when a check-in chose silence (suppress output).
        self.last_activity = time.monotonic()
        self.proactive_armed = True
        self._proactive_silent = False
        # undo / context / tasks / telegram-native state
        self.turn_user_uuid: str | None = None
        self.turn_files_touched = False
        self.undo_uuids: dict[int, str] = {}
        self.ucounter = 0
        self.ctx_pct: float | None = None
        self.last_user_msg_id: int | None = None
        self.sessions_cache: list = []
        self._task_progress_ts: dict[str, float] = {}
        self._streaming_tools: list[str] = []
        self._turn_had_tools = False
        self._shell_calls: dict[str, bool] = {}   # tool_use_id -> shell? (Bash/PS)

    def _prefix(self) -> str:
        # label output whenever "who is talking" isn't obvious: any group/topic
        # session, or a non-active agent speaking into the private chat. Use the
        # soul's avatar + display name when set (issue #4 persona display).
        if self.chat_id != CHAT_ID or self.cfg.name != self.mgr.active:
            soul = self.cfg.soul
            icon = soul.emoji or "🤖"
            label = soul.display_name or self.cfg.name
            return f"{icon} {label}:\n"
        return ""

    def _react(self, emoji: str):
        """Best-effort status reaction on the user's triggering message
        (👀 working → 👍 done / 😱 error). Telegram only allows a fixed emoji
        set; failures (bad emoji, no permission) are swallowed."""
        mid = self.last_user_msg_id
        if not mid or self.turn_source.kind != "user":
            return
        async def _go():
            try:
                await self.mgr.bot.set_message_reaction(
                    self.chat_id, mid,
                    reaction=[ReactionTypeEmoji(emoji=emoji)] if emoji else [])
            except Exception:
                pass
        asyncio.create_task(_go())

    # -- lifecycle ----------------------------------------------------------- #
    def _options(self, fork: bool = False) -> ClaudeAgentOptions:
        prompt = BRIDGE_PROMPT + f"\n\nYour agent name on this bridge: {self.cfg.name}."
        soul_block = self.cfg.soul.render_prompt()
        if soul_block:
            prompt += f"\n\n{soul_block}"
        mem_block = self.mgr.memory_for(self.cfg.name).render_prompt()
        if mem_block:
            prompt += f"\n\n{mem_block}"
        if self.cfg.secretary:
            prompt += SECRETARY_PROMPT
        return ClaudeAgentOptions(
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": prompt},
            can_use_tool=self._can_use_tool,
            include_partial_messages=True,
            permission_mode="bypassPermissions" if self.cfg.auto_approve else None,
            allowed_tools=list(SAFE_TOOLS) + list(bridgetools.ALLOWED),
            mcp_servers={bridgetools.SERVER_NAME: bridgetools.build_bridge_server(self)},
            hooks=guards.build_hooks(self),
            enable_file_checkpointing=True,
            extra_args={"replay-user-messages": None},
            fork_session=fork,
            model=self.cfg.model or None,
            cwd=self.cfg.workdir,
            resume=self.session_id,
            cli_path=CLAUDE_BIN or None,
            stderr=self.stderr_tail.append,
            # keep the claude subprocess off the (possibly full) system drive;
            # give the init handshake extra headroom so a cold-start claude.exe
            # (freshly extracted after a reboot) doesn't trip the 60s default
            env={"TEMP": str(TMP_DIR), "TMP": str(TMP_DIR),
                 "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT": "180000"},
        )

    def _ensure_workdir(self):
        """A bad cwd makes the CLI die with WinError 267 in a restart loop —
        create it or fall back to the default instead."""
        if os.path.isdir(self.cfg.workdir):
            return
        try:
            os.makedirs(self.cfg.workdir, exist_ok=True)
            self.outbox.emit(f"📁 workdir didn't exist — created {self.cfg.workdir}")
        except OSError:
            self.outbox.emit(f"⚠️ workdir {self.cfg.workdir} is invalid — "
                             f"falling back to {WORKDIR} (/cwd to change)")
            self.cfg.workdir = WORKDIR
            self.mgr.save_agents()

    async def start(self, resume: bool = True, fork: bool = False):
        self._stopping = False
        if not resume:
            self.session_id = None
        self.outbox.start()
        self._ensure_workdir()
        self.client = ClaudeSDKClient(self._options(fork=fork))
        await self.client.connect()
        self.connected = True
        self.busy = False
        self._consumer = asyncio.create_task(self._consume())
        if self._typing is None or self._typing.done():
            self._typing = asyncio.create_task(self._typing_loop())
        if self._watchdog is None or self._watchdog.done():
            self._watchdog = asyncio.create_task(self._watchdog_loop())
        log.info("session %s started (resume=%s)", self.skey, self.session_id)

    async def stop(self):
        self._stopping = True
        self.connected = False
        # unblock anything waiting on the user, or the question lock and the
        # can_use_tool callback would hang forever across a restart
        for st in list(self.questions.values()):
            f = st.get("future")
            if f and not f.done():
                f.set_result("(session stopped before the user answered — "
                             "ask again)")
        for st in list(self.perms.values()):
            f = st.get("future")
            if f and not f.done():
                f.set_result("d")
        for t in (self._consumer, self._typing, self._watchdog, self._crash_task):
            if t:
                t.cancel()
        self._consumer = self._typing = self._watchdog = self._crash_task = None
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self.busy = False

    async def restart(self, resume: bool = True, fork: bool = False,
                      note: str = "♻️ Restarting Claude…"):
        if note:
            self.outbox.emit(note)
        keep = list(self.pending)
        await self.stop()
        try:
            await self.start(resume=resume, fork=fork)
        except Exception as e:
            self.outbox.emit(f"❌ restart failed: {e}")
            return
        self.pending = deque(keep, maxlen=64)
        await self._drain()

    async def interrupt(self):
        if self.client and self.busy:
            try:
                await self.client.interrupt()
                self.outbox.emit("⏹ interrupt sent")
                return
            except Exception as e:
                self.outbox.emit(f"⚠️ in-band interrupt failed ({e}), restarting…")
        await self.restart(resume=True, note="")

    # -- input ----------------------------------------------------------------#
    async def feed(self, text: str, source: TurnSource | None = None,
                   echo: bool = False) -> bool:
        source = source or TurnSource()
        if source.kind == "user":
            # the user is back — reset the idle clock and re-arm proactive
            self.last_activity = time.monotonic()
            self.proactive_armed = True
        if source.kind != "user" and not self.bot_turn_bucket.allow():
            self.outbox.emit(
                f"🚦 dropped {source.kind} turn (rate limit "
                f"{BOT_TURNS_PER_HOUR}/h reached): {text[:120]}")
            return False
        if echo:
            self.outbox.emit(f"▶️ {text}")
        if self.busy:
            self.pending.append((text, source))
            if source.kind == "user":
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                    f"🗑 Clear queue ({len(self.pending)})",
                    callback_data=f"qq:{self.sid}:0")]])
                self.outbox.keyboard(
                    f"⏳ queued (#{len(self.pending)} in line) — "
                    "/interrupt to cut in", kb)
            return True
        await self._send_turn(text, source)
        return True

    async def _send_turn(self, text: str, source: TurnSource):
        if not self.connected:
            try:
                await self.start(resume=True)
            except Exception as e:
                self.outbox.emit(f"❌ can't start Claude: {e}")
                self.pending.appendleft((text, source))
                return
        self.busy = True
        self.turn_started = time.monotonic()
        self.turn_source = source
        self._proactive_silent = False
        self._turn_text_streamed = False
        self.turn_user_uuid = None
        self.turn_files_touched = False
        self._turn_had_tools = False
        # mood: prepend a one-line tone nudge only when the weather has shifted
        # (pop_nudge returns "" if unchanged) so we never spam the turn stream.
        nudge = self.mood.pop_nudge()
        sent = f"[mood — {nudge}]\n{text}" if nudge else text
        self._react("👀")          # acknowledge: working on it
        try:
            await self.client.query(sent)
        except Exception as e:
            self.busy = False
            metrics.bump("send_fail")
            self.outbox.emit(f"⚠️ couldn't send to Claude (message kept in "
                             f"queue): {e} — /restart if this persists")
            self.pending.appendleft((text, source))

    async def _drain(self):
        if self.pending and not self.busy:
            text, source = self.pending.popleft()
            await self._send_turn(text, source)

    # -- output ---------------------------------------------------------------#
    async def _consume(self):
        try:
            async for msg in self.client.receive_messages():
                try:
                    await self._handle(msg)
                except Exception:
                    log.exception("handler error")
        except asyncio.CancelledError:
            return
        except Exception as e:
            if self._stopping:
                return
            log.warning("consume loop died: %s", e)
        if not self._stopping:
            self._crash_task = asyncio.create_task(self._crash_restart())

    async def _crash_restart(self):
        self.connected = False
        self.busy = False
        self.mood.note_restart(crashed=True)   # next turn will be a touch careful
        self.mgr.note_crash()                  # feeds auto-escalation (#8)
        delay, drop_resume = self.backoff.record()
        tail = "\n".join(list(self.stderr_tail)[-3:])
        note = f"⚠️ Claude exited. Restarting in {delay:.0f}s…"
        if drop_resume and self.session_id:
            note += " (repeated crashes — starting a FRESH session)"
            self.session_id = None
            self.mgr.save_session_id(self.skey, None)
        if tail:
            note += f"\n{tail[:300]}"
        self.outbox.emit(note)
        await asyncio.sleep(delay)
        if self._stopping:
            return
        try:
            await self.start(resume=self.session_id is not None)
        except Exception as e:
            self.outbox.emit(f"❌ restart failed: {e} — /restart to retry")
            return
        if self.pending:
            self.outbox.emit(f"🔁 re-sending {len(self.pending)} queued message(s)")
        await self._drain()

    async def _handle(self, msg):
        if isinstance(msg, TaskStartedMessage):
            self.outbox.emit(f"⚙️ background task started: {msg.description[:120]}")
            return
        if isinstance(msg, TaskProgressMessage):
            now = time.monotonic()
            if now - self._task_progress_ts.get(msg.task_id, 0) > 60:
                self._task_progress_ts[msg.task_id] = now
                u = msg.usage or {}
                self.outbox.emit(
                    f"⚙️ {msg.description[:80]} · {u.get('total_tokens', 0) // 1000}k tok"
                    + (f" · {msg.last_tool_name}" if msg.last_tool_name else ""))
            return
        if isinstance(msg, TaskNotificationMessage):
            self._task_progress_ts.pop(msg.task_id, None)
            icon = {"completed": "✅", "stopped": "⏹", "failed": "❌"}.get(msg.status, "ℹ️")
            self.outbox.emit(f"{icon} background task {msg.status}: {msg.summary[:300]}")
            return

        if isinstance(msg, SystemMessage):
            if msg.subtype == "init":
                data = msg.data
                sid = data.get("session_id")
                if sid:
                    self.session_id = sid
                    self.mgr.save_session_id(self.skey, sid)
                self.slash_commands = data.get("slash_commands") or []
                self.model = data.get("model", self.model)
                self.backoff.reset()
            return

        if isinstance(msg, StreamEvent):
            if msg.parent_tool_use_id:
                return
            ev = msg.event
            ev_type = ev.get("type")
            if ev_type == "content_block_start":
                block = ev.get("content_block", {})
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    if name and name not in ("AskUserQuestion", "TodoWrite") \
                            and not name.startswith(f"mcp__{bridgetools.SERVER_NAME}__"):
                        if self._turn_text_streamed:
                            self.outbox.stream_close(None)
                            self._turn_text_streamed = False
                        self._streaming_tools.append(name)
                        self._turn_had_tools = True
                        self.outbox.emit(f"{tool_icon(name)} **{name}**…")
            elif ev_type == "content_block_delta":
                delta = ev.get("delta", {})
                if delta.get("type") == "text_delta":
                    # don't live-stream a proactive check-in: it might decline
                    # with the silence sentinel, which we suppress wholesale at
                    # AssistantMessage time. Buffer it instead of flashing it.
                    if self.turn_source.kind == "proactive":
                        return
                    self._turn_text_streamed = True
                    self.outbox.stream_delta(delta.get("text", ""))
            return

        if isinstance(msg, AssistantMessage):
            if msg.parent_tool_use_id:
                return
            texts, tools = [], []
            pre_shown = list(self._streaming_tools)
            self._streaming_tools.clear()
            for block in msg.content:
                if isinstance(block, TextBlock):
                    texts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    if block.name == "AskUserQuestion":
                        # approvals mode: handled synchronously in _can_use_tool.
                        # bypass mode: the tool auto-errors, so render buttons
                        # here and feed the tap back as the next user turn.
                        if self.cfg.auto_approve:
                            asyncio.create_task(
                                self._legacy_question(block.input))
                        continue
                    if block.name in FILE_TOOLS:
                        self.turn_files_touched = True
                    if block.name.startswith(f"mcp__{bridgetools.SERVER_NAME}__"):
                        continue  # bridge tools narrate themselves
                    if block.name in ("Bash", "PowerShell"):
                        self._shell_calls[block.id] = True
                        if len(self._shell_calls) > 50:
                            self._shell_calls.pop(next(iter(self._shell_calls)))
                    summ = summarize_tool(block.name, block.input)
                    # Skip re-emitting if live banner already showed this tool
                    # and there's no richer summary to add
                    if block.name in pre_shown and not summ:
                        pre_shown.remove(block.name)
                        continue
                    tools.append((block.name, summ))
            full = "\n".join(t for t in texts if t)
            parsed = markers.parse(full)
            # proactive check-in that chose silence: drop the whole turn quietly
            if self.turn_source.kind == "proactive":
                from .proactive import declined
                if declined(parsed.text):
                    self._proactive_silent = True
                    return
                # it has something to say — mark it as a spontaneous thought
                parsed.text = "💭 " + parsed.text
            markup = self._kb_markup(parsed.buttons) if parsed.buttons else None
            if self._turn_text_streamed:
                self.outbox.stream_close(parsed.text, markup)
                self._turn_text_streamed = False
            elif parsed.text:
                if markup:
                    self.outbox.keyboard(parsed.text, markup)
                else:
                    self.outbox.emit(parsed.text)
            elif markup:
                self.outbox.keyboard("➡️", markup)
            await self.mgr.handle_markers(self, parsed)
            if tools:
                self._turn_had_tools = True
            for line in format_tool_lines(tools):
                self.outbox.emit(line)
            if self.cfg.tts and parsed.text:
                asyncio.create_task(self._speak(parsed.text))
            return

        if isinstance(msg, UserMessage):
            if msg.uuid and not msg.parent_tool_use_id and \
                    self.busy and self.turn_user_uuid is None:
                self.turn_user_uuid = msg.uuid   # checkpoint anchor for undo
            content = msg.content
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, ToolResultBlock):
                        continue
                    c = block.content
                    if isinstance(c, list):
                        c = " ".join(x.get("text", "") for x in c
                                     if isinstance(x, dict))
                    if block.is_error:
                        self.outbox.emit(format_error(str(c)))
                    elif self._shell_calls.pop(block.tool_use_id, False):
                        out = format_output(str(c))
                        if out:
                            self.outbox.emit(out)
            return

        if isinstance(msg, ResultMessage):
            self.busy = False
            self.backoff.reset()
            self.mood.note_result(bool(msg.is_error))   # update emotional weather
            # a proactive check-in that chose silence leaves no footer either —
            # the whole turn stays invisible. Still account for its cost.
            if self._proactive_silent:
                self._proactive_silent = False
                self.mgr.add_cost(msg.total_cost_usd or 0.0)
                await self._drain()
                return
            self._react("😱" if msg.is_error else "👍")   # done
            dur = time.monotonic() - self.turn_started
            today, budget_alert = self.mgr.add_cost(msg.total_cost_usd or 0.0)
            foot = f"✅ {fmt_duration(dur)}"
            if self.model:
                foot += f" · {_pretty_model(self.model)}"
            if msg.total_cost_usd:
                foot += (f" · {_cost_emoji(msg.total_cost_usd)} "
                         f"${msg.total_cost_usd:.4f} · today ${today:.2f}")
            u = msg.usage or {}
            t_in = (u.get("input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0)
                    + u.get("cache_read_input_tokens", 0))
            t_out = u.get("output_tokens", 0)
            if t_in or t_out:
                foot += f" · 📊 {_tok(t_in)}→{_tok(t_out)} tok"
                cached = u.get("cache_read_input_tokens", 0)
                if cached:
                    foot += f" ({_tok(cached)} cached)"
            if msg.subtype and msg.subtype != "success":
                foot += f" · {msg.subtype}"
            if msg.is_error:
                foot = f"❌ {foot} · {str(msg.result or '')[:300]}"

            await self._refresh_context_pct()
            buttons = []
            if self.ctx_pct is not None and self.ctx_pct >= CONTEXT_WARN_PCT:
                foot += f" · ⚠️ {_ctx_bar(self.ctx_pct)} {self.ctx_pct:.0f}%"
                buttons.append(InlineKeyboardButton(
                    "🗜 Compact now", callback_data="send:/compact"))
            elif self.ctx_pct is not None and self.ctx_pct >= 40:
                foot += f" · {_ctx_bar(self.ctx_pct)} {self.ctx_pct:.0f}%"
            if self.turn_files_touched and self.turn_user_uuid:
                self.ucounter += 1
                self.undo_uuids[self.ucounter] = self.turn_user_uuid
                while len(self.undo_uuids) > 10:
                    self.undo_uuids.pop(min(self.undo_uuids), None)
                buttons.append(InlineKeyboardButton(
                    "↩️ Undo file edits",
                    callback_data=f"ud:{self.sid}:{self.ucounter}"))
            if budget_alert:
                foot += f"\n{budget_alert}"
            if self._turn_had_tools:
                foot = SEP + "\n" + foot
            if buttons:
                self.outbox.keyboard(foot, InlineKeyboardMarkup([buttons]))
            else:
                self.outbox.emit(foot)
            await self._drain()
            return

        if isinstance(msg, RateLimitEvent):
            info = msg.rate_limit_info
            if info.status and info.status != "allowed":
                now = time.monotonic()
                if now - self._last_rl_note > 300:
                    self._last_rl_note = now
                    self.outbox.emit(f"🚦 API rate limit: {info.status}"
                                     + (f" (resets {info.resets_at})" if info.resets_at else ""))
            return

    # -- helpers ----------------------------------------------------------------#
    async def _refresh_context_pct(self):
        if not (self.client and self.connected):
            return
        try:
            usage = await asyncio.wait_for(self.client.get_context_usage(), timeout=10)
            self.ctx_pct = float(usage.get("percentage", 0.0))
        except Exception:
            pass

    async def _speak(self, text: str):
        try:
            res = await voice.synthesize(text, self.cfg.voice)
            if res:
                self.outbox.voice(res[0], res[1])
            else:
                self.cfg.tts = False
                self.mgr.save_agents()
                self.outbox.emit("🔇 TTS failed (no backend, or it errored — "
                                 "see /logs) — turned off; /tts on to retry")
        except OSError as e:
            metrics.bump("tts_fail")
            if e.errno == 28:   # disk full — stop trying until re-enabled
                self.cfg.tts = False
                self.mgr.save_agents()
                self.outbox.emit("🔇 TTS disabled: disk full. Free space and /tts on")
            log.warning("tts failed: %s", e)
        except Exception as e:
            metrics.bump("tts_fail")
            log.warning("tts failed: %s", e)

    async def guard_approve(self, tool: str, tool_input: dict, matched: str) -> bool:
        """Dangerous-command tap-to-approve, used by the PreToolUse hook even
        when auto-approve is on."""
        self.pcounter += 1
        pid = self.pcounter
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.perms[pid] = {"future": fut, "tool": tool}
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Run it", callback_data=f"pm:{self.sid}:{pid}:a"),
            InlineKeyboardButton("⛔ Deny", callback_data=f"pm:{self.sid}:{pid}:d"),
        ]])
        cmd = (tool_input.get("command") or "")[:600]
        self.outbox.keyboard(
            f"🔴 **Dangerous command** · matched `{matched}`\n"
            f"```bash\n{cmd}\n```", kb)
        try:
            verdict = await asyncio.wait_for(fut, timeout=PERMISSION_TIMEOUT)
        except asyncio.TimeoutError:
            verdict = "d"
            self.outbox.emit(f"⌛ no answer in {PERMISSION_TIMEOUT // 60}m — denied")
        finally:
            self.perms.pop(pid, None)
        return verdict in ("a", "s")

    async def undo(self, n: int) -> str:
        uuid = self.undo_uuids.get(n)
        if not uuid:
            return "⚠️ nothing to undo (checkpoint expired)"
        if not (self.client and self.connected):
            return "⚠️ session not connected"
        try:
            await self.client.rewind_files(uuid)
            self.undo_uuids.pop(n, None)
            return "↩️ files rewound to before that turn"
        except Exception as e:
            return f"⚠️ undo failed: {e}"

    # -- permissions / questions ----------------------------------------------#
    async def _can_use_tool(self, tool_name: str, tool_input: dict, ctx):
        if tool_name == "AskUserQuestion":
            answer = await self._ask_question(tool_input)
            return PermissionResultDeny(
                message=f"[bridge] user answered: {answer}. This denial is the "
                        "transport for the answer — continue, don't re-ask.")
        if self.cfg.auto_approve or tool_name in self.always_allow:
            return PermissionResultAllow()
        self.pcounter += 1
        pid = self.pcounter
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.perms[pid] = {"future": fut, "tool": tool_name}
        summ = summarize_tool(tool_name, tool_input)
        agent_note = " (subagent)" if getattr(ctx, "agent_id", None) else ""
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Allow", callback_data=f"pm:{self.sid}:{pid}:a"),
            InlineKeyboardButton("🔁 Always", callback_data=f"pm:{self.sid}:{pid}:s"),
            InlineKeyboardButton("⛔ Deny", callback_data=f"pm:{self.sid}:{pid}:d"),
        ]])
        if tool_name in ("Bash", "PowerShell") and summ:
            subj = f"\n```bash\n{summ[:500]}\n```"
        elif summ:
            subj = f"\n`{summ[:300]}`"
        else:
            subj = ""
        self.outbox.keyboard(
            f"🔐 **Permission needed**{agent_note}\n"
            f"{tool_icon(tool_name)} **{tool_name}**{subj}", kb)
        try:
            verdict = await asyncio.wait_for(fut, timeout=PERMISSION_TIMEOUT)
        except asyncio.TimeoutError:
            verdict = "d"
            self.outbox.emit(f"⌛ no answer in {PERMISSION_TIMEOUT // 60}m — denied {tool_name}")
        finally:
            self.perms.pop(pid, None)
        if verdict == "s":
            self.always_allow.add(tool_name)
            if tool_name not in self.cfg.always_allow:
                self.cfg.always_allow.append(tool_name)
                self.mgr.save_agents()
        if verdict in ("a", "s"):
            return PermissionResultAllow()
        return PermissionResultDeny(
            message="[bridge] user denied this action via Telegram.")

    def resolve_perm(self, pid: int, verdict: str) -> str | None:
        st = self.perms.get(pid)
        if not st or st["future"].done():
            return None
        st["future"].set_result(verdict)
        return st["tool"]

    async def _legacy_question(self, tool_input: dict):
        """Bypass-permissions path: buttons now, answer fed as the next turn."""
        ans = await self._ask_question(tool_input)
        await self.feed(ans)

    async def _ask_question(self, tool_input: dict) -> str:
        """Render question(s) as buttons and wait for the user's answer —
        indefinitely (a question is never answered on the user's behalf), and
        strictly one question on screen at a time."""
        answers = []
        if self._q_lock.locked():
            self.outbox.emit("❓ one question at a time — answer the pending "
                             "question above first")
        async with self._q_lock:
            for question in tool_input.get("questions", []):
                self.qcounter += 1
                qid = self.qcounter
                fut: asyncio.Future = asyncio.get_running_loop().create_future()
                st = {
                    "q": question.get("question") or question.get("header") or "?",
                    "opts": [o.get("label", "?") for o in question.get("options", [])],
                    "descs": [o.get("description", "") for o in question.get("options", [])],
                    "multi": bool(question.get("multiSelect")),
                    "selected": set(), "future": fut, "message_id": None,
                }
                self.questions[qid] = st
                lines = [f"❓ {st['q']}"]
                for lab, desc in zip(st["opts"], st["descs"]):
                    if desc:
                        lines.append(f"• {lab} — {desc}")

                async def _remember(msg, _st=st):
                    _st["message_id"] = msg.message_id

                self.outbox.keyboard("\n".join(lines), self.question_kb(qid),
                                     _remember)
                try:
                    ans = await fut
                finally:
                    self.questions.pop(qid, None)
                answers.append(f"{st['q']} -> {ans}")
        return " | ".join(answers) if answers else "(no questions)"

    def question_kb(self, qid: int) -> InlineKeyboardMarkup:
        st = self.questions[qid]
        rows = []
        for i, lab in enumerate(st["opts"]):
            if st["multi"]:
                mark = "☑ " if i in st["selected"] else "☐ "
                cb = f"qt:{self.sid}:{qid}:{i}"
            else:
                mark = ""
                cb = f"qp:{self.sid}:{qid}:{i}"
            rows.append([InlineKeyboardButton((mark + lab)[:64], callback_data=cb)])
        rows.append([InlineKeyboardButton("✏️ Type my own", callback_data=f"qo:{self.sid}:{qid}")])
        if st["multi"]:
            rows.append([InlineKeyboardButton("✅ Done", callback_data=f"qd:{self.sid}:{qid}")])
        return InlineKeyboardMarkup(rows)

    def _kb_markup(self, labels: list[str]) -> InlineKeyboardMarkup:
        self.kbcounter += 1
        bid = self.kbcounter
        self.kb_store[bid] = labels
        while len(self.kb_store) > 40:
            self.kb_store.pop(min(self.kb_store), None)
        rows, row = [], []
        for i, lab in enumerate(labels):
            row.append(InlineKeyboardButton(lab[:48], callback_data=f"bt:{self.sid}:{bid}:{i}"))
            if len(row) == 2 or len(lab) > 24:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return InlineKeyboardMarkup(rows)

    # -- background loops -------------------------------------------------------#
    async def _typing_loop(self):
        while True:
            try:
                if self.busy:
                    await self.mgr.bot.send_chat_action(
                        self.chat_id, ChatAction.TYPING,
                        message_thread_id=self.thread_id)
                    await asyncio.sleep(4.5)
                else:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(5)

    async def _watchdog_loop(self):
        while True:
            try:
                await asyncio.sleep(30)
                if not self.busy:
                    continue
                elapsed = time.monotonic() - self.turn_started
                if elapsed > TURN_WARN_SECONDS and \
                        time.monotonic() - self._last_warn > 300:
                    self._last_warn = time.monotonic()
                    if self.questions:
                        self.outbox.emit("❓ Claude is waiting for your answer "
                                         "to the question above ☝️")
                        continue
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                        "⏹ Interrupt", callback_data="act:interrupt")]])
                    self.outbox.keyboard(
                        f"⏳ turn still running ({fmt_duration(elapsed)}). "
                        "Tool calls keep streaming above; interrupt if it's stuck.", kb)
            except asyncio.CancelledError:
                return
            except Exception:
                pass

    def status_line(self) -> str:
        state = "🟢" if self.connected else "🔴"
        busy = (f"⏳ busy {fmt_duration(time.monotonic() - self.turn_started)}"
                if self.busy else "💤 idle")
        head = (f"{state} {self.cfg.name} · 🧠 {_pretty_model(self.model) or 'default'}"
                f" · {'auto✅' if self.cfg.auto_approve else 'approvals🔐'}")
        badges = []
        if self.cfg.secretary:
            badges.append("📋 secretary")
        if self.cfg.tts:
            badges.append("🔊 tts")
        if self.pending:
            badges.append(f"📥 {len(self.pending)} queued")
        det = f"   {busy}"
        if self.ctx_pct is not None:
            det += f" · {_ctx_bar(self.ctx_pct)} {self.ctx_pct:.0f}%"
        if badges:
            det += " · " + " · ".join(badges)
        return head + "\n" + det
