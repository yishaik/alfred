# Authentication and Subscription Matrix

## Important distinction

A web/app subscription and a developer API account are separate products unless the vendor explicitly supports account login in its coding CLI. This architecture uses subscription login where officially supported and API credentials everywhere else.

| Provider | Subscription-backed path on Fly | Direct API path | Recommended role |
|---|---|---|---|
| Anthropic | Claude Code login with Claude Pro/Max | `ANTHROPIC_API_KEY` | primary executor |
| OpenAI | Codex login with eligible ChatGPT plan | `OPENAI_API_KEY` | GPT council and coding specialist |
| Google | Gemini CLI Google account login | `GEMINI_API_KEY` | reviewer, router, large-context analysis |
| xAI | no server-side subscription CLI assumed | `XAI_API_KEY` | independent Grok reviewer |
| Groq | not Grok; optional separate service | `GROQ_API_KEY` | Alfred voice transcription fallback |

## Credential persistence

CLI credentials are stored below `/data/home`, on the persistent Fly Volume. They are not copied into the image. Anyone with Fly organization access sufficient to SSH to the Machine may be able to use these sessions; keep Fly membership and access tokens tightly scoped.

## OpenAI key flow

An OpenAI Platform API-key setup widget was initiated for the project key named `Alfred Fly Multi-Model Agent`. After creating or copying the key in that secure flow, add it directly to Fly:

```bash
fly secrets set OPENAI_API_KEY='sk-...' --app <app>
```

Do not paste the key into Telegram, GitHub issues, PRs, `.env` files, CI output, or application logs.

## Token rotation

1. Create the replacement token.
2. Update the Fly secret.
3. Restart or redeploy the Machine.
4. Run `python -m cloud.platformctl doctor` and one read-only provider call.
5. Revoke the previous token.

Do not revoke first; that creates an avoidable outage and makes rollback harder.
