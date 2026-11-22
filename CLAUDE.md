# CLAUDE.md — Media Downloader Bot (v1 / MVP)

Telegram bot: user sends a public TikTok, YouTube or Facebook link → bot offers 🎬 Video / 🎵 Audio → bot downloads with yt-dlp and sends the file back. No media is stored after delivery.

This file is the source of truth for Claude Code. Read it fully before changing code.

## 1. Working agreements

- **Plan, then build.** For each task below: state the plan in 3–5 lines, make the change, run the verification, then report.
- **Small steps.** One task = one commit (Conventional Commits: `feat:`, `fix:`, `test:`, `chore:`). Do not batch unrelated changes.
- **Ask before** changing the stack, adding a dependency, changing the hosting model, or touching anything in "Boundaries".
- **Verify before claiming done.** Run `pytest` and `python -m compileall app` and show the output. Do not say something works unless you ran it.
- **No invented APIs.** If you are unsure about a yt-dlp option or a Telegram Bot API field, check the official docs or the installed package source (`python -c "import yt_dlp, inspect; ..."`) rather than guessing.

## 2. Stack (fixed)

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | |
| Webhook | FastAPI + uvicorn | Render **web service**. Must answer Telegram in <2 s. Enqueue only, never download here. |
| Worker | Plain Python process (`python -m app.worker`) | Render **background worker**. Downloads, uploads, cleans up. |
| Queue / cache / rate limit | Redis (Render Key Value) | TCP `redis://` URL. Eviction policy **must be `noeviction`** (queue must not be evicted). |
| Database | PostgreSQL (Render Postgres) | Users, request log, media cache metadata, admin audit. |
| Download | yt-dlp + ffmpeg | Called as a Python library. Never via `shell=True` or a shell string. |
| Telegram | Bot API over HTTPS (`httpx`) | Webhook with `secret_token`. Private chats only in v1. |
| Hosting | Render (Blueprint in `render.yaml`) | Docker runtime for both services. |
| Removed | MQTT | Out of scope. Do not reintroduce without an approved requirement. |

Note: the original brief said "Vercel". Vercel is not used for the worker because downloads and ffmpeg need a persistent process. The web service runs on Render.

## 3. Architecture

```
Telegram ──webhook──▶ web (FastAPI, app/main.py)
                        │ validate secret, dedupe update_id, rate limit, route
                        ├──▶ Redis  (req:*, queue:downloads, claim:*, cancel:*, fid:*)
                        └──▶ Postgres (users, requests)
Redis queue ──BRPOP──▶ worker (app/worker.py, N threads)
                        ├── yt-dlp download to a per-job temp dir
                        ├── Telegram sendVideo / sendAudio (file_id returned)
                        ├──▶ Redis fid:* cache, Postgres media_cache + requests
                        └── rmtree temp dir in `finally`
```

### Module map

| File | Responsibility |
|---|---|
| `app/main.py` | FastAPI app, `/health`, `/telegram/webhook` (secret check, dedupe, always 200) |
| `app/handlers.py` | Routes messages, commands, inline callbacks. Builds jobs. No downloading. |
| `app/worker.py` | Job loop, retries, cancel checks, status edits, cleanup |
| `app/downloader.py` | yt-dlp wrapper, `DownloadError` codes, `classify_error` |
| `app/tg.py` | Telegram HTTP client; `TelegramError` on any `ok: false` |
| `app/store.py` | All Redis access (queue, pending requests, cache, rate limit, cancel, heartbeat) |
| `app/db.py` | All Postgres access and the migration runner |
| `app/messages.py` | **All user-facing text.** Never hard-code user strings elsewhere. |
| `app/url_utils.py` | URL extraction, host allow-list, `url_hash` |
| `app/config.py` | `Settings` (pydantic-settings), read from env / `.env` |
| `app/log.py` | JSON logging. Pass `extra={"req_id", "user_id", "platform", "code"}`. |
| `app/api.py` | Mini App HTTP API under `/api` (me, create/list/get/cancel downloads). Read-only on polls. |
| `app/jobs.py` | `submit()`: cache hit or enqueue. Shared by chat callbacks and the Mini App API. |
| `app/webapp_auth.py` | Validates Mini App `initData` (HMAC with the bot token, `auth_date` max age) |
| `migrations/*.sql` | Schema. Applied on web startup. Must be idempotent. |
| `scripts/set_webhook.py` | One-off: register the Telegram webhook |

Paths above are relative to `apps/bot/`. The repo is a monorepo:

| Path | What |
|---|---|
| `apps/bot/` | Python web + worker (one Docker image) |
| `apps/miniapp/` | Telegram Mini App: static HTML/CSS/ES modules, no build, no deps. See its README. Copy lives in `src/strings.js`. Tests: `npm test` (Node 20+). Hosted as the Render static site `bot-miniapp`. |

Mini App job stages (Redis `job:<db_id>`, then the DB): `queued → downloading → (retrying) → uploading → done | failed | cancelled`. The API reports `expired` when a job is not final, its progress hash is gone, and it is older than `REQUEST_TTL`.

### Callback protocol

`callback_data` is `dl|<v|a>|<token>` (≤64 bytes). The **token** keys a Redis entry `req:<token>` holding `{url, platform, user_id}` for 1 hour. Never put a URL directly in callback data.

### Job payload (Redis list `queue:downloads`)

```json
{"token": "...", "db_id": "uuid", "fmt": "video|audio", "url": "...", "platform": "youtube|tiktok|facebook",
 "user_id": 123, "chat_id": 123, "message_id": 456, "attempt": 1}
```

### Error codes (user-facing, from `messages.ERROR_TEXT`)

| Code | Meaning | Retried? |
|---|---|---|
| `E-UNSUPPORTED` | Host not allowed or extractor cannot read link | no |
| `E-PRIVATE` | Private, members-only, age-gated, login required | no |
| `E-TOOBIG` | Over `MAX_UPLOAD_MB` (download or Telegram 413) | no |
| `E-FAILED` | Anything else (network, extractor break, unexpected) | yes, up to `MAX_JOB_ATTEMPTS` |

## 4. Functional scope (v1)

Reference IDs from the system analysis. Implement and test each.

| ID | Requirement | Current status in scaffold |
|---|---|---|
| FR-01 | Detect TikTok/YouTube/Facebook links, incl. short links | Done (`url_utils`). Test short links for real. |
| FR-02 | Inline keyboard 🎬 Video / 🎵 Audio | Done |
| FR-03 | Download, send with caption | Done (verify end-to-end) |
| FR-04 | TikTok no-watermark when the extractor exposes it; otherwise say so | **TODO** (see Phase 3) |
| FR-05 | Reject invalid/unsupported links clearly | Done |
| FR-06 | Progress states queued → downloading → uploading | Done (message edits) |
| FR-07 | Rate limit 5/min, 30/day, configurable | Done (`store.rate_limit_hit`). Needs a test. |
| FR-08 | Refuse files over limit, suggest audio | Done |
| FR-09 | Instant resend via cached `file_id` | Done (Redis). Postgres `media_cache` is write-only for now. |
| FR-10 | Log each request | Done (`requests` table) |
| FR-11 | Admin ban/unban | Done (`/admin ban|unban`) |
| FR-12 | Second language via `/language` | **Not started** (Could priority) |

Commands: `/start /help /status /cancel /terms /admin`. Plus `/language` when FR-12 is built.

## 5. Non-functional requirements (must hold)

- **NFR-01** webhook returns 200 within 2 s. Keep handler work small.
- **NFR-03** failed jobs retry with backoff; updates are idempotent (`dedupe_update`).
- **NFR-05** verify `X-Telegram-Bot-Api-Secret-Token` with `hmac.compare_digest`. Never log the bot token.
- **NFR-06** only allow-listed hosts reach yt-dlp. No shell. SSRF: never fetch a URL that isn't on the allow-list.
- **NFR-07** temp media is deleted in `finally`. Request logs retained 90 days (add a cleanup job in Phase 5).
- **NFR-08** JSON logs; alert when error rate >20% over 15 min (configure in Render, Phase 6).
- **NFR-09** yt-dlp pinned to an exact version. Weekly smoke-test job (Phase 5).

## 6. Boundaries (do NOT do without asking)

- Do not store downloaded media beyond the job. No S3, no persistent volumes for media.
- Do not add DRM, login, cookie, or proxy bypass code. If YouTube blocks datacenter IPs, the decision goes back to the owner (see Open Questions).
- Do not add group chat support, payments, a web UI, or playlists/channels.
- Do not add MQTT.
- Do not commit `.env` or any secret. Secrets belong in Render env vars.
- Do not change the message wording in `messages.py` without asking. Product owns the copy.

## 7. Commands

```bash
# setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# quality gate (run before every commit; from apps/bot)
python -m compileall -q app scripts tests
pytest -q
# Mini App (from apps/miniapp)
npm run check && npm test

# local stack (web :8000, worker, postgres, redis)
cp .env.example .env && docker compose up --build

# run without docker (needs local postgres + redis + ffmpeg)
uvicorn app.main:app --reload --port 8000     # terminal 1
python -m app.worker                          # terminal 2

# register webhook (after public URL exists, e.g. ngrok locally or Render in prod)
PYTHONPATH=. python scripts/set_webhook.py
```

## 8. Build plan for Claude Code (do in order)

Each phase ends with a verification gate. Do not start the next phase until the gate passes.

### Phase 1 — Verify the scaffold
- [ ] Create a venv, install `requirements-dev.txt`.
- [ ] `pytest -q` passes (URL, downloader classification, messages).
- [ ] `docker compose up --build` starts web, worker, db, redis. `curl localhost:8000/health` returns `{"status":"ok"}`.
- **Gate:** all three succeed. Report any failure with the exact error before changing code.

### Phase 2 — Unit tests for core logic
- [ ] `tests/test_store.py`: rate limit (per minute and per day, using a Redis test instance or `fakeredis` added to dev deps), `claim` is one-shot, `dedupe_update`, `cancel` flag.
- [ ] `tests/test_handlers.py`: mock `TelegramClient` and `db`/`store` with `monkeypatch`. Cover: plain link → keyboard; unsupported host → UNSUPPORTED; `/start`, `/help`, unknown command; non-admin `/admin` gets UNKNOWN_COMMAND; banned user gets BANNED; callback from another user is ignored; duplicate tap enqueues once; cached file_id is sent without enqueueing.
- [ ] `tests/test_webhook.py`: FastAPI `TestClient`. Wrong secret → 403. Duplicate `update_id` → processed once. Missing secret header → 403.
- **Gate:** `pytest -q` green. Coverage of `handlers.py` and `main.py` ≥ 80% (`pytest --cov` with `pytest-cov` added to dev deps).

### Phase 3 — yt-dlp spike and FR-04
- [ ] Write `scripts/spike_download.py`: takes a list of URLs (10 per platform, public videos), runs `downloader.download` for `video` and `audio`, prints title, size, duration, elapsed time, and the error code. Output a Markdown table.
- [ ] Run it **from a Render worker** (one-off job or temporary worker). Record results in `docs/spike-results.md`.
- [ ] Pin `yt-dlp` to the exact version used in the spike (`requirements.txt`).
- [ ] For TikTok: inspect `info["formats"]` for watermark-free streams. Implement a format filter in `downloader._options` if one exists. If none exists, the bot must say so (FR-04): add `messages.TIKTOK_WATERMARK_NOTE` and show it in the caption.
- **Gate:** the spike table exists. Each platform has a pass or fail verdict with evidence. If YouTube fails from Render IPs, **stop and report** (decision needed: cookies, proxy, or drop YouTube from v1).

### Phase 4 — Protection and UX polish
- [ ] Add FR-06 "retrying" path test (`E-FAILED` re-enqueues with `attempt+1`, stops at `MAX_JOB_ATTEMPTS`).
- [ ] Implement `/cancel` end-to-end test: cancel during download stops before upload and sets `cancelled`.
- [ ] Implement FR-12: `/language` with an inline keyboard (en, km). Add Khmer strings to `messages.py` behind a `t(key, lang)` helper. Store language in `users.language`. Ask for the Khmer copy from the owner before writing it.
- **Gate:** tests green; manual test in Telegram of happy path (video + audio), rate limit, `/cancel`, unsupported link.

### Phase 5 — Operations
- [ ] Add `scripts/cleanup_logs.py` (delete `requests` older than 90 days; run as a Render cron job, schedule weekly). Add to `render.yaml` as `type: cron`.
- [ ] Add `scripts/smoke_test.py`: downloads one public sample URL per platform, exits non-zero on failure. Add a `.github/workflows/weekly-smoke.yml` scheduled workflow.
- [ ] Add a GitHub Actions CI workflow: `pip install`, `compileall`, `pytest`, on every push.
- **Gate:** CI green on `main`.

### Phase 6 — Release
- [ ] Create the Render Blueprint from `render.yaml`. Fill `sync: false` vars in the dashboard.
- [ ] Run `PYTHONPATH=. python scripts/set_webhook.py` against the production URL.
- [ ] Set Render alerts: web 5xx rate, worker restarts, Redis memory >80%.
- [ ] Soft-launch with 20–50 users. Check `/admin stats` daily.
- **Gate:** 48 h with error rate under 20% and no worker restarts loop.

## 9. Open questions (owner must answer; do not guess)

| # | Question | Default used in code |
|---|---|---|
| Q1 | Max file size | 50 MB (`MAX_UPLOAD_MB`). Self-hosted Bot API (2 GB) is a later option. |
| Q2 | Hosting for worker | Render background worker (decided) |
| Q3 | Legal owner, takedown contact, terms text | `TERMS_URL` env var; copy in `messages.terms` |
| Q4 | YouTube length limit in v1 | None enforced yet. Add `MAX_YOUTUBE_SECONDS` if decided. |
| Q5 | Languages | English only. Khmer is requested in Phase 4. |

## 10. Definition of done (for any task)

1. Code follows the module map. User text is in `messages.py`.
2. New behavior has a test. `pytest -q` and `compileall` pass.
3. No secrets, no shell calls, no media left on disk.
4. Logs use `extra=` fields, not string-formatted IDs.
5. Commit message follows Conventional Commits.
6. The change is reported with: what changed, how it was verified (command + result), and what is still open.
