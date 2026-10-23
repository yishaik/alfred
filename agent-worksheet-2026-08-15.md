# Agent worksheet — 2026-08-15

## Plan

- Diagnose why enabling Telegram Topics / threaded mode hid the main (General)
  thread and made replies miss the conversation.
- Keep the primary private-chat session intact, but always deliver into the
  Topics thread Telegram actually has open.
- Do not restart the live bridge from this session.

## Result

Root cause: once Topics is on, Telegram still gives inbound messages a
`message_thread_id`, but the bridge discarded it for the primary session and
posted with no thread. Those messages land *outside* the topic list, which
makes General disappear. A leftover persistent reply keyboard made it worse
(Telegram hides General when a reply keyboard is shown in a Topics chat).

Fixes:
- Route private-chat Topics through the same `main@p` session, but keep the
  delivery thread id on the session/outbox.
- Remember the last unbound private-chat thread across restarts
  (`agents.json.private_thread_id`) so startup/job notices stay in General.
- Ignore ordinary reply-chain ids (not Topics) so we do not invent a hidden
  thread in a normal chat.
- Stop attaching the persistent reply keyboard once Topics is detected; try
  `unhideGeneralForumTopic` and send `ReplyKeyboardRemove` into that thread.

## Verification

- `.venv/bin/python -m py_compile tgbridge/manager.py tgbridge/handlers.py tgbridge/main.py selftest.py`
- Targeted selftest: `test_job_skey`, `test_route_thread_id`, `test_resolve_route` all OK.
- Full `selftest.py` still reports the same 6 pre-existing failures
  (bridge-tool exposure, singleton lock while the live bridge owns it, peer bus).

## Deviations

- Did not restart the bridge (AGENTS.md). The running process will pick this
  up on the next orchestrator restart.
- Reply keyboards (`/brainbot` managed-bot button, persistent quick bar) stay
  disabled while Topics is on. That is a Telegram limitation, not a new one.

## Open for the user

- After the next `/restart` (or orchestrator restart), send one message in
  General. The bridge should unhide General, drop the leftover keyboard, and
  keep answering in that topic.
- If General is still missing in the Telegram UI: Chat settings → Topics →
  unhide General, or turn Topics off if you want the old reply keyboard back.
