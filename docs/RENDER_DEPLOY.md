# Render Deployment Guide

## What runs on Render

| Service | Type | What it does |
|---|---|---|
| bot (`bot-webhook` in `render.yaml`, or your existing Docker web service) | Web service, Docker, **1 instance** | Telegram long polling + download workers + `/health` + Mini App `/api` |
| `bot-miniapp` | Static site | Mini App files (optional) |

There is **no** Postgres, Redis, background worker, webhook or ngrok. State is in memory, so a deploy or
restart clears the queue, bans, stats and the "instant resend" cache. That is expected.

```text
git push main ──▶ GitHub Actions: compileall + pytest + docker build   (checks only)
              └─▶ Render: build root Dockerfile ──▶ /health OK ──▶ swap ──▶ old instance stops
```

- Render rebuilds the bot only when `apps/bot/app/**`, `apps/bot/requirements.txt`, `Dockerfile`,
  `.dockerignore` or `render.yaml` change (`buildFilter`). Docs/tests/scripts/Mini App pushes skip it.
- A code-only change rebuilds one image layer (dependencies and ffmpeg are cached): the build takes
  seconds, then Render waits for `/health`.

## Rules that keep the bot working

1. **One poller per bot token.** Only one process in the world may call `getUpdates` for a token.
   Stop the local bot (`docker compose stop bot`) before Render runs with the same token, or use a
   separate dev bot locally. Two pollers log `409 conflict` and steal each other's messages.
2. **One instance, one process.** `numInstances: 1` and `uvicorn --workers 1`. Never scale out.
3. **A plan that does not sleep.** Polling only runs while the process is up. Free web services sleep
   after idle time and stop receiving messages. Use `starter` or higher.
4. **No webhook.** The bot deletes any webhook on startup. Do not run a `setWebhook` script anymore.

## Environment variables

| Key | Required | Example / default |
|---|---|---|
| `BOT_TOKEN` | yes | from @BotFather |
| `ADMIN_IDS` | recommended | `123456789,987654321` |
| `TERMS_URL` | optional | `https://example.com/terms` |
| `MINIAPP_URL` | optional | `https://bot-miniapp.onrender.com/` (enables the Mini App button) |
| `POLL_TIMEOUT` | no | `25` |
| `MAX_UPLOAD_MB`, `RATE_PER_MINUTE`, `RATE_PER_DAY` | no | `50`, `5`, `30` |
| `WORKER_CONCURRENCY`, `MAX_JOB_ATTEMPTS`, `TEMP_DIR` | no | `2`, `3`, `tmp` |
| `FILE_TTL_MIN` | no | `15`: Mini App "Save to device" links expire and the file is deleted after this |
| `INFO_RATE_PER_MINUTE` | no | `20`: Mini App link previews per user per minute |
| `MAX_DURATION_SEC` | no | `0` = no limit (open question Q4) |

`bot-miniapp` (static site): `API_BASE_URL` (required, the bot service URL) and `BOT_USERNAME`
(optional, without `@`; powers the "Open in Telegram" button when the page is opened in a browser).

Memory: "Save to device" files live on the instance disk under `TEMP_DIR` for up to `FILE_TTL_MIN`.
With `MAX_UPLOAD_MB=50` and `WORKER_CONCURRENCY=2` that stays small; keep both as they are on a 512 MB plan.

Delete leftovers from the old setup; they are ignored but confusing: `DATABASE_URL`, `DB_*`,
`REDIS_URL`, `WEBHOOK_SECRET`, `PUBLIC_BASE_URL`.

## Deploy: existing Docker web service (e.g. `dlbot-d2zj`)

1. **Settings**: Runtime Docker, Dockerfile path `./Dockerfile`, Docker build context `.`,
   Health check path `/health`, Instances 1, plan `starter` or higher.
   Optional, for faster deploys: **Build Filters → Included paths** = the list from `render.yaml`.
2. **Environment**: set the variables above and delete the leftovers.
3. Delete old services if they still exist: `bot-worker`, `bot-redis`, `bot-db` (they cost money).
4. Stop the local bot: `docker compose stop bot`.
5. Push to `main` (Auto-Deploy on), or **Manual Deploy → Deploy latest commit**.

## Deploy: new Blueprint

**New → Blueprint** → select the repo → Render reads `render.yaml` → fill `BOT_TOKEN`, `ADMIN_IDS`,
`TERMS_URL`, `MINIAPP_URL` and (for `bot-miniapp`) `API_BASE_URL` = the bot service URL. Then step 4 above.

## Verify (2 minutes)

1. Render **Logs** show, in order:
   `workers started` → `polling started` → `Uvicorn running on http://0.0.0.0:10000` → `Your service is live`.
2. `curl https://<service>.onrender.com/health` →
   `{"status":"ok","queued":0,"polling":true}` (`polling` turns true after the first successful poll).
3. In Telegram: `/start`, then send a YouTube/TikTok link → 🎬 / 🎵 buttons → the file arrives.
4. `/health` also reports `"ffmpeg": true` and the installed `yt_dlp` version.

## Mini App in BotFather (once)

The bot sets the chat **Menu button** to the Mini App on every start when `MINIAPP_URL` is set, so no
BotFather step is required for that. Optional extras:

1. @BotFather → `/mybots` → your bot → **Bot Settings → Configure Mini App → Enable Mini App** →
   enter `MINIAPP_URL` (e.g. `https://bot-miniapp.onrender.com/`). This adds the **Open** button on
   the bot's profile.
2. For a shareable `t.me/<bot>/<app>` link: `/newapp` → choose the bot → title, description, a
   640×360 image → URL = `MINIAPP_URL` → short name (e.g. `download`).
3. Set `BOT_USERNAME` on `bot-miniapp` so the page shows "Open in Telegram" when opened in a browser.

## Mini App: manual test checklist

Run on iOS, Android and Desktop, in light and dark theme:

- [ ] Paste a YouTube, a TikTok (`vm.tiktok.com`) and a Facebook (`fb.watch`) link: the platform icon
      appears in the field and **Continue** enables. A random site shows "This site isn't supported".
- [ ] Preview shows thumbnail, duration, title (2 lines max), uploader and only the qualities that exist.
- [ ] **This device**, Video 720p → progress with a percentage → **Save to device** saves an MP4 that
      plays (iOS: Files/Photos). Repeat with Audio → MP3.
- [ ] **Bot chat** → the file arrives in the chat; **Open chat** closes the app.
- [ ] Close the app during a download and reopen it from the menu: it resumes on the same job.
- [ ] **Cancel** during a download → "Download cancelled." and back to home.
- [ ] Start a second download while one runs → the running one is shown (one job per user).
- [ ] A private video, a live stream, a playlist link: each shows its own message.
- [ ] A long video at 1080p over 50 MB → "Pick a lower quality or Audio" with a **Try 720p** button.
- [ ] Wait 15 min after "Your file is ready", tap **Save to device** → "The download link has expired".
- [ ] Open `MINIAPP_URL` in a normal browser → "Open this in Telegram".

## Troubleshooting

| Log / symptom | Cause | Fix |
|---|---|---|
| `409 conflict, another bot instance is polling` (continuous) | Same token polled elsewhere (local Docker, an old Render service) | Stop the other one. A few 409s during a deploy are normal: old and new instance overlap briefly. |
| `bot token rejected (401)` | Wrong `BOT_TOKEN` | Fix the env var; Render restarts the service. |
| `"polling": false` stays false | Telegram unreachable or token wrong | Check the two lines above in the logs. |
| Bot silent after some idle time | Free plan went to sleep | Upgrade the plan (rule 3). |
| `Port scan timeout reached, no open ports` | Service is not a web service, or custom start command | Use the Dockerfile `CMD` (binds `$PORT`). |
| `E-TOOBIG` replies | File over 50 MB (Telegram bot upload limit) | Expected; user can choose 🎵 Audio. |
| YouTube `Sign in to confirm you're not a bot` (Mini App shows "YouTube is blocking our server") | YouTube blocks Render's datacenter IPs | Owner decision (CLAUDE.md Open Questions); do not add cookies/proxies without approval. |
| Mini App "Save to device" does nothing | Telegram client older than Bot API 8.0 | The app falls back to opening the link in the browser; update Telegram for in-app saving. |
| Mini App shows "The download link has expired" | More than `FILE_TTL_MIN` since the file was ready, or the service restarted | Download again. Files never survive a deploy. |
| Extractor errors after a platform change | yt-dlp is outdated | Bump the `yt-dlp==` pin in `apps/bot/requirements.txt` to the latest release, run `pytest` and a real download, push. |

## Rollback

Render → the service → **Events** → pick the previous successful deploy → **Rollback**.
