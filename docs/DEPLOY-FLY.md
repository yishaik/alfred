# Deployment Guide

## 1. Apply this overlay to Alfred

Copy the files in this package into the root of `yishaik/alfred`. The paths are repository-relative. Commit them on a feature branch and run the test workflow.

## 2. Choose a unique Fly app name

Edit `app` in `fly.toml`, or let Fly copy the config under a unique name:

```bash
fly launch --copy-config --name alfred-<unique-name> --no-deploy
```

The default region is `fra`, a practical low-latency European region for an Israel-based operator. Change it only when data residency or measured latency requires another region.

## 3. Create persistent storage

```bash
fly volumes create alfred_data --region fra --size 10 --app alfred-<unique-name>
```

Use one Machine while a single local Fly Volume is the state source. Do not horizontally scale the Telegram poller without first replacing JSON state and the singleton lock with shared transactional storage.

## 4. Add required secrets

```bash
fly secrets set \
  BRIDGE_BOT_TOKEN='...' \
  BRIDGE_CHAT_ID='...' \
  GH_TOKEN='...' \
  ALFRED_ALLOWED_REPOS='yishaik/alfred,yishaik/another-repo' \
  --app alfred-<unique-name>
```

Add provider and deployment credentials only for integrations you intend to enable. Never copy `.env.fly.example` into Git.

## 5. Deploy

```bash
fly deploy --app alfred-<unique-name>
fly status --app alfred-<unique-name>
fly checks list --app alfred-<unique-name>
fly logs --app alfred-<unique-name>
```

The health endpoint is `/healthz`. It reveals only `ok` or `starting` and contains no account, repository, model, or secret details.

## 6. Authenticate subscription-backed CLIs once

The Fly Volume persists `$HOME=/data/home`, so one interactive login per CLI survives future image deployments.

```bash
fly ssh console --app alfred-<unique-name>
claude
codex login
gemini
```

Select the account-backed login option for each CLI. Finish browser/device authorization outside the SSH session when prompted. Then verify:

```bash
claude doctor
codex --version
gemini --version
python -m cloud.platformctl doctor
```

Claude Code can use a Claude Pro or Max account. Codex can use eligible ChatGPT account access. Gemini CLI can use Google account authentication. These account-backed sessions do not convert consumer subscriptions into general API credits.

## 7. Configure Grok and direct APIs

Grok server-side use requires `XAI_API_KEY`:

```bash
fly secrets set XAI_API_KEY='...' --app alfred-<unique-name>
```

Direct OpenAI, Anthropic, or Gemini API calls similarly require their respective API credentials. The secure OpenAI key setup flow opened in this ChatGPT conversation can create the `OPENAI_API_KEY`; store it with `fly secrets set` rather than in the repository.

## 8. Configure deployment adapters

```bash
fly secrets set \
  VERCEL_TOKEN='...' \
  VERCEL_ORG_ID='team_ZKpdeShcRUJptlPmkXbqEqfI' \
  NETLIFY_AUTH_TOKEN='...' \
  NETLIFY_TEAM_SLUG='yishaik' \
  CLOUDFLARE_API_TOKEN='...' \
  CLOUDFLARE_ACCOUNT_ID='...' \
  SUPABASE_ACCESS_TOKEN='...' \
  HF_TOKEN='...' \
  TAVILY_API_KEY='...' \
  --app alfred-<unique-name>
```

Use tokens restricted to the minimum account, project, repository, and action scope. Prefer provider Git integrations for normal deployment, with CLI tokens used for previews, inspection, and explicitly approved recovery operations.

## 9. Smoke test

From Telegram, ask Alfred to:

1. Run `python -m cloud.platformctl doctor`.
2. Clone one allowlisted repository.
3. Create an `agent/smoke-test` branch.
4. Run the repository's normal test command.
5. Run a four-model council review.
6. Open a draft PR without deploying.

Only after this flow succeeds should preview deployment credentials be enabled.

## Rollback

```bash
fly releases --app alfred-<unique-name>
fly deploy --image <previous-image> --app alfred-<unique-name>
```

The `/data` volume remains attached, so application rollback does not roll back mutable state. Alfred already writes daily state backups; restore a backup separately when state corruption, rather than image regression, is the problem.
