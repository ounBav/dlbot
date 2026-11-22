# Render Deployment Guide

## How deploys work

```text
git push main ──▶ GitHub Actions
                   1. test    compileall + pytest          (~30 s)
                   2. build   ONE image → ghcr.io/ounbav/dlbot:<sha>   (cached: ~30–60 s)
                   3. deploy  Render deploy hooks (web + worker) with ?imgURL=…:<sha>
Render ──▶ pull image ──▶ start      (no build on Render; usually 1–2 min)
```

- Render never builds. Both services run the same image; the worker just overrides the command.
- A push that fails tests never reaches Render (image services don't auto-deploy).
- Pushes that only change `*.md` files don't run the pipeline.
- Every deploy is pinned to a commit SHA, so Render's **Rollback** works.

## One-time setup

Do these in order. Steps 1–3 are only needed once.

### 1. Push the code so the first image exists

```bash
git push origin main
```

Open **GitHub → Actions → deploy**. `test` and `build` should pass. `deploy` will warn
"hooks not set" — that's expected the first time.

### 2. Let Render pull the private image

1. GitHub → **Settings → Developer settings → Personal access tokens → Tokens (classic)**
   → new token with only **`read:packages`**.
2. Render → **Workspace Settings → Container Registry Credentials → Add**
   - Name: **`ghcr-dlbot`** (must match `render.yaml`)
   - Registry: GitHub Container Registry
   - Username: your GitHub username
   - Token: the token from step 2.1

### 3. Create or update the Blueprint

Render → **New → Blueprint** → this repo, branch `main` (or **Blueprints → Sync** if it exists).

Fill the `sync: false` variables:

| Variable | Value |
|---|---|
| `BOT_TOKEN` | Token from @BotFather (both services) |
| `PUBLIC_BASE_URL` | `https://bot-webhook-xxxx.onrender.com` (after the first deploy) |
| `ADMIN_IDS` | Your Telegram user ID (comma-separated for several) |
| `TERMS_URL` | Link to your terms page |

`WEBHOOK_SECRET`, database and Redis values are generated and linked automatically.

> **Existing services built with `runtime: docker`:** Render may refuse to switch an existing
> service to `runtime: image` during a sync. If it does, delete only **bot-webhook** and
> **bot-worker** (not `bot-db` / `bot-redis` — your data lives there) and sync again.

### 4. Connect GitHub Actions to Render

1. Render → **bot-webhook → Settings → Deploy Hook** → copy the URL.
2. Render → **bot-worker → Settings → Deploy Hook** → copy the URL.
3. GitHub → **Settings → Secrets and variables → Actions → New repository secret**
   - `RENDER_DEPLOY_HOOK_WEB` = web hook URL
   - `RENDER_DEPLOY_HOOK_WORKER` = worker hook URL

Deploy hook URLs are secrets: anyone with one can trigger a deploy.

### 5. Register the Telegram webhook

With the production values (`WEBHOOK_SECRET` is on the bot-webhook **Environment** tab):

```bash
BOT_TOKEN=... WEBHOOK_SECRET=... PUBLIC_BASE_URL=https://bot-webhook-xxxx.onrender.com \
  DB_HOST=x DB_SCHEMA=x DB_USERNAME=x DB_PASSWORD=x REDIS_URL=redis://x \
  PYTHONPATH=. python scripts/set_webhook.py
```

(The `DB_*`/`REDIS_URL` placeholders only satisfy settings validation; the script calls Telegram only.)
Telegram keeps this webhook until you change it, so this is once per URL, not per deploy.

### 6. Test

Re-run the workflow (**Actions → deploy → Run workflow**), then send a link to the bot.

## Everyday use

| Want to… | Do |
|---|---|
| Deploy | `git push origin main` |
| Redeploy without code changes | Actions → deploy → **Run workflow** |
| Roll back | Render → service → **Rollback** to an earlier deploy |
| Bump yt-dlp | Edit the pin in `requirements.txt`, push |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Render: `failed to read dockerfile: open Dockerfile: no such file or directory` | Service is still `runtime: docker` and builds `./Dockerfile` from the repo root. The Dockerfile must stay at the repo root (it builds `apps/bot/` with root context). Don't move it back into `apps/bot/` without also changing the service's Dockerfile path and context. |
| Render: "failed to pull image" / unauthorized | Credential name isn't `ghcr-dlbot`, or the token lacks `read:packages` |
| Actions `deploy`: HTTP 400 | Image URL in `render.yaml` doesn't match `ghcr.io/<owner>/<repo>` (must be lowercase) |
| Actions `deploy`: HTTP 404 | Deploy hook secret is wrong or was regenerated |
| `deploy` job skipped with a warning | Hook secrets not set (step 4) |
| "Connection refused" in logs on first boot | DB still provisioning; web retries migrations, then restart once |
| Telegram not delivering | Re-run step 5; check `getWebhookInfo` `last_error_message` |

## Resources

- [Render: Deploy a prebuilt image](https://render.com/docs/deploying-an-image)
- [Render: Deploy hooks](https://render.com/docs/deploy-hooks)
- [Render: Blueprint spec](https://render.com/docs/blueprint-spec)
- [Telegram Bot API: setWebhook](https://core.telegram.org/bots/api#setwebhook)
