"""Webhook API. Runs on Render as a web service. Must answer Telegram fast (NFR-01)."""

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import db, store
from .config import get_settings
from .handlers import handle_update
from .log import setup_logging
from .tg import TelegramClient

log = logging.getLogger("webhook")
tg_client: TelegramClient | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global tg_client
    setup_logging()
    db.run_migrations()
    tg_client = TelegramClient(get_settings().bot_token)
    yield
    tg_client.http.close()


app = FastAPI(title="Media Downloader Bot", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/health")
def health():
    try:
        store.ping()
    except Exception:
        raise HTTPException(status_code=503, detail="redis unavailable")
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    # NFR-05: reject requests that do not carry Telegram's secret token
    if not hmac.compare_digest(x_telegram_bot_api_secret_token or "", get_settings().webhook_secret):
        raise HTTPException(status_code=403)

    update = await request.json()
    update_id = update.get("update_id")
    if update_id is not None and not store.dedupe_update(update_id):
        return {"ok": True}  # already processed; Telegram retried

    try:
        await run_in_threadpool(handle_update, update, tg_client)
    except Exception:
        # Always return 200 so Telegram does not retry in a storm; the error is logged.
        log.exception("update handling failed")
    return {"ok": True}
