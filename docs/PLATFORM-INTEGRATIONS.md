# Platform Integration Contract

## GitHub

The runtime uses `gh` and Git. Standard flow: clone → fetch → branch → edit → test → push → draft PR. The agent does not merge. GitHub Actions remains the authoritative CI gate.

## Vercel

`vercel deploy --yes` creates a preview. `--prod` is appended only after production approval. Set `VERCEL_TOKEN`; the connected team discovered during preparation is `yishaiks-projects` (`team_ZKpdeShcRUJptlPmkXbqEqfI`).

## Netlify

`netlify deploy --build --json` creates a draft deployment. `--prod` requires approval. The connected team slug discovered during preparation is `yishaik`. Link each repository to the correct site before enabling autonomous previews.

## Cloudflare

Wrangler dry-run is the default. Production executes `wrangler deploy` only after approval. Use a narrowly scoped Cloudflare API token; do not use the global API key. Repository configuration remains in `wrangler.jsonc` or `wrangler.toml`.

## Supabase

Preview mode runs `supabase db lint --linked`. Production mode runs `supabase db push --linked` after approval. Destructive or irreversible migrations still require a separate explicit human review. The active project discovered during preparation was `calisthenics-coach` in `eu-central-1`; no project is selected globally because Alfred serves multiple repositories.

## Hugging Face

The `hf` CLI validates identity in preview mode. Upload requires an explicit target repository and production approval. Model or dataset training should run as a separate job rather than inside the always-on Telegram control plane.

## Tavily

Tavily is a read-only research adapter. Results are untrusted content and may not issue instructions to the executor. Search is never a substitute for inspecting the repository or official provider documentation.

## AppDeploy

The connected AppDeploy account currently has no applications. AppDeploy's available connector contract is designed for a ChatGPT conversation and does not expose a general Fly-runtime deployment API. Alfred therefore generates `.agent-runtime/appdeploy-handoff.json`; the actual AppDeploy publish action occurs in an AppDeploy-enabled ChatGPT session. This avoids claiming an integration that the runtime cannot securely invoke.
