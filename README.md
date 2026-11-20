# Media Downloader Bot

Telegram bot that downloads public TikTok, YouTube and Facebook videos (or their audio) and sends them back. Built for the Render platform: one web service (webhook), one background worker, Postgres and Redis.

See `CLAUDE.md` for architecture, conventions and the build plan.

## Quick start (local)

```bash
cp .env.example .env          # fill in BOT_TOKEN, WEBHOOK_SECRET, ADMIN_IDS
docker compose up --build     # web on :8000, worker, Postgres, Redis
```

Run tests without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

## Deploy (Render)

1. Push the repo to GitHub.
2. In Render: **New → Blueprint**, select the repo. It reads `render.yaml`.
3. Fill the `sync: false` variables (`BOT_TOKEN`, `PUBLIC_BASE_URL`, `ADMIN_IDS`, `TERMS_URL`).
4. After the first deploy, register the webhook once:
   `PYTHONPATH=. python scripts/set_webhook.py` (with the production env vars).

## Project layout

```
app/
  main.py        FastAPI webhook (/telegram/webhook, /health)
  handlers.py    message, command and button routing
  worker.py      job loop: download, upload, cache, cleanup
  downloader.py  yt-dlp wrapper and error codes
  tg.py          Telegram Bot API client
  store.py       Redis: queue, cache, rate limit, cancel flags
  db.py          Postgres access and migrations runner
  messages.py    all user-facing text
  url_utils.py   link detection and host allow-list
migrations/      SQL, applied on web startup (idempotent)
scripts/         set_webhook.py
tests/           pytest suite
```
