# Mini App (`apps/miniapp`)

Telegram Mini App for the media downloader bot. Users paste a link, pick Video or Audio, and
watch progress. The file itself is still delivered **in the bot chat** by the worker; the app never
serves media.

Static HTML/CSS/ES modules. No build step, no npm dependencies; only Telegram's
`telegram-web-app.js` is loaded at runtime.

## Files

| File | Role |
|---|---|
| `index.html` | Page shell. All visible text is bound from `src/strings.js` via `data-s` attributes. |
| `styles.css` | Uses Telegram theme variables (`--tg-theme-*`), with light/dark fallbacks. |
| `config.js` | `window.MINIAPP_CONFIG.apiBase`. Generated on Render from `API_BASE_URL`. |
| `src/app.js` | Controller: Telegram SDK, form, MainButton, polling, rendering. |
| `src/api.js` | Client for `apps/bot/app/api.py` (`Authorization: tma <initData>`). |
| `src/logic.js` | Pure helpers (URL detection, stages, backoff). Unit-tested. |
| `src/strings.js` | **All user-facing copy.** Error meanings mirror `apps/bot/app/messages.py`. |
| `tests/` | `node --test` suites (no dependencies). |

## How it talks to the bot

```
Mini App ── GET  /api/me ──────────────▶ user, usage today, limits, platform allow-list
         ── POST /api/downloads ───────▶ bot posts a status message in the user's chat,
                                         job is queued (or a cached file is sent at once)
         ── GET  /api/downloads ───────▶ last 20 jobs with live stage (polled while any job is active)
         ── POST /api/downloads/{id}/cancel
```

- **Auth:** the server validates Telegram's signed `initData`. The app never trusts `initDataUnsafe`.
- **Polling:** a single list request every 1.5 s, backing off to 3 s and then 6 s. It stops when every
  job is final (`done | failed | cancelled | expired`) and pauses while the app is hidden.
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
