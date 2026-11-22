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
   Keep the service at **one instance** (in-memory state, one poller per token) on a plan that does
   not sleep when idle. Nothing to register: the bot deletes any old webhook and starts polling.

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
