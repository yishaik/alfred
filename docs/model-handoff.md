# Model selection and conversation continuity

`/engine` reports the current engine. `/engine claude`, `/engine codex` and
`/engine antigravity` change it for this Telegram route. Wait for an active
answer to finish before changing engines or models. Incoming messages queue
while any engine or the router works.

`/model` discovers models for the active engine. Refresh reloads the provider
catalog. Codex uses the installed app-server's `model/list`; Claude uses SDK
initialization metadata; Antigravity uses `agy models`. If discovery fails,
the previous catalog remains available. Codex can fall back to its local
catalog cache. Claude can fall back to the stable Opus/Sonnet/Haiku aliases;
these are labeled as aliases, not an account availability check.

Changing a Codex model resumes the same native thread with `-m`. Selecting
Default reads the configured model from CODEX_HOME/config.toml. With no explicit
configured model, CLI resume behavior determines the model. Antigravity model
changes create a fresh native conversation and receive shared history.

Complete user/answer exchanges are saved locally in ignored
`state/handoffs/<route-hash>.jsonl`. The corresponding JSON file stores the
active engine and successful-turn cursors. Native external thread IDs and model
preferences are also saved under state, keyed by route rather than agent name.
Existing Claude transcripts bootstrap the journal on the first handoff.

Each engine receives exchanges it has missed. A fresh native conversation gets
the available history again, including after `/engine reset`. Only successful
answers advance cursors. A provider error keeps context for the Claude fallback.
The in-memory tail holds 64 exchanges; the prompt budget is 24,000 characters.
Omissions and shortened exchanges are explicit, with a path to the full local
journal. This is conversation continuity, not a replay of tool outputs or a
guarantee that old filesystem facts remain current.

## Validation and activation

Run `.venv/Scripts/python.exe -m pytest tests/test_model_handoff.py -q` offline.
Model discovery itself does not submit an inference turn. Live Telegram model
switching still needs an orchestrator restart followed by a user-path check.
AGENTS.md forbids agents from restarting this bridge or pushing its commits.

Official Codex protocol: https://learn.chatgpt.com/docs/app-server
