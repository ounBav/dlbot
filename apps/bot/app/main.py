"""The bot process: Telegram long polling + in-process download workers + a small HTTP API.

One process, one Render web service:
  - Poller (app/poller.py)     getUpdates loop -> handlers.handle_update (route + enqueue only)
  - WorkerPool (app/worker.py) download -> send -> delete temp files
  - FastAPI                    /health and the Mini App API (/api). No webhook endpoint.
State lives in app/store.py (in memory), so this must run as exactly one process.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import api, messages, store
from .config import get_settings
from .log import setup_logging
from .poller import Poller
from .tg import TelegramClient
from .worker import WorkerPool

log = logging.getLogger("bot")
tg_client: TelegramClient | None = None
workers: WorkerPool | None = None
poller: Poller | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global tg_client, workers, poller
    setup_logging()

    s = get_settings()
    tg_client = TelegramClient(s.bot_token)
    api.configure(tg_client)
    try:
        # idempotent; refreshes the Menu button (opens the Mini App when MINIAPP_URL is set)
        tg_client.set_commands(messages.BOT_COMMANDS, web_app_url=s.miniapp_url,
                               web_app_label=messages.MENU_BUTTON)
    except Exception:
        log.exception("could not register bot commands")  # never block startup on this

    workers = WorkerPool(tg_client, s.worker_concurrency)
    workers.start()
    poller = Poller(tg_client, s.poll_timeout)
    poller.start()
    yield
    poller.stop()       # stop taking new updates first
    workers.stop()      # then let in-flight downloads finish (bounded)
    tg_client.http.close()


app = FastAPI(title="Media Downloader Bot", docs_url=None, redoc_url=None, lifespan=lifespan)

# The Mini App is a separate static site, so its origin must be allowed explicitly.
# Auth is a header (no cookies), so credentials stay off.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)
app.include_router(api.router)


@app.get("/health")
def health():
    if workers is not None and not workers.alive():
        raise HTTPException(status_code=503, detail="workers stopped")
    if poller is not None and not poller.alive():
        raise HTTPException(status_code=503, detail="poller stopped")
    return {"status": "ok", "queued": store.get_store().queue_size(),
            "polling": poller is not None and poller.last_ok is not None}
