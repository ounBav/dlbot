# Media Downloader Bot

Telegram bot that downloads public TikTok, YouTube and Facebook videos (or their audio) and sends them back. Built for Render as **one web service**: a Telegram long-polling loop and the download workers run in the same process (FastAPI only serves `/health` and the Mini App API). No database and no Redis: state (queue, cache, rate limits, bans, request log) is an in-memory singleton in `app/store.py`, so it resets on every restart/redeploy.

Flow: link → 🎬/🎵 buttons → job queued in memory → worker thread downloads with yt-dlp into `tmp/job-*/` → file sent to Telegram → the job folder is deleted (always, in `finally`).

See `CLAUDE.md` for architecture, conventions and the build plan.

## Quick start (local)

```bash
cp apps/bot/.env.example apps/bot/.env   # fill in BOT_TOKEN, ADMIN_IDS
docker compose up --build               # bot (polling + workers, /health on :8000), Mini App :5173
```

The bot uses **long polling** (`getUpdates`): no webhook, no public URL and no ngrok. Message it in Telegram as
soon as `docker compose logs bot` shows `polling started`.

> Only one process can poll per bot token. If Render is running with the same token, both get
> `409 Conflict`. Use a separate dev bot (from @BotFather) for local work.

Run tests without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

## Deploy (Render)

Full guide, checklist and troubleshooting: [docs/RENDER_DEPLOY.md](docs/RENDER_DEPLOY.md).

1. Push the repo to GitHub.
2. In Render: **New → Blueprint**, select the repo. It reads `render.yaml`.
3. Fill the `sync: false` variables (`BOT_TOKEN`, `ADMIN_IDS`, `TERMS_URL`).
   Keep the service at **one instance** (in-memory state, one poller per token). On the free plan, set
   up the keep-alive pinger below or the bot stops answering after 15 idle minutes. Nothing to register: the bot deletes any old webhook and starts polling.

### Keep-alive on Render (free plan)

The free plan puts a web service to sleep after **15 minutes without inbound HTTP traffic**. While it
sleeps the process is stopped, so polling stops and commands get no reply. An external pinger keeps it
awake by calling `/health` more often than every 15 minutes.

1. **cron-job.org**: create a cronjob → URL `https://<app>.onrender.com/health` → every **10 minutes**
   → request timeout **30 s** → turn on failure email notifications.
   (Or **UptimeRobot**: an HTTP(s) monitor on the same URL, 5-minute interval.)
2. **Why under 15 minutes:** Render sleeps after 15 idle minutes; 10 minutes leaves margin for a late ping.
   A ping that arrives while the service sleeps wakes it (cold start ≈ 30–60 s, hence the 30 s timeout
   and Telegram keeping the pending updates).
3. **Hours:** one always-on service uses ≈ 720–744 h/month, inside the 750 free instance hours per
   workspace. Do not keep two free services awake (the static Mini App site does not use instance hours).
4. **Start command must use `--workers 1`** (the Dockerfile `CMD` already does). More workers = more
   pollers on one token = Telegram `409 Conflict`, and split in-memory state.
5. **Verify:** Render logs show the pinger every 10 minutes and no "spinning down" lines; a bot command
   sent an hour later replies instantly. `/health` access lines are filtered out of the log, so check
   cron-job.org's execution history (or `uptime_sec` in the `/health` body, which keeps growing).

`/health` (GET and HEAD, no auth) always answers 200:
`{"ok": true, "uptime_sec": 3600, "polling": true, "last_update_at": "2026-11-22T10:00:00+00:00", ...}`.
`polling: false` means the poll loop crashed and is restarting (5 s → 10 s → 30 s → 60 s backoff);
it is also logged as a warning. `KEEPALIVE_SELF_PING=true` makes the service ping its own
`RENDER_EXTERNAL_URL/health` every 10 minutes, but it only helps while the service is already awake:
the external pinger is still required.

## Project layout

```
app/
  main.py        process entry: starts the poller + worker threads; FastAPI /health and /api
  poller.py      Telegram long polling (getUpdates) -> handlers
  handlers.py    message, command and button routing
  worker.py      worker threads: download, upload, cache, temp cleanup
  downloader.py  yt-dlp wrapper and error codes
  tg.py          Telegram Bot API client
  store.py       in-memory singleton: queue, cache, rate limit, cancel flags, users, request log
  messages.py    all user-facing text
  url_utils.py   link detection and host allow-list
tmp/             per-job download folders (git-ignored, emptied after each send)
scripts/         deploy/monitor/validate helpers
tests/           pytest suite
```
