"""Telegram handlers: bridge commands, callbacks, media, voice."""

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from . import metrics, voice
from .config import (APP_LOG_FILE, AUDIT_FILE, GROUP_ID, INBOX, MAX_JOBS,
                     PEERS, authorized_chat)
from .manager import AgentManager
from .fmt import SEP
from .session import TurnSource, _ctx_bar, _pretty_model

log = logging.getLogger("bridge.handlers")

AGENT_NAME_RE = re.compile(r"^[a-z0-9_-]{1,20}$")
CURATED_CMDS = ["clear", "compact", "context", "usage", "cost", "review",
                "security-review", "init", "run", "code-review", "simplify"]


def mgr(ctx: ContextTypes.DEFAULT_TYPE) -> AgentManager:
    return ctx.application.bot_data["mgr"]


def _route(update: Update) -> tuple[int, int | None]:
    msg = update.effective_message
    return update.effective_chat.id, msg.message_thread_id if msg else None


async def _session(update: Update, ctx):
    chat_id, thread_id = _route(update)
    return await mgr(ctx).session_for_route(chat_id, thread_id)


# --------------------------------------------------------------------------- #
# Keyboards
# --------------------------------------------------------------------------- #
def panel_kb(s) -> InlineKeyboardMarkup:
    model_label = f"🧠 {s.model or 'default'}"
    auto_label = "🔓 auto-approve" if s.cfg.auto_approve else "🔐 tap-to-approve"
    sec_label = "📋 secretary ●" if s.cfg.secretary else "📋 secretary"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏹ Interrupt", callback_data="act:interrupt"),
         InlineKeyboardButton("♻️ Restart", callback_data="act:restart")],
        [InlineKeyboardButton("🆕 Clear", callback_data="send:/clear"),
         InlineKeyboardButton("🗜 Compact", callback_data="send:/compact"),
         InlineKeyboardButton("📊 Context", callback_data="send:/context")],
        [InlineKeyboardButton(model_label, callback_data="menu:model"),
         InlineKeyboardButton("🤖 Agents", callback_data="menu:agents"),
         InlineKeyboardButton("⏰ Jobs", callback_data="menu:jobs")],
        [InlineKeyboardButton(auto_label, callback_data="act:auto"),
         InlineKeyboardButton(sec_label, callback_data="act:secretary")],
        [InlineKeyboardButton("📈 Usage", callback_data="send:/usage"),
         InlineKeyboardButton("📋 Commands", callback_data="menu:cmds"),
         InlineKeyboardButton("📂 Status", callback_data="act:status")],
    ])


def model_kb(current: str) -> InlineKeyboardMarkup:
    def lbl(name, val):
        return ("● " if current == val else "") + name
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl("Opus", "opus"), callback_data="model:opus"),
         InlineKeyboardButton(lbl("Sonnet", "sonnet"), callback_data="model:sonnet"),
         InlineKeyboardButton(lbl("Haiku", "haiku"), callback_data="model:haiku")],
        [InlineKeyboardButton(lbl("Default", ""), callback_data="model:"),
         InlineKeyboardButton("⬅ Back", callback_data="menu:back")],
    ])


def cmds_kb(s) -> InlineKeyboardMarkup:
    live = s.slash_commands or CURATED_CMDS
    names = [c for c in CURATED_CMDS if c in live] or CURATED_CMDS
    rows, row = [], []
    for c in names:
        row.append(InlineKeyboardButton(f"/{c}", callback_data=f"send:/{c}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


def agents_kb(m: AgentManager) -> InlineKeyboardMarkup:
    rows = []
    for name in sorted(m.agents):
        mark = "● " if name == m.active else ""
        rows.append([
            InlineKeyboardButton(f"{mark}{name}", callback_data=f"ags:{name}"),
            InlineKeyboardButton("♻️", callback_data=f"agr:{name}"),
            InlineKeyboardButton("🗑", callback_data=f"agx:{name}"),
        ])
    rows.append([InlineKeyboardButton("➕ new agent", callback_data="agn"),
                 InlineKeyboardButton("⬅ Back", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)


def jobs_kb(m: AgentManager) -> tuple[str, InlineKeyboardMarkup]:
    jobs = m.scheduler.list_jobs() if m.scheduler else []
    if not jobs:
        return ("⏰ no scheduled jobs",
                InlineKeyboardMarkup([[InlineKeyboardButton(
                    "⬅ Back", callback_data="menu:back")]]))
    lines, rows = [f"⏰ jobs ({len(jobs)}/{MAX_JOBS}) — tap to cancel:"], []
    for j in jobs[:25]:
        lines.append(f"#{j['id']} {j['kind']} @ {j['next_human']}"
                     + (f" ({j['recur']})" if j.get("recur") else "")
                     + f" [{j['agent']}]: {j['text'][:60]}")
        rows.append([InlineKeyboardButton(f"❌ #{j['id']} {j['text'][:30]}",
                                          callback_data=f"jbd:{j['id']}")])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="menu:back")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _status_text(m: AgentManager) -> str:
    lines = ["📂 𝗕𝗿𝗶𝗱𝗴𝗲 𝗦𝘁𝗮𝘁𝘂𝘀", SEP]
    if m.sessions:
        lines += [s.status_line() for s in m.sessions.values()]
    else:
        lines.append("(no live sessions)")
    lines.append(SEP)
    lines.append(f"⭐ active: {m.active}")
    lines.append(f"💰 today ${m.today_cost():.2f} · month ${m.month_cost():.2f}")
    if m.scheduler and m.scheduler.jobs:
        lines.append(f"⏰ {len(m.scheduler.jobs)} jobs (/jobs)")
    if PEERS:
        lines.append("🔌 peers: " + ", ".join(PEERS))
    counters = metrics.summary()
    if counters:
        lines.append(f"📊 {counters}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, ctx):
    s = await _session(update, ctx)
    await update.message.reply_text(
        "🤖 𝗖𝗹𝗮𝘂𝗱𝗲 𝗕𝗿𝗶𝗱𝗴𝗲 𝗼𝗻𝗹𝗶𝗻𝗲\n"
        f"{SEP}\n"
        f"⚡ agent: {s.cfg.name}\n"
        f"🧠 model: {_pretty_model(s.model) or 'default'}\n"
        f"📂 cwd: {s.cfg.workdir}\n"
        f"{SEP}\n"
        "Type anything to talk to Claude; /slashcommands pass straight through.\n\n"
        "🎛 Bridge: /panel /status /agents /jobs /remind /sessions /fork\n"
        "          /restart /interrupt /kill\n"
        "👤 Per-agent: /auto /secretary /tts /cwd /bind /newagent /delagent\n\n"
        "💡 React 👎 to interrupt · edit your last message to correct it.",
        reply_markup=panel_kb(s))


async def cmd_panel(update: Update, ctx):
    s = await _session(update, ctx)
    m = mgr(ctx)
    ctx_part = (f" · {_ctx_bar(s.ctx_pct)} {s.ctx_pct:.0f}%"
                if s.ctx_pct is not None else "")
    tts_part = " · 🔊" if s.cfg.tts else ""
    mood_part = f" · {s.mood.label()}" if s.cfg.soul.is_set() else ""
    header = (f"⚡ {s.cfg.name} · 🧠 {_pretty_model(s.model) or 'default'} · "
              f"💰 ${m.today_cost():.2f} today{ctx_part}{tts_part}{mood_part}")
    await update.message.reply_text(header, reply_markup=panel_kb(s))


async def cmd_status(update: Update, ctx):
    s = await _session(update, ctx)
    txt = _status_text(mgr(ctx))
    if s.stderr_tail:
        txt += "\n\nstderr tail:\n" + "\n".join(list(s.stderr_tail)[-5:])
    await update.message.reply_text(txt[:4000], reply_markup=panel_kb(s))


async def cmd_restart(update: Update, ctx):
    await (await _session(update, ctx)).restart(resume=True)


async def cmd_interrupt(update: Update, ctx):
    await (await _session(update, ctx)).interrupt()


async def cmd_stop(update: Update, ctx):
    await (await _session(update, ctx)).interrupt()


async def cmd_kill(update: Update, ctx):
    s = await _session(update, ctx)
    await s.stop()
    await update.message.reply_text("🔴 stopped. /restart to bring it back.")


async def cmd_agents(update: Update, ctx):
    await update.message.reply_text(
        "Agents (● = active for the private chat):",
        reply_markup=agents_kb(mgr(ctx)))


async def cmd_newagent(update: Update, ctx):
    m = mgr(ctx)
    args = ctx.args or []
    if not args or not AGENT_NAME_RE.match(args[0]):
        await update.message.reply_text(
            "usage: /newagent <name> [workdir]\n"
            "name: a-z 0-9 _ - (max 20). Example: /newagent docs D:\\Projects\\docs")
        return
    name = args[0]
    if name in m.agents:
        await update.message.reply_text(f"agent {name} already exists")
        return
    from .session import AgentConfig
    cfg = AgentConfig(name=name)
    if len(args) > 1:
        cfg.workdir = " ".join(args[1:])
        if not _valid_dir(cfg.workdir):
            await update.message.reply_text(
                f"⚠️ can't use {cfg.workdir} (not creatable) — agent not created")
            return
    m.agents[name] = cfg
    m.save_agents()
    await update.message.reply_text(
        f"✅ agent {name} created (cwd {cfg.workdir}).\n"
        f"Switch to it via /agents, bind a topic with /bind {name}, "
        f"or message it from another agent with ⟦TO:{name}|…⟧.",
        reply_markup=agents_kb(m))


async def cmd_delagent(update: Update, ctx):
    m = mgr(ctx)
    name = (ctx.args or [""])[0]
    if name == "main" or name not in m.agents:
        await update.message.reply_text("can't delete that (unknown or 'main')")
        return
    await m.remove_agent(name)
    await update.message.reply_text(f"🗑 agent {name} removed",
                                    reply_markup=agents_kb(m))


async def _toggle(update, ctx, attr: str, on_note: str, off_note: str):
    s = await _session(update, ctx)
    arg = (ctx.args or [""])[0].lower()
    cur = getattr(s.cfg, attr)
    val = True if arg in ("on", "1", "true") else False if arg in ("off", "0") else not cur
    setattr(s.cfg, attr, val)
    mgr(ctx).save_agents()
    return s, val, on_note if val else off_note


async def cmd_auto(update: Update, ctx):
    s, val, note = await _toggle(update, ctx, "auto_approve",
                                 "🔓 auto-approve ON (no permission prompts)",
                                 "🔐 approvals ON — risky tools need a tap")
    if s.client and s.connected:
        try:
            await s.client.set_permission_mode(
                "bypassPermissions" if val else "default")
        except Exception as e:
            note += f"\n(applies after /restart: {e})"
    await update.message.reply_text(note)


async def cmd_secretary(update: Update, ctx):
    s, val, note = await _toggle(update, ctx, "secretary",
                                 "📋 secretary mode ON",
                                 "secretary mode off")
    await update.message.reply_text(note + " — restarting session to apply…")
    await s.restart(resume=True, note="")


def _valid_dir(path: str) -> bool:
    import os
    if os.path.isdir(path):
        return True
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


async def cmd_cwd(update: Update, ctx):
    s = await _session(update, ctx)
    if not ctx.args:
        await update.message.reply_text(f"cwd: {s.cfg.workdir}\nusage: /cwd <path>")
        return
    new = " ".join(ctx.args)
    if not _valid_dir(new):
        await update.message.reply_text(f"⚠️ {new} doesn't exist and can't be created")
        return
    s.cfg.workdir = new
    mgr(ctx).save_agents()
    await update.message.reply_text(
        f"📁 cwd → {s.cfg.workdir} — restarting session…")
    await s.restart(resume=True, note="")


async def cmd_bind(update: Update, ctx):
    m = mgr(ctx)
    chat_id, thread_id = _route(update)
    if chat_id != GROUP_ID:
        await update.message.reply_text(
            "/bind works inside a forum topic (set BRIDGE_GROUP_ID and message "
            "the bot from a topic).")
        return
    name = (ctx.args or [""])[0]
    if name not in m.agents:
        await update.message.reply_text(
            f"unknown agent. existing: {', '.join(sorted(m.agents))}")
        return
    m.topics[str(thread_id or 0)] = name
    m.save_topics()
    await update.message.reply_text(
        f"🔗 this topic now talks to agent {name} (fresh session on next message)")


async def cmd_proactive(update: Update, ctx):
    from .config import (PROACTIVE_IDLE_HOURS, PROACTIVE_QUIET_END,
                         PROACTIVE_QUIET_START)
    s, val, note = await _toggle(update, ctx, "proactive",
                                 "💭 proactive check-ins ON", "proactive off")
    if val:
        note += (f"\nAfter ~{PROACTIVE_IDLE_HOURS:g}h idle I'll skim our chat "
                 f"and nudge you if something's open — staying quiet "
                 f"{PROACTIVE_QUIET_START:02d}:00–{PROACTIVE_QUIET_END:02d}:00.")
    await update.message.reply_text(note)


async def cmd_soul(update: Update, ctx):
    """View or edit an agent's character sheet (the structured persona)."""
    from .soul import PRESETS
    s = await _session(update, ctx)
    soul = s.cfg.soul
    args = ctx.args or []
    sub = args[0].lower() if args else ""

    if not sub or sub == "show":
        await update.message.reply_text(
            soul.render_card() + f"\n\ncurrent mood: {s.mood.label()}\n\n"
            "edit: /soul set <field> <value> · /soul add values|quirks <text>\n"
            "fields: display_name emoji role tone notes\n"
            f"presets: {', '.join(PRESETS)} (/soul preset <name>)")
        return

    if sub == "preset":
        name = (args[1] if len(args) > 1 else "").lower()
        if name not in PRESETS:
            await update.message.reply_text(
                f"unknown preset. available: {', '.join(PRESETS)}")
            return
        from .soul import Soul
        s.cfg.soul = Soul.from_dict(PRESETS[name].to_dict())
        mgr(ctx).save_agents()
        await update.message.reply_text(
            f"🎭 loaded preset “{name}”:\n\n{s.cfg.soul.render_card()}\n\n"
            "restart the session to apply in-character (/restart).")
        return

    if sub == "clear":
        from .soul import Soul
        s.cfg.soul = Soul()
        mgr(ctx).save_agents()
        await update.message.reply_text("🎭 character cleared — plain voice. "
                                        "/restart to apply.")
        return

    if sub == "set" and len(args) >= 3:
        field_name = args[1].lower()
        if field_name not in soul.EDITABLE:
            await update.message.reply_text(
                f"can't set “{field_name}”. settable: {', '.join(soul.EDITABLE)}")
            return
        value = " ".join(args[2:])
        setattr(soul, field_name, value)
        mgr(ctx).save_agents()
        await update.message.reply_text(
            f"🎭 {field_name} → {value}\n/restart to apply.")
        return

    if sub == "add" and len(args) >= 3:
        field_name = args[1].lower()
        if field_name not in soul.LIST_FIELDS:
            await update.message.reply_text(
                f"can't add to “{field_name}”. list fields: "
                f"{', '.join(soul.LIST_FIELDS)}")
            return
        getattr(soul, field_name).append(" ".join(args[2:]))
        mgr(ctx).save_agents()
        await update.message.reply_text(
            f"🎭 added to {field_name}.\n{soul.render_card()}\n/restart to apply.")
        return

    await update.message.reply_text(
        "usage: /soul · /soul preset <name> · /soul set <field> <value> · "
        "/soul add values|quirks <text> · /soul clear")


async def cmd_remember(update: Update, ctx):
    """Pin a fact the agent should carry across sessions (issue #12)."""
    s = await _session(update, ctx)
    text = " ".join(ctx.args or []).strip()
    if not text:
        await update.message.reply_text(
            "usage: /remember <text> — pins something I'll recall in every "
            "future session. /memory to list, /forget to drop.")
        return
    mem = mgr(ctx).memory_for(s.cfg.name)
    mem.add(text, kind="pinned")
    mgr(ctx).save_memory()
    await update.message.reply_text(
        f"📌 remembered. I'll carry this into future sessions.\n“{text[:200]}”")


async def cmd_forget(update: Update, ctx):
    """Drop a remembered item by number or text (issue #15)."""
    s = await _session(update, ctx)
    ref = " ".join(ctx.args or []).strip()
    mem = mgr(ctx).memory_for(s.cfg.name)
    if not ref:
        await update.message.reply_text(
            mem.render_list() + "\n\nforget with: /forget <number|text>")
        return
    removed = mem.remove(ref)
    if removed is None:
        await update.message.reply_text(f"🤔 nothing matched “{ref}”. /memory to list.")
        return
    mgr(ctx).save_memory()
    await update.message.reply_text(f"🗑 forgotten: “{removed[:200]}”")


async def cmd_memory(update: Update, ctx):
    """List everything the agent remembers (issues #12/#14)."""
    s = await _session(update, ctx)
    await update.message.reply_text(mgr(ctx).memory_for(s.cfg.name).render_list())


def voice_kb(current: str, names: list) -> InlineKeyboardMarkup:
    rows, row = [], []
    for v in names:
        mark = "● " if v == current else ""
        short = v.replace("Neural", "").replace("-", " ") if "Neural" in v else v
        row.append(InlineKeyboardButton(f"{mark}{short}", callback_data=f"voi:{v}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔄 default", callback_data="voi:")])
    return InlineKeyboardMarkup(rows)


async def cmd_voice(update: Update, ctx):
    """Pick the TTS voice for this agent (issue #3 voice picker)."""
    s = await _session(update, ctx)
    backend, names = voice.list_voices()
    if not backend:
        await update.message.reply_text(
            "🔇 no TTS backend — set OPENAI_API_KEY or `pip install edge-tts`")
        return
    arg = " ".join(ctx.args or []).strip()
    if arg:
        if arg not in names:
            await update.message.reply_text(
                f"unknown voice for {backend}. options: {', '.join(names)}")
            return
        s.cfg.voice = arg
        mgr(ctx).save_agents()
        await update.message.reply_text(f"🔊 voice → {arg}")
        return
    cur = s.cfg.voice or voice.default_voice(backend)
    await update.message.reply_text(
        f"🔊 pick a voice ({backend}) — current: {cur}",
        reply_markup=voice_kb(s.cfg.voice, names))


async def cmd_tts(update: Update, ctx):
    s, val, note = await _toggle(update, ctx, "tts",
                                 "🔊 voice replies ON", "🔇 voice replies off")
    if val and not voice.tts_available():
        note += ("\n⚠️ no TTS backend found — set OPENAI_API_KEY or "
                 "`pip install edge-tts`")
    await update.message.reply_text(note)


def _session_date(workdir: str, info) -> str:
    """Last-activity date for a session: SDK metadata if present, else the
    transcript file's mtime."""
    import datetime as _dt
    for attr in ("modified_at", "updated_at", "created_at"):
        v = getattr(info, attr, None)
        if v:
            return str(v)[:10]
    try:
        from pathlib import Path
        from claude_agent_sdk import project_key_for_directory
        p = (Path.home() / ".claude" / "projects"
             / project_key_for_directory(workdir) / f"{info.session_id}.jsonl")
        return _dt.date.fromtimestamp(p.stat().st_mtime).isoformat()
    except Exception:
        return ""


async def cmd_sessions(update: Update, ctx):
    s = await _session(update, ctx)
    from claude_agent_sdk import list_sessions
    try:
        infos = list_sessions(directory=s.cfg.workdir, limit=8)
    except Exception as e:
        await update.message.reply_text(f"⚠️ couldn't list sessions: {e}")
        return
    if not infos:
        await update.message.reply_text("no past sessions for this workdir")
        return
    s.sessions_cache = [i.session_id for i in infos]
    lines, rows = [f"🗂 sessions in {s.cfg.workdir} (tap to resume):"], []
    for idx, i in enumerate(infos):
        cur = "● " if i.session_id == s.session_id else ""
        title = (i.custom_title or i.summary or i.first_prompt or "?")[:48]
        when = _session_date(s.cfg.workdir, i)
        lines.append(f"{cur}{idx + 1}. {title}" + (f" · {when}" if when else ""))
        rows.append([InlineKeyboardButton(
            f"{cur}{idx + 1}. {title}"[:60],
            callback_data=f"ss:{s.sid}:{idx}")])
    rows.append([InlineKeyboardButton("🔱 fork current", callback_data=f"sf:{s.sid}:0")])
    await update.message.reply_text("\n".join(lines)[:4000],
                                    reply_markup=InlineKeyboardMarkup(rows))


async def cmd_find(update: Update, ctx):
    s = await _session(update, ctx)
    query = " ".join(ctx.args or [])
    if len(query) < 3:
        await update.message.reply_text("usage: /find <text> (min 3 chars) — "
                                        "searches past conversations in this workdir")
        return
    from .transcripts import search_transcripts
    hits = await asyncio.to_thread(search_transcripts, s.cfg.workdir, query)
    if not hits:
        await update.message.reply_text(f"no matches for “{query}”")
        return
    s.sessions_cache = [h[0] for h in hits]
    lines = [f"🔎 “{query}” — tap to resume that conversation:"]
    rows = []
    for i, (sid_, snip) in enumerate(hits):
        cur = "● " if sid_ == s.session_id else ""
        lines.append(f"{cur}{i + 1}. {snip[:120]}")
        rows.append([InlineKeyboardButton(f"{cur}{i + 1}. {snip[:50]}",
                                          callback_data=f"ss:{s.sid}:{i}")])
    await update.message.reply_text("\n".join(lines)[:4000],
                                    reply_markup=InlineKeyboardMarkup(rows))


async def cmd_fork(update: Update, ctx):
    s = await _session(update, ctx)
    await s.restart(resume=True, fork=True,
                    note="🔱 forking conversation — edits branch from here…")


async def cmd_audit(update: Update, ctx):
    """Last entries of the tool-call audit trail (state/audit.jsonl)."""
    import json as _json
    try:
        raw = await asyncio.to_thread(AUDIT_FILE.read_text, encoding="utf-8")
    except OSError:
        await update.message.reply_text("🧾 no audit entries yet")
        return
    out = ["🧾 recent tool calls (⛔/✅ = guarded):"]
    for ln in raw.splitlines()[-15:]:
        try:
            e = _json.loads(ln)
        except ValueError:
            continue
        mark = ""
        if e.get("guarded"):
            mark = {"allow": " ✅", "deny": " ⛔"}.get(e.get("decision"), " 🔐")
        out.append(f"{e.get('ts', '?')[5:16]} [{e.get('agent', '?')}] "
                   f"{e.get('tool', '?')}{mark} {e.get('summary', '')[:90]}")
    if len(out) == 1:
        out.append("(empty)")
    await update.message.reply_text("\n".join(out)[:4000])


async def cmd_logs(update: Update, ctx):
    """Recent warnings/errors from the app log."""
    try:
        raw = await asyncio.to_thread(
            APP_LOG_FILE.read_text, encoding="utf-8", errors="replace")
    except OSError:
        await update.message.reply_text("no app log yet")
        return
    bad = [ln[:200] for ln in raw.splitlines()
           if " WARNING " in ln or " ERROR " in ln or " CRITICAL " in ln][-30:]
    if not bad:
        await update.message.reply_text("✅ no warnings or errors in the app log")
        return
    await update.message.reply_text(
        ("🧾 recent warnings/errors:\n" + "\n".join(bad))[:4000])


async def cmd_digest(update: Update, ctx):
    """On-demand 'what happened today' summary (issue #7)."""
    from .digest import build_digest
    await update.message.reply_text(build_digest(mgr(ctx)))


async def cmd_jobs(update: Update, ctx):
    text, kb = jobs_kb(mgr(ctx))
    await update.message.reply_text(text[:4000], reply_markup=kb)


async def cmd_remind(update: Update, ctx):
    s = await _session(update, ctx)
    raw = update.message.text.split(None, 1)
    body = raw[1] if len(raw) > 1 else ""
    when, _, text = body.partition("|")
    if not text:
        parts = body.split(None, 1)
        if parts and parts[0].lower() in ("daily", "every") and len(parts) > 1:
            sub = parts[1].split(None, 1)
            when = f"{parts[0]} {sub[0]}"
            text = sub[1] if len(sub) > 1 else ""
        elif len(parts) > 1:
            when, text = parts[0], parts[1]
    if not when.strip() or not text.strip():
        await update.message.reply_text(
            "usage: /remind <when>|<text>\n"
            "e.g. /remind +30m|standup · /remind 15:00 call mom · "
            "/remind daily 09:00 plan the day")
        return
    try:
        job = mgr(ctx).scheduler.add(s, "remind", when.strip(), text.strip())
        await update.message.reply_text(
            f"⏰ #{job['id']} set for {job['next_human']}"
            + (f" ({job['recur']})" if job.get("recur") else ""))
    except ValueError as e:
        await update.message.reply_text(f"⚠️ {e}")


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
async def on_text(update: Update, ctx):
    s = await _session(update, ctx)
    msg = update.message
    text = msg.text
    # Resolve a pending "Other / type your answer" question without re-feeding Claude
    for qid, st in list(s.questions.items()):
        if st.get("waiting_text") and not st["future"].done():
            if st.get("message_id"):
                try:
                    await mgr(ctx).bot.edit_message_text(
                        f"❓ {st['q']}\n✅ {text[:200]}",
                        chat_id=s.chat_id,
                        message_id=st["message_id"])
                except Exception:
                    pass
            st["future"].set_result(text)
            return
    r = msg.reply_to_message
    if r and (r.text or r.caption) and not text.startswith("/"):
        text = f"[replying to: «{(r.text or r.caption)[:300]}»]\n{text}"
    s.last_user_msg_id = msg.message_id
    await s.feed(text, TurnSource(), echo=msg.text.startswith("/"))


async def on_edited(update: Update, ctx):
    msg = update.edited_message
    if not msg or not msg.text:
        return
    s = await mgr(ctx).session_for_route(update.effective_chat.id,
                                         msg.message_thread_id)
    if msg.message_id == s.last_user_msg_id and s.busy:
        await s.interrupt()
        await s.feed(f"[the user corrected their message; use this version instead] "
                     f"{msg.text}")
    else:
        await s.feed(f"[the user edited an earlier message; updated version:] "
                     f"{msg.text}")


async def on_reaction(update: Update, ctx):
    """Reaction shortcuts: 👎/🤮/🤬 = interrupt; 🔁 = redo last response."""
    mr = update.message_reaction
    if not mr or not authorized_chat(mr.chat.id):
        return
    emojis = {getattr(r, "emoji", "") for r in (mr.new_reaction or [])}

    if emojis & {"👎", "🤮", "🤬"}:
        for s in list(mgr(ctx).sessions.values()):
            if s.chat_id == mr.chat.id and s.busy:
                await s.interrupt()
        return

    if emojis & {"🔁", "🔄"}:
        m = mgr(ctx)
        for s in list(m.sessions.values()):
            if s.chat_id == mr.chat.id and not s.busy:
                await s.feed(
                    "[user reacted 🔁 — please redo/retry your last response "
                    "with a different approach]", TurnSource())
                return


async def on_media(update: Update, ctx):
    s = await _session(update, ctx)
    msg = update.message
    INBOX.mkdir(parents=True, exist_ok=True)
    try:
        if msg.photo:
            tg_file = await msg.photo[-1].get_file()
            name, kind = f"photo_{msg.message_id}.jpg", "image"
        elif msg.document:
            tg_file = await msg.document.get_file()
            name, kind = msg.document.file_name or f"file_{msg.message_id}", "file"
        elif msg.video:
            tg_file = await msg.video.get_file()
            name, kind = msg.video.file_name or f"video_{msg.message_id}.mp4", "video"
        else:
            return
    except Exception as e:
        await msg.reply_text(f"⚠️ couldn't fetch media: {e}")
        return
    safe = re.sub(r'[\\/:*?"<>|]', "_", name).lstrip(". ") or f"file_{msg.message_id}"
    path = INBOX / safe
    try:
        await tg_file.download_to_drive(str(path))
    except Exception as e:
        await msg.reply_text(f"⚠️ download failed (bot limit ≈20MB): {e}")
        return
    cap = (msg.caption or "").strip()
    note = f"[received {kind}: {path}]"

    # albums (media groups) arrive as separate updates — coalesce into ONE turn
    gid = msg.media_group_id
    if gid:
        albums = ctx.application.bot_data.setdefault("albums", {})
        entry = albums.setdefault(gid, {"items": [], "caption": "",
                                        "task": None, "s": s, "msg": msg})
        entry["items"].append(note)
        if cap:
            entry["caption"] = cap
        if entry["task"]:
            entry["task"].cancel()

        async def flush(gid=gid, albums=albums):
            await asyncio.sleep(1.5)
            e = albums.pop(gid, None)
            if not e:
                return
            combined = "\n".join(e["items"]) \
                + (f"\n{e['caption']}" if e["caption"] else "")
            await e["msg"].reply_text(
                f"📎 album: {len(e['items'])} files saved → Claude")
            await e["s"].feed(combined)

        entry["task"] = asyncio.create_task(flush())
        return

    await msg.reply_text(f"📎 saved → {safe} → Claude")
    await s.feed(note + (f"\n{cap}" if cap else ""))


async def on_location(update: Update, ctx):
    s = await _session(update, ctx)
    loc = update.message.location
    await update.message.reply_text("📍 → Claude")
    await s.feed(f"[received location: lat={loc.latitude:.6f}, "
                 f"lon={loc.longitude:.6f}]")


async def on_voice(update: Update, ctx):
    s = await _session(update, ctx)
    msg = update.message
    INBOX.mkdir(parents=True, exist_ok=True)
    media = msg.voice or msg.audio
    try:
        tg_file = await media.get_file()
        path = INBOX / f"voice_{msg.message_id}.ogg"
        await tg_file.download_to_drive(str(path))
    except Exception as e:
        await msg.reply_text(f"⚠️ couldn't fetch voice note: {e}")
        return
    text = await voice.transcribe(str(path))
    if text:
        await msg.reply_text(f"🎙 “{text[:500]}”")
        await s.feed(text)
    else:
        note = ("no transcription API configured (set OPENAI_API_KEY or "
                "GROQ_API_KEY)" if not voice.available() else "transcription failed")
        await msg.reply_text(f"⚠️ {note}; passing the file to Claude")
        await s.feed(f"[received voice note (untranscribed): {path}]")


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
async def on_callback(update: Update, ctx):
    q = update.callback_query
    if not authorized_chat(update.effective_chat.id):
        await q.answer("not authorized")
        return
    m = mgr(ctx)
    data = q.data or ""
    await q.answer()

    # session-scoped callbacks carry a sid: pm/qp/qt/qd/qo/bt/qq
    parts = data.split(":")
    tag = parts[0]
    if tag in ("pm", "qp", "qt", "qd", "qo", "bt", "ud", "ss", "sf", "qq"):
        s = m.find_by_sid(int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else None
        if not s:
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            return
        # turn-interactive taps need a live client; ss/sf/qq work on a
        # stopped session too (they restart it / touch only local state)
        if tag in ("pm", "qp", "qt", "qd", "qo", "bt") and s.client is None:
            await _edit(q, "💤 (session no longer active — /restart)")
            return
        await _session_cb(q, s, tag, parts[2:])
        return

    chat_id, thread_id = update.effective_chat.id, \
        q.message.message_thread_id if q.message else None
    s = await m.session_for_route(chat_id, thread_id)

    if data.startswith("send:"):
        await s.feed(data[5:], echo=True)
    elif data == "act:interrupt":
        await s.interrupt()
    elif data == "act:restart":
        await s.restart(resume=True)
    elif data == "act:status":
        await _edit(q, _status_text(m), panel_kb(s))
    elif data == "act:auto":
        ctx.args = []
        s.cfg.auto_approve = not s.cfg.auto_approve
        m.save_agents()
        if s.client and s.connected:
            try:
                await s.client.set_permission_mode(
                    "bypassPermissions" if s.cfg.auto_approve else "default")
            except Exception:
                pass
        await _edit(q, "Control panel:", panel_kb(s))
    elif data == "act:secretary":
        s.cfg.secretary = not s.cfg.secretary
        m.save_agents()
        await _edit(q, f"secretary {'ON' if s.cfg.secretary else 'off'} — "
                       "restarting session…", panel_kb(s))
        await s.restart(resume=True, note="")
    elif data == "menu:model":
        await _edit(q, "Pick a model (applies live):", model_kb(s.cfg.model))
    elif data == "menu:cmds":
        await _edit(q, "Send a command to Claude:", cmds_kb(s))
    elif data == "menu:agents":
        await _edit(q, "Agents (● = active):", agents_kb(m))
    elif data == "menu:jobs":
        text, kb = jobs_kb(m)
        await _edit(q, text[:4000], kb)
    elif data == "menu:back":
        await _edit(q, "Control panel:", panel_kb(s))
    elif data.startswith("model:"):
        model = data.split(":", 1)[1]
        s.cfg.model = model
        s.model = model
        m.save_agents()
        applied = "live"
        if s.client and s.connected:
            try:
                await s.client.set_model(model or None)
            except Exception:
                applied = "after /restart"
        else:
            applied = "after /restart"
        await _edit(q, f"🧠 model → {model or 'default'} ({applied})", panel_kb(s))
    elif data.startswith("voi:"):
        v = data.split(":", 1)[1]
        s.cfg.voice = v
        m.save_agents()
        shown = v or f"default ({voice.default_voice()})"
        await _edit(q, f"🔊 voice → {shown}"
                       + ("" if s.cfg.tts else " (turn on with /tts)"))
    elif data == "agn":
        await _edit(q, "Create one with: /newagent <name> [workdir]\n"
                       "e.g. /newagent research D:\\Projects\\research",
                    agents_kb(m))
    elif data.startswith("ags:"):
        name = data[4:]
        if name in m.agents:
            await m.switch_active(name)
            await _edit(q, f"● active agent → {name}", agents_kb(m))
    elif data.startswith("agr:"):
        name = data[4:]
        sess = await m.session_for_agent(name)
        await sess.restart(resume=True)
        await _edit(q, f"♻️ {name} restarted", agents_kb(m))
    elif data.startswith("agx:"):
        name = data[4:]
        await _edit(q, f"Delete agent {name}? Its sessions and bindings go away.",
                    InlineKeyboardMarkup([[
                        InlineKeyboardButton("🗑 yes, delete", callback_data=f"agX:{name}"),
                        InlineKeyboardButton("⬅ no", callback_data="menu:agents")]]))
    elif data.startswith("agX:"):
        name = data[4:]
        if name != "main" and name in m.agents:
            await m.remove_agent(name)
        await _edit(q, "Agents:", agents_kb(m))
    elif data.startswith("jbd:"):
        m.scheduler.cancel(data[4:])
        text, kb = jobs_kb(m)
        await _edit(q, text[:4000], kb)
    elif data.startswith("pgc:"):
        parts_pg = data.split(":")
        if len(parts_pg) == 5:
            _, chat_str, tid_str, pid_str, pn_str = parts_pg
            tid_cb = int(tid_str) if tid_str != "0" else None
            s = await m.session_for_route(int(chat_str), tid_cb)
            if s:
                await _handle_page_cb(q, s, int(pid_str), int(pn_str))


async def _session_cb(q, s, tag: str, rest: list[str]):
    if tag == "ud":
        result = await s.undo(int(rest[0]))
        await _edit(q, (q.message.text or "") + f"\n{result}")
        return
    if tag == "ss":
        idx = int(rest[0])
        if idx >= len(s.sessions_cache):
            await _edit(q, "⚠️ stale list — run /sessions again")
            return
        target = s.sessions_cache[idx]
        s.session_id = target
        s.mgr.save_session_id(s.skey, target)
        await _edit(q, f"⏪ resuming session …{target[-8:]}")
        await s.restart(resume=True, note="")
        return
    if tag == "sf":
        await _edit(q, "🔱 forking conversation…")
        await s.restart(resume=True, fork=True, note="")
        return
    if tag == "qq":
        n = len(s.pending)
        s.pending.clear()
        await _edit(q, f"🗑 queue cleared ({n} message{'s' if n != 1 else ''} dropped)")
        return
    if tag == "pm":
        try:
            pid, verdict = int(rest[0]), rest[1]
        except (IndexError, ValueError):
            return
        tool = s.resolve_perm(pid, verdict)
        label = {"a": "✅ allowed", "s": "🔁 always allowed",
                 "d": "⛔ denied"}.get(verdict, verdict)
        if tool:
            await _edit(q, f"🔐 {tool}: {label}")
        else:
            await _edit(q, "🔐 (already resolved)")
        return

    qid = int(rest[0]) if rest else 0
    st = s.questions.get(qid)
    if tag in ("qp", "qt", "qd", "qo") and not st:
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if tag == "qp":
        try:
            label = st["opts"][int(rest[1])]
        except (IndexError, ValueError):
            await _edit(q, f"❓ {st['q']}\n⚠️ (that option expired)")
            return
        await _edit(q, f"❓ {st['q']}\n✅ {label}")
        if not st["future"].done():
            st["future"].set_result(label)
    elif tag == "qt":
        try:
            idx = int(rest[1])
        except (IndexError, ValueError):
            return
        if not 0 <= idx < len(st["opts"]):
            return
        st["selected"].symmetric_difference_update({idx})
        try:
            await q.edit_message_reply_markup(reply_markup=s.question_kb(qid))
        except Exception:
            pass
    elif tag == "qd":
        labels = [st["opts"][i] for i in sorted(st["selected"])
                  if 0 <= i < len(st["opts"])]
        ans = ", ".join(labels) if labels else "(none selected)"
        await _edit(q, f"❓ {st['q']}\n✅ {ans}")
        if not st["future"].done():
            st["future"].set_result(ans)
    elif tag == "qo":
        await _edit(q, f"❓ {st['q']}\n✏️ Type your answer below.")
        st["waiting_text"] = True  # on_text will resolve the future when user types
    elif tag == "bt":
        bid, idx = int(rest[0]), int(rest[1])
        labels = s.kb_store.get(bid)
        if not labels or idx >= len(labels):
            return
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await s.feed(labels[idx], echo=True)


async def _handle_page_cb(q, s, page_id: int, page_num: int):
    from .fmt import md_to_html
    pages = s.outbox._page_store.get(page_id)
    if not pages:
        await _edit(q, "⚠️ page expired — run the command again")
        return
    n = len(pages)
    text = pages[page_num] + f"\n\n`[{page_num + 1}/{n}]`"
    kb = None
    if page_num + 1 < n:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            f"▶ More  {page_num + 2}/{n}",
            callback_data=f"pgc:{s.chat_id}:{s.thread_id or 0}:{page_id}:{page_num + 1}"
        )]])
    try:
        await q.edit_message_text(md_to_html(text), parse_mode="HTML",
                                  reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        await _edit(q, text, kb)


async def _edit(q, text: str, markup=None):
    try:
        await q.edit_message_text(text, reply_markup=markup,
                                  disable_web_page_preview=True)
    except Exception:
        pass


_last_err_note = 0.0


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    """Transient Telegram hiccups (Bad Gateway, timeouts, flood waits) are
    retried by PTB's own loop — logging them is enough; never page the user.
    Real errors are forwarded at most once per 5 minutes."""
    import time as _time
    from telegram.error import Conflict, NetworkError, RetryAfter, TimedOut
    err = ctx.error
    if isinstance(err, (NetworkError, TimedOut, RetryAfter)):
        metrics.bump("tg_transient")
        log.warning("transient telegram error: %s: %s", type(err).__name__, err)
        return
    global _last_err_note
    now = _time.monotonic()
    if isinstance(err, Conflict):
        # another poller on this bot token — the singleton lock should prevent
        # it, but if it happens, log ONE concise line (no traceback storm) and
        # warn the user once per 5 min instead of every retry
        metrics.bump("tg_conflict")
        log.warning("getUpdates Conflict — another instance is polling this bot")
        if now - _last_err_note < 300:
            return
        _last_err_note = now
        try:
            from .config import CHAT_ID
            await ctx.bot.send_message(
                CHAT_ID, "⚠️ Another bridge instance is polling this bot. Only "
                "one should run — check for a duplicate start_bridge / leftover "
                "python bridge.py. This instance keeps retrying meanwhile.")
        except Exception:
            pass
        return
    metrics.bump("handler_error")
    log.exception("handler error", exc_info=err)
    if now - _last_err_note < 300:
        return
    _last_err_note = now
    try:
        from .config import CHAT_ID
        await ctx.bot.send_message(
            CHAT_ID, f"⚠️ bridge error: {type(err).__name__}: {str(err)[:300]}")
    except Exception:
        pass
