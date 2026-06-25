"""In-process MCP server exposing bridge actions as real tools.

This replaces the legacy ⟦…⟧ text markers (still parsed as a fallback) with
proper tool calls: schemas, validation, and immediate success/error feedback
to Claude. One server instance is built per session so handlers close over it.
"""

import asyncio
import logging
from typing import Annotated

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import napkin_store

log = logging.getLogger("bridge.tools")

# x-reader's Node CLI tools (content fetcher + Gemma model router) shelled out by
# the fetch_content / route_model tools below.
XREADER_DIR = r"D:\Projects\x-reader"

SERVER_NAME = "bridge"
TOOL_NAMES = ["send_file", "send_buttons", "message_agent",
              "schedule", "unschedule", "list_jobs",
              "remember", "forget", "recall", "kb_read",
              "fetch_content", "route_model"]
# fully-qualified names for allowed_tools
ALLOWED = [f"mcp__{SERVER_NAME}__{t}" for t in TOOL_NAMES]


def _text(msg: str, err: bool = False) -> dict:
    out = {"content": [{"type": "text", "text": msg}]}
    if err:
        out["is_error"] = True
    return out


async def _run_node(script: str, *script_args: str, timeout: float = 90.0) -> tuple[str, int]:
    """Run one of x-reader's Node CLIs and return (combined output, returncode).
    Never raises — OSError/timeout are folded into a non-zero code + message so
    the calling tool degrades gracefully."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", script, *script_args, cwd=XREADER_DIR,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as e:
        return (f"couldn't launch node: {e}", 1)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return (f"{script} timed out after {int(timeout)}s", 1)
    return ((out or b"").decode("utf-8", "replace").strip(), proc.returncode or 0)


def build_bridge_server(session):
    """Create the per-session MCP server config for ClaudeAgentOptions."""
    mgr = session.mgr

    @tool("send_file", "Send a file from disk to the user via Telegram. "
          "Images are delivered as photos, everything else as documents.",
          {"path": Annotated[str, "absolute path on this machine"]})
    async def send_file(args):
        session.outbox.file(str(args.get("path", "")))
        return _text("queued for delivery")

    @tool("send_buttons", "Show the user tappable quick-reply buttons. The "
          "tapped label arrives as the user's next message. Use for short "
          "menus of likely next actions (max 8).",
          {"labels": Annotated[list, "button labels, e.g. ['Yes', 'No']"],
           "text": Annotated[str, "optional message text above the buttons"]})
    async def send_buttons(args):
        labels = [str(x)[:60] for x in (args.get("labels") or [])][:8]
        if not labels:
            return _text("no labels given", err=True)
        markup = session._kb_markup(labels)
        session.outbox.keyboard(str(args.get("text") or "➡️"), markup)
        return _text("buttons shown; the tapped label arrives as the next user message")

    @tool("message_agent", "Send a message to another agent on this bridge "
          "(or a configured peer bridge). Rate-limited and hop-capped to "
          "prevent loops — never use for acknowledgements or thanks.",
          {"agent": Annotated[str, "destination agent or peer name"],
           "message": str})
    async def message_agent(args):
        dest = str(args.get("agent", "")).strip()
        text = str(args.get("message", ""))
        if not dest or not text:
            return _text("agent and message are required", err=True)
        await mgr.route_bot_message(session, dest, text,
                                    session.turn_source.hop + 1)
        return _text(f"routed to {dest} (subject to hop/rate limits; "
                     "drops are reported to the user)")

    @tool("schedule", "Schedule a future action. kind='remind' texts the USER "
          "at the given time; kind='prompt' sends YOU the text as a prompt at "
          "that time (for follow-ups, digests, checks). When formats: "
          "'2026-01-31 15:00', '15:00', '+30m', 'daily 09:00', "
          "'weekly mon 09:00', 'weekdays 08:30'; append ' until 2026-12-31' "
          "to recurring ones.",
          {"when": str, "kind": Annotated[str, "remind | prompt"],
           "text": str})
    async def schedule(args):
        kind = str(args.get("kind", "remind"))
        if kind not in ("remind", "prompt"):
            return _text("kind must be 'remind' or 'prompt'", err=True)
        try:
            job = mgr.scheduler.add(session, kind, str(args.get("when", "")),
                                    str(args.get("text", "")))
        except ValueError as e:
            return _text(f"couldn't schedule: {e}", err=True)
        session.outbox.emit(
            f"⏰ scheduled {kind} #{job['id']} for {job['next_human']}"
            + (f" ({job['recur']})" if job.get("recur") else ""))
        return _text(f"job #{job['id']} scheduled for {job['next_human']}"
                     + (f", recurring {job['recur']}" if job.get("recur") else ""))

    @tool("unschedule", "Cancel a scheduled job by id.", {"job_id": str})
    async def unschedule(args):
        jid = str(args.get("job_id", "")).strip().lstrip("#")
        ok = mgr.scheduler.cancel(jid)
        if ok:
            session.outbox.emit(f"🗑 job {jid} cancelled")
        return _text("cancelled" if ok else f"job {jid} not found", err=not ok)

    @tool("list_jobs", "List the scheduled jobs (reminders and prompts).", {})
    async def list_jobs(args):
        jobs = mgr.scheduler.list_jobs()
        if not jobs:
            return _text("no scheduled jobs")
        lines = [f"#{j['id']} {j['kind']} @ {j['next_human']}"
                 + (f" ({j['recur']})" if j.get("recur") else "")
                 + f" [{j['agent']}]: {j['text'][:80]}" for j in jobs]
        return _text("\n".join(lines))

    @tool("remember", "Save something to long-term memory so you recall it in "
          "future sessions (it's injected into every fresh session). Use for "
          "durable facts about the user, decisions, open loops, and contacts — "
          "NOT for transient chatter. kind='note' for your own observations, "
          "'fact' for reference knowledge, 'pinned' for must-never-forget.",
          {"text": str,
           "kind": Annotated[str, "note | fact | pinned (default note)"]})
    async def remember(args):
        text = str(args.get("text", "")).strip()
        if not text:
            return _text("text is required", err=True)
        kind = str(args.get("kind") or "note")
        # add() spawns a short-lived `node` (napkin); keep it off the event loop
        # so a ~1-2s cold start can't freeze every other session and the typing
        # indicator while it runs.
        mem = mgr.memory_for(session.cfg.name)
        try:
            item = await asyncio.to_thread(mem.add, text, kind)
        except napkin_store.NapkinError as e:
            return _text(f"couldn't save to memory: {e}", err=True)
        if item is None:
            return _text("nothing to remember", err=True)
        mgr.save_memory()
        return _text(f"remembered ({item.kind}): {text[:120]}")

    @tool("forget", "Remove an item from long-term memory by a substring of its "
          "text. Use when something you stored is now wrong or obsolete.",
          {"text": Annotated[str, "substring identifying the item to drop"]})
    async def forget(args):
        mem = mgr.memory_for(session.cfg.name)
        removed = await asyncio.to_thread(mem.remove, str(args.get("text", "")))
        if removed is None:
            return _text("no matching memory found", err=True)
        mgr.save_memory()
        return _text(f"forgotten: {removed[:120]}")

    @tool("recall", "Search your long-term memory vault (BM25 + recency). Empty "
          "query returns everything. Use to check what you already know before "
          "asking the user to repeat themselves. Results show a snippet and, "
          "for notes, the file path — read the full file with `kb_read`.",
          {"query": Annotated[str, "what to search for (optional)"]})
    async def recall(args):
        mem = mgr.memory_for(session.cfg.name)
        hits = await asyncio.to_thread(mem.search, str(args.get("query", "")))
        if not hits:
            return _text("(nothing remembered yet)")
        lines = [f"[{it.kind}] {it.text}"
                 + (f"  (file: {it.file})" if it.file else "")
                 for it in hits[:40]]
        return _text("\n".join(lines))

    @tool("kb_read", "Read the full text of one file from your long-term memory "
          "vault, by the path shown in a `recall` result (e.g. "
          "'notes/foo.md'). Use when a recall snippet isn't the whole story.",
          {"file": Annotated[str, "vault-relative path from a recall result"]})
    async def kb_read(args):
        name = str(args.get("file", "")).strip()
        if not name:
            return _text("file is required", err=True)
        vault = mgr.memory_for(session.cfg.name).vault
        try:
            content = await asyncio.to_thread(napkin_store.read, vault, name)
        except napkin_store.NapkinError as e:
            return _text(f"couldn't read {name}: {e}", err=True)
        return _text(content or "(empty or not found)")

    @tool("fetch_content", "Fetch the FULL content of a tweet or web article that "
          "normal web fetch can't read (X/Twitter posts, JS-heavy pages) — uses a "
          "headless browser with the saved X login. Pulls the whole thread, "
          "images, links, and linked X Articles, and auto-saves everything to the "
          "Second Brain. Pass a tweet URL/id or any article URL.",
          {"url": Annotated[str, "tweet URL/id or article URL"]})
    async def fetch_content(args):
        url = str(args.get("url", "")).strip()
        if not url:
            return _text("url is required", err=True)
        # --save: every fetch lands in the Second Brain automatically.
        out, rc = await _run_node("fetch.mjs", url, "--save", timeout=120.0)
        return _text(out[:6000] or "(no content)", err=(rc != 0))

    @tool("route_model", "Ask the local Gemma model-router which AI model best fits "
          "a task (gemini=content/visuals, claude-code=code, claude=logic/analysis, "
          "gpt=images/personal, grok=facts/current-events). Returns the recommended "
          "model and a short reason.",
          {"task": Annotated[str, "the task to route to a model"]})
    async def route_model(args):
        task = str(args.get("task", "")).strip()
        if not task:
            return _text("task is required", err=True)
        out, rc = await _run_node("route.mjs", task, timeout=180.0)
        return _text(out or "(no result)", err=(rc != 0))

    return create_sdk_mcp_server(
        name=SERVER_NAME, version="1.0.0",
        tools=[send_file, send_buttons, message_agent,
               schedule, unschedule, list_jobs,
               remember, forget, recall, kb_read,
               fetch_content, route_model])
