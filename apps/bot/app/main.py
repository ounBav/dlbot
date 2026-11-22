"""The bot process: Telegram long polling + in-process download workers + a small HTTP API.

One process, one Render web service:
  - Poller (app/poller.py)     getUpdates loop -> handlers.handle_update (route + enqueue only)
  - WorkerPool (app/worker.py) download -> send -> delete temp files
  - FastAPI                    /health and the Mini App API (/api). No webhook endpoint.
State lives in app/store.py (in memory), so this must run as exactly one process.
"""

import asyncio
import logging
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from yt_dlp.version import __version__ as yt_dlp_version

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
_started_at = time.monotonic()
_HAS_FFMPEG = shutil.which("ffmpeg") is not None   # checked once: /health must stay fast


async def _self_ping(url: str, interval: float, transport: httpx.AsyncBaseTransport | None = None):
    """KEEPALIVE_SELF_PING: GET our own public /health every `interval` seconds.

    This only keeps the service awake while it is ALREADY awake. Once Render has put it to sleep
    (or after a restart that nobody visits) this task is not running, so it cannot wake anything.
    An external pinger (cron-job.org / UptimeRobot, see README) is still required."""
    async with httpx.AsyncClient(timeout=30, transport=transport) as client:
        while True:
            await asyncio.sleep(interval)
            try:
                r = await client.get(url)
                log.debug("self-ping %s", r.status_code)
            except httpx.HTTPError as exc:
                log.warning("self-ping failed: %s", exc.__class__.__name__, extra={"code": "E-NETWORK"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    global tg_client, workers, poller, _started_at
    setup_logging()
    _started_at = time.monotonic()

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

    ping_task = None
    if s.keepalive_self_ping:
        if s.render_external_url:
            url = s.render_external_url.rstrip("/") + "/health"
            ping_task = asyncio.create_task(_self_ping(url, s.keepalive_interval_sec))
        else:
            log.warning("KEEPALIVE_SELF_PING is on but RENDER_EXTERNAL_URL is empty; not pinging")
    yield
    if ping_task is not None:
        ping_task.cancel()
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
app.include_router(api.files_router)   # Mini App "save to device" links (token, no initData)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    """Liveness for Render and uptime pingers. Always 200, no I/O: a pinger must never see a
    failure just because polling is restarting. Problems are reported in the body and logged."""
    polling = poller is not None and poller.alive()
    workers_ok = workers is not None and workers.alive()
    if not polling:
        log.warning("health: polling is not running", extra={"code": "E-POLLER"})
    if not workers_ok:
        log.warning("health: workers are not running", extra={"code": "E-WORKER"})
    last = poller.last_update_at if poller is not None else None
    return {
        "ok": True,
        "status": "ok" if polling and workers_ok else "degraded",
        "uptime_sec": int(time.monotonic() - _started_at),
        "polling": polling,
        "last_update_at": datetime.fromtimestamp(last, timezone.utc).isoformat() if last else None,
        "workers": workers_ok,
        "queued": store.get_store().queue_size(),
        "ffmpeg": _HAS_FFMPEG,
        "yt_dlp": yt_dlp_version,
    }
