# Mini App (`apps/miniapp`)

Telegram Mini App for the media downloader bot. Flow: **home** (paste a link) → **preview**
(thumbnail, title, Video/Audio, quality, delivery) → **progress** (live percentage, cancel) →
**ready** (save the file) or **error** (specific message plus the next step).
TikTok profile: **profile** (username, how many, pick videos or **Select all**) → **batch** (groups of
10 sent one after another, per-video status, Stop sending). The batch keeps running if the app closes.

Two deliveries:
- **This device** (default): the worker keeps the finished file for `FILE_TTL_MIN` (15 min) behind
  `GET /api/files/<token>`; **Save to device** calls `Telegram.WebApp.downloadFile` (Bot API 8.0+)
  or falls back to `openLink`.
- **Bot chat**: the worker sends the file to the user's chat, exactly like the chat flow.

Static HTML/CSS/ES modules. No build step, no npm dependencies; only Telegram's
`telegram-web-app.js` is loaded at runtime.

## Files

| File | Role |
|---|---|
| `index.html` | Page shell. All visible text is bound from `src/strings.js` via `data-s` attributes. |
| `styles.css` | Uses Telegram theme variables (`--tg-theme-*`), with light/dark fallbacks. |
| `config.js` | `window.MINIAPP_CONFIG` = `{apiBase, botUsername}`. Generated on Render from `API_BASE_URL` and `BOT_USERNAME`. |
| `src/app.js` | Controller: screen state machine, Telegram SDK (MainButton/BackButton, haptics, theme, closing confirmation), polling, session resume. |
| `src/api.js` | Client for `apps/bot/app/api.py` (`Authorization: tma <initData>`). |
| `src/logic.js` | Pure helpers (URL detection, stages, backoff). Unit-tested. |
| `src/strings.js` | **All user-facing copy.** Error meanings mirror `apps/bot/app/messages.py`. |
| `tests/` | `node --test` suites (no dependencies). |

## How it talks to the bot

```
Mini App ── GET  /api/me ──────────────▶ user, usage, limits, platform allow-list, qualities, file TTL
         ── POST /api/info ────────────▶ preview: title, uploader, duration, thumbnail, qualities
         ── POST /api/downloads ───────▶ {url, format, quality?, delivery} → job id
                                         (409 E-BUSY with the running job's id: one job per user)
         ── GET  /api/downloads/{id} ──▶ status, progress 0-1, file {name, size, download_path, expires_at}
         ── POST /api/downloads/{id}/cancel
         ── GET  /api/downloads ───────▶ recent downloads (home screen)
         ── POST /api/tiktok/scan ─────▶ {username, limit 10|50|100|"all"} → one-time listing
         ── POST /api/tiktok/scan/{id}/send ▶ {video_ids} → batch (sent in groups of PROFILE_BATCH_SIZE)
                                         (409 E-BUSY with the running batch id: one batch per user)
         ── GET  /api/tiktok/batches/{id} ──▶ per-video status, current group, counts
         ── POST /api/tiktok/batches/{id}/cancel · POST /api/tiktok/scan/discard (pagehide, keepalive)
Telegram ── GET  /api/files/{token} ───▶ the file (attachment; token instead of initData; 410 when expired)
```

- **Auth:** the server validates Telegram's signed `initData`. The app never trusts `initDataUnsafe`.
- **Polling:** `GET /api/downloads/{id}` every 1.5 s while a job runs; on network errors it backs off
  (3 s, 6 s, 12 s). It pauses while the app is hidden and resumes on return.
- **Resume:** the active job id is kept in `sessionStorage`; reopening the app returns to its progress
  or ready screen.
- **Cache busting:** Telegram's webview caches hard. Bump `?v=` in `index.html` and the imports at the
  top of `src/app.js` together on every release (the static site also sends `no-cache`).
- **E-NOCHAT:** if the user never pressed Start, the app asks Telegram for write access
  (`requestWriteAccess`, Bot API 6.9+), then retries once.
- **Platform hints** use the host list returned by `/api/me`, so the app and the server can't disagree.
  The server re-validates every URL anyway (NFR-06).
- **No `?api=` override.** A crafted link could otherwise send a user's signed initData to a third-party
  server, which could replay it within `MINIAPP_AUTH_MAX_AGE`.

## Develop

```bash
npm test             # unit tests (Node 20+)
npm run check        # syntax check
docker compose up    # from the repo root: web :8000, worker, db, redis, miniapp :5173
```

Telegram only opens HTTPS Mini Apps. To try it in Telegram locally:

1. Tunnel port 5173 (for example `ngrok http 5173`) and the bot API port 8000 (the bot itself polls Telegram and needs no tunnel).
2. Set `apiBase` in `config.js` to the **HTTPS** URL of the web service.
3. In `apps/bot/.env`, set `MINIAPP_URL=<tunnel URL>` and add the tunnel origin to `CORS_ORIGINS`.
4. Restart `bot`. On startup it sets the chat Menu button to open the Mini App; `/start` also shows an
   "Open app" button.

## Deploy (Render)

The `bot-miniapp` static site in `render.yaml` copies the files to `dist/` and writes `config.js`
from `API_BASE_URL`. It redeploys only when `apps/miniapp/**` changes. After the first deploy:

1. `bot-miniapp` → set `API_BASE_URL` to the `bot-webhook` URL.
2. `bot-webhook` → set `MINIAPP_URL` to the `bot-miniapp` URL (this also allows it in CORS).
3. Optionally, in @BotFather, use `/newapp` to get a `t.me/<bot>/<app>` direct link.
