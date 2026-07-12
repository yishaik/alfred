# Alfred — Agent Workflow

Standard operating procedure for any agent session in this repo.
Tag this file at session start: `@AGENT_WORKFLOW.md`

## Session start
1. Read `AGENTS.md` (you should already have it)
2. Read `TODOS.md` — pick the top unblocked task
3. Run selftest: `.venv/Scripts/python.exe selftest.py`
4. Create `agent-worksheet-{YYYY-MM-DD}.md` and log your plan there

## During work
- After each file edit: `.venv/Scripts/python.exe -m py_compile <file>`
- Log decisions and deviations in your worksheet under `## Deviations`
- If you hit an unknown: pick the conservative option, log it, keep going
- Never restart the bridge — you'll kill your own process

## Before committing
- [ ] All changed files compile cleanly
- [ ] selftest.py passes
- [ ] Worksheet updated with what was done and any open questions
- [ ] TODOS.md updated (mark done, add new items found)
- [ ] One commit, message: `feat/fix/chore: <what> — see agent-worksheet-{date}.md`
- [ ] Do NOT push

## Self-check before ending session
Ask yourself:
> "If another agent picks this up tomorrow with only AGENTS.md + TODOS.md, can they continue without asking me anything?"

If no → update the relevant doc before committing.

## Common commands
```bash
# compile-check all tgbridge modules
.venv/Scripts/python.exe -m py_compile tgbridge/*.py

# run offline tests
.venv/Scripts/python.exe selftest.py

# check bridge log (don't restart)
Get-Content state/bridge-app.log -Tail 50
```
