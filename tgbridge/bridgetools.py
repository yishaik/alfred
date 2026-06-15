"""In-process MCP server exposing bridge actions as real tools.

This replaces the legacy ⟦…⟧ text markers (still parsed as a fallback) with
proper tool calls: schemas, validation, and immediate success/error feedback
to Claude. One server instance is built per session so handlers close over it.
"""

import logging
from typing import Annotated

from claude_agent_sdk import create_sdk_mcp_server, tool

log = logging.getLogger("bridge.tools")

SERVER_NAME = "bridge"
TOOL_NAMES = ["send_file", "send_buttons", "message_agent",
              "schedule", "unschedule", "list_jobs"]
# fully-qualified names for allowed_tools
ALLOWED = [f"mcp__{SERVER_NAME}__{t}" for t in TOOL_NAMES]


def _text(msg: str, err: bool = False) -> dict:
    out = {"content": [{"type": "text", "text": msg}]}
    if err:
        out["is_error"] = True
    return out


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

    return create_sdk_mcp_server(
        name=SERVER_NAME, version="1.0.0",
        tools=[send_file, send_buttons, message_agent,
               schedule, unschedule, list_jobs])
