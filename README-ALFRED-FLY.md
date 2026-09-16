# Alfred Fly Multi-Model Engineering Platform

This overlay upgrades the existing `yishaik/alfred` Telegram bridge for Fly.io deployment and adds a guarded engineering/deployment control surface.

## Included

- Fly.io Docker image, health checks, restart behavior, and persistent `/data` volume
- subscription-backed Claude Code, Codex, and Gemini CLI sessions
- current-model configuration for Claude, GPT, Gemini, and Grok
- read-only multi-model council
- safe repository clone/branch/test/draft-PR workflow
- preview and approved-production adapters for Vercel, Netlify, Cloudflare, Supabase, and Hugging Face
- Tavily search adapter
- AppDeploy handoff manifest
- optional ephemeral Fly worker Machines
- secret redaction, repository allowlist, path confinement, command allowlists, and explicit production approval
- CI and unit tests

## First commands

```bash
cp -R <overlay>/* <alfred-repo>/
pytest -q tests/test_cloud_platform.py
fly launch --copy-config --name alfred-<unique-name> --no-deploy
fly volumes create alfred_data --region fra --size 10 --app alfred-<unique-name>
fly deploy --app alfred-<unique-name>
```

Read in this order:

1. `docs/ARCHITECTURE-FLY.md`
2. `docs/AUTH-AND-SUBSCRIPTIONS.md`
3. `docs/SECURITY-FLY.md`
4. `docs/DEPLOY-FLY.md`
5. `docs/PLATFORM-INTEGRATIONS.md`

## Deliberate non-goals

- no autonomous merge to the default branch
- no production deployment without explicit approval
- no storage of raw secrets in Git
- no claim that a consumer subscription provides API credits
- no direct AppDeploy runtime call where the connector does not expose one
