"""HTTP API for the Telegram Mini App (apps/miniapp). Mounted under /api by app/main.py.

Every call carries `Authorization: tma <initData>`; the user id comes only from validated initData.
Downloads reuse the chat pipeline: the bot posts a status message in the user's private chat,
the worker edits it and sends the file there. The API only reports progress; it never serves media.
Errors are `{"detail": {"code": "E-...", ...}}`.
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from . import db, jobs, messages, store
from .config import get_settings
from .tg import TelegramClient, TelegramError
from .url_utils import PLATFORM_HOSTS, PLATFORM_LABELS, detect_platform, extract_url
from .webapp_auth import InitDataError, validate

log = logging.getLogger("api")


def _no_store(response: Response) -> None:
    # Telegram webviews cache aggressively; polled job status must always be fresh.
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api", dependencies=[Depends(_no_store)])

# "expired": the DB row never reached a final state and its live progress is gone
# (worker lost the job, or it sat in the queue past REQUEST_TTL). Shown as final so the UI stops polling.
FINAL_STAGES = {"done", "failed", "cancelled", "expired"}
STALE_AFTER = timedelta(seconds=store.REQUEST_TTL)
_tg: TelegramClient | None = None


def configure(tg: TelegramClient) -> None:
    global _tg
    _tg = tg


def get_tg() -> TelegramClient:
    if _tg is None:
        raise HTTPException(status_code=503, detail={"code": "E-FAILED"})
    return _tg


def _fail(status: int, code: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, **extra})


def current_user(authorization: str | None = Header(default=None)) -> dict:
    scheme, _, init_data = (authorization or "").partition(" ")
    if scheme.lower() != "tma" or not init_data:
        raise _fail(401, "E-AUTH")
    s = get_settings()
    try:
        user = validate(init_data, s.bot_token, s.miniapp_auth_max_age)
    except InitDataError as exc:
        log.info("rejected initData", extra={"code": str(exc)})
        raise _fail(401, "E-AUTH")
    # Read-only on purpose: the UI polls, so writes happen only in /me and POST /downloads.
    if db.is_banned(user["id"]):
        raise _fail(403, "E-BANNED")
    return user


def _remember(user: dict) -> None:
    """Create/refresh the users row (requests.telegram_id references it)."""
    db.upsert_user(user["id"], user.get("username"))


def _is_stale(created_at) -> bool:
    if not isinstance(created_at, datetime):
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created_at > STALE_AFTER


def _job_view(row: dict) -> dict:
    """DB row + live Redis stage while the job is still running."""
    status = row["status"]
    title = row.get("title")
    if status not in FINAL_STAGES:
        progress = store.get_progress(row["id"])
        if progress is None and _is_stale(row.get("created_at")):
            status = "expired"
        else:
            progress = progress or {}
            status = progress.get("stage") or "queued"
            title = title or progress.get("title") or None
    return {
        "id": row["id"],
        "url": row["url"],
        "platform": row["platform"],
        "format": row["format"],
        "status": status,
        "error_code": row.get("error_code"),
        "title": title,
        "created_at": row["created_at"],
    }


def _owned_request(job_id: str, user_id: int) -> dict:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise _fail(404, "E-NOTFOUND")
    row = db.get_request(job_id, user_id)
    if row is None:
        raise _fail(404, "E-NOTFOUND")
    return row


@router.get("/me")
def me(user: dict = Depends(current_user)):
    _remember(user)
    s = get_settings()
    return {
        "user": {"id": user["id"], "first_name": user.get("first_name", ""), "username": user.get("username")},
        "usage_today": store.usage_today(user["id"]),
        "limit_per_day": s.rate_per_day,
        "limit_per_minute": s.rate_per_minute,
        "max_upload_mb": s.max_upload_mb,
        # Hosts let the UI hint "TikTok link detected" without a second copy of the allow-list.
        # The server still re-validates every submitted URL (NFR-06).
        "platforms": [{"key": k, "label": v, "hosts": sorted(PLATFORM_HOSTS[k])}
                      for k, v in PLATFORM_LABELS.items()],
        "terms_url": s.terms_url or None,
    }


class DownloadIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    format: Literal["video", "audio"]


@router.post("/downloads")
def create_download(body: DownloadIn, user: dict = Depends(current_user), tg: TelegramClient = Depends(get_tg)):
    user_id = user["id"]
    url = extract_url(body.url)
    platform = detect_platform(url) if url else None
    if platform is None:
        raise _fail(400, "E-UNSUPPORTED")

    _remember(user)
    s = get_settings()
    allowed, retry_after = store.rate_limit_hit(user_id, s.rate_per_minute, s.rate_per_day)
    if not allowed:
        raise _fail(429, "E-RATELIMIT", retry_after=retry_after)

    # The private chat id equals the user id. This fails if the user never started the bot.
    try:
        msg = tg.send_message(user_id, messages.from_miniapp(PLATFORM_LABELS[platform], body.format))
    except TelegramError:
        raise _fail(409, "E-NOCHAT")

    try:
        db_id = jobs.submit(tg, token=secrets.token_urlsafe(8), user_id=user_id, chat_id=user_id,
                            message_id=msg["message_id"], url=url, platform=platform,
                            fmt=body.format, source="miniapp")
    except TelegramError:
        log.exception("mini app submit failed", extra={"user_id": user_id, "platform": platform})
        raise _fail(502, "E-FAILED")

    if db_id is None:
        return {"id": None, "status": "done", "cached": True}
    return {"id": db_id, "status": "queued", "cached": False}


@router.get("/downloads")
def list_downloads(user: dict = Depends(current_user)):
    return {"items": [_job_view(r) for r in db.recent_requests(user["id"], limit=20)]}


@router.get("/downloads/{job_id}")
def get_download(job_id: str, user: dict = Depends(current_user)):
    return _job_view(_owned_request(job_id, user["id"]))


@router.post("/downloads/{job_id}/cancel")
def cancel_download(job_id: str, user: dict = Depends(current_user)):
    view = _job_view(_owned_request(job_id, user["id"]))
    token = (store.get_progress(job_id) or {}).get("token")
    if view["status"] in FINAL_STAGES or not token:
        raise _fail(409, "E-FINISHED")
    store.mark_cancel(token)
    return {"id": job_id, "status": "cancelling"}
