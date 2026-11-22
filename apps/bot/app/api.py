"""HTTP API for the Telegram Mini App (apps/miniapp). Mounted under /api by app/main.py.

Every call carries `Authorization: tma <initData>`; the user id comes only from validated initData.
Downloads reuse the chat pipeline. Two deliveries:
  - chat:   the bot posts a status message in the user's private chat, the worker edits it and
            sends the file there (same as the chat flow).
  - device: no chat message; the worker keeps the finished file for FILE_TTL_MIN behind
            GET /api/files/{token} (random 32-byte token, no initData: Telegram's downloadFile
            fetches the URL without custom headers). The janitor deletes it afterwards.
Errors are `{"detail": {"code": "E-...", ...}}`.
"""

import logging
import mimetypes
import os
import re
import secrets
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import jobs, messages, profiles, store
from .config import get_settings
from .downloader import QUALITY_LADDER, DownloadError, probe
from .tg import TelegramClient, TelegramError
from .url_utils import PLATFORM_HOSTS, PLATFORM_LABELS, detect_platform, extract_url
from .webapp_auth import InitDataError, validate

log = logging.getLogger("api")


def _no_store(response: Response) -> None:
    # Telegram webviews cache aggressively; polled job status must always be fresh.
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api", dependencies=[Depends(_no_store)])
# Served without initData (see module docstring), so it lives on its own router.
files_router = APIRouter(prefix="/api")
_FILE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")   # secrets.token_urlsafe(32)

# "expired": the request never reached a final state and its live progress is gone
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
    if store.is_banned(user["id"]):
        raise _fail(403, "E-BANNED")
    return user


def _remember(user: dict) -> None:
    """Create/refresh the users row (requests.telegram_id references it)."""
    store.upsert_user(user["id"], user.get("username"))


def _is_stale(created_at) -> bool:
    if not isinstance(created_at, datetime):
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created_at > STALE_AFTER


def _num(value, cast=float):
    try:
        return cast(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _file_view(progress: dict) -> dict | None:
    """The download link of a finished "save to device" job, while it has not expired."""
    token = progress.get("file_token")
    meta = store.get_file(token) if token else None
    if meta is None:
        return None
    return {"name": meta["name"], "size": meta["size"], "download_path": f"/api/files/{token}",
            "expires_at": datetime.fromtimestamp(meta["expires_at"], timezone.utc)}


def _job_view(row: dict) -> dict:
    """Request log row + live stage while the job is still running."""
    status = row["status"]
    title = row.get("title")
    progress = store.get_progress(row["id"])
    if status not in FINAL_STAGES:
        if progress is None and _is_stale(row.get("created_at")):
            status = "expired"
        else:
            status = (progress or {}).get("stage") or "queued"
            title = title or (progress or {}).get("title") or None
    progress = progress or {}
    delivery = progress.get("delivery") or "chat"
    view = {
        "id": row["id"],
        "url": row["url"],
        "platform": row["platform"],
        "format": row["format"],
        "status": status,
        "error_code": row.get("error_code"),
        "title": title,
        "created_at": row["created_at"],
        "delivery": delivery,
        "quality": _num(progress.get("quality"), int),
        "progress": 1.0 if status == "done" else _num(progress.get("progress")),
        "file": None,
    }
    if status == "done" and delivery == "device":
        view["file"] = _file_view(progress)
    return view


def _owned_request(job_id: str, user_id: int) -> dict:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise _fail(404, "E-NOTFOUND")
    row = store.get_request(job_id, user_id)
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
        "qualities": list(QUALITY_LADDER),
        "file_ttl_min": s.file_ttl_min,
        "profile_max_videos": max(1, s.profile_max_videos),
        "profile_max_send": _max_send(),
    }


def _allowed_url(raw: str) -> tuple[str, str]:
    url = extract_url(raw)
    platform = detect_platform(url) if url else None
    if platform is None:
        raise _fail(400, "E-UNSUPPORTED")
    return url, platform


class InfoIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


@router.post("/info")
def media_info(body: InfoIn, user: dict = Depends(current_user)):
    """Preview a link (title, thumbnail, qualities) without downloading. Runs yt-dlp metadata only."""
    url, platform = _allowed_url(body.url)
    s = get_settings()
    allowed, retry_after = store.hit_window(f"info:{user['id']}", s.info_rate_per_minute, 60)
    if not allowed:
        raise _fail(429, "E-RATELIMIT", retry_after=retry_after)
    try:
        info = probe(url, max_duration=s.max_duration_sec)
    except DownloadError as exc:
        log.info("preview refused", extra={"user_id": user["id"], "platform": platform, "code": exc.code})
        raise _fail(422, exc.code, max_duration=s.max_duration_sec or None)
    return {
        "url": url,
        "platform": platform,
        "title": info.title,
        "uploader": info.uploader,
        "duration": info.duration,
        "thumbnail": info.thumbnail,
        "has_video": info.has_video,
        "qualities": info.qualities,
    }


class DownloadIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    format: Literal["video", "audio"]
    quality: Literal[360, 480, 720, 1080] | None = None
    delivery: Literal["chat", "device"] = "chat"


def _active_job(user_id: int) -> dict | None:
    for row in store.recent_requests(user_id, limit=5):
        view = _job_view(row)
        if view["status"] not in FINAL_STAGES:
            return view
    return None


@router.post("/downloads")
def create_download(body: DownloadIn, user: dict = Depends(current_user), tg: TelegramClient = Depends(get_tg)):
    user_id = user["id"]
    url, platform = _allowed_url(body.url)

    _remember(user)
    active = _active_job(user_id)
    if active is not None:    # one active job per user; checked before the quota is spent
        raise _fail(409, "E-BUSY", id=active["id"])
    s = get_settings()
    allowed, retry_after = store.rate_limit_hit(user_id, s.rate_per_minute, s.rate_per_day)
    if not allowed:
        raise _fail(429, "E-RATELIMIT", retry_after=retry_after)

    quality = None if body.format == "audio" else body.quality
    chat_id = message_id = None
    if body.delivery == "chat":
        # The private chat id equals the user id. This fails if the user never started the bot.
        try:
            msg = tg.send_message(user_id, messages.from_miniapp(PLATFORM_LABELS[platform], body.format))
        except TelegramError:
            raise _fail(409, "E-NOCHAT")
        chat_id, message_id = user_id, msg["message_id"]

    try:
        db_id = jobs.submit(tg, token=secrets.token_urlsafe(8), user_id=user_id, chat_id=chat_id,
                            message_id=message_id, url=url, platform=platform,
                            fmt=body.format, source="miniapp", quality=quality, delivery=body.delivery)
    except TelegramError:
        log.exception("mini app submit failed", extra={"user_id": user_id, "platform": platform})
        raise _fail(502, "E-FAILED")

    if db_id is None:
        return {"id": None, "status": "done", "cached": True}
    return {"id": db_id, "status": "queued", "cached": False}


@router.get("/downloads")
def list_downloads(user: dict = Depends(current_user)):
    return {"items": [_job_view(r) for r in store.recent_requests(user["id"], limit=20)]}


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


# --- TikTok profile tab: list a profile once, pick videos, send them to the bot chat -------------
# The listing is one-time state (store.put_scan, one per user): it is dropped when the videos are
# sent, when the app closes (POST /tiktok/scan/discard), after PROFILE_SCAN_TTL_MIN, or on restart.

MAX_SEND_HARD = 20   # the UI follows a batch through GET /downloads, which returns the last 20 jobs


def _max_send() -> int:
    return max(1, min(get_settings().profile_max_send, MAX_SEND_HARD))


def _video_view(v: dict) -> dict:
    return {k: v[k] for k in ("id", "url", "title", "duration", "thumbnail", "timestamp", "view_count")}


class ScanIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    limit: Literal[10, 50, 100, "all"] = 10


@router.post("/tiktok/scan")
def scan_profile(body: ScanIn, user: dict = Depends(current_user)):
    """List the latest public videos of a TikTok profile (metadata only). Replaces any earlier list."""
    username = profiles.normalize_username(body.username)
    if username is None:
        raise _fail(400, "E-USERNAME")
    s = get_settings()
    allowed, retry_after = store.hit_window(f"scan:{user['id']}", s.profile_scan_rate_per_minute, 60)
    if not allowed:
        raise _fail(429, "E-RATELIMIT", retry_after=retry_after)
    store.drop_scan(user["id"])          # never keep two listings for one user
    cap = max(1, s.profile_max_videos)
    limit = cap if body.limit == "all" else min(body.limit, cap)
    try:
        result = profiles.scan(username, limit, attempts=s.profile_scan_attempts)
    except DownloadError as exc:
        log.info("profile scan refused", extra={"user_id": user["id"], "platform": "tiktok", "code": exc.code})
        raise _fail(422, exc.code)

    scan_id = secrets.token_urlsafe(12)
    videos = [_video_view(asdict(v)) for v in result.videos]
    expires = store.put_scan(user["id"], {"id": scan_id, "username": result.username, "videos": videos},
                             s.profile_scan_ttl_min * 60)
    log.info("profile scanned", extra={"user_id": user["id"], "platform": "tiktok", "code": f"n={len(videos)}"})
    return {
        "scan_id": scan_id,
        "username": result.username,
        "nickname": result.nickname or None,
        "limit": body.limit,
        "count": len(videos),
        # "All" stopped at the server's cap: the profile may have more videos than listed.
        "truncated": body.limit == "all" and len(videos) >= cap,
        "max_send": _max_send(),
        "expires_at": datetime.fromtimestamp(expires, timezone.utc),
        "videos": videos,
    }


class SendIn(BaseModel):
    video_ids: list[str] = Field(min_length=1, max_length=MAX_SEND_HARD)


@router.post("/tiktok/scan/{scan_id}/send")
def send_profile_videos(scan_id: str, body: SendIn, user: dict = Depends(current_user),
                        tg: TelegramClient = Depends(get_tg)):
    """Queue the picked videos for delivery to the bot chat, then forget the listing (one-time)."""
    user_id = user["id"]
    scan = store.get_scan(user_id)
    if scan is None or scan["id"] != scan_id:
        raise _fail(410, "E-SCANGONE")
    by_id = {v["id"]: v for v in scan["videos"]}
    picked = [by_id[i] for i in dict.fromkeys(body.video_ids) if i in by_id]
    if not picked:
        raise _fail(400, "E-NOTFOUND")
    max_send = _max_send()
    if len(picked) > max_send:
        raise _fail(400, "E-TOOMANY", max=max_send)

    _remember(user)
    s = get_settings()
    allowed, remaining = store.reserve_daily(user_id, len(picked), s.rate_per_day)
    if not allowed:
        if remaining <= 0:
            now = int(datetime.now(timezone.utc).timestamp())
            raise _fail(429, "E-RATELIMIT", retry_after=86400 - (now % 86400))
        raise _fail(429, "E-QUOTA", remaining=remaining)

    try:   # one summary message per batch; it also proves the user has started the bot
        tg.send_message(user_id, messages.from_profile(len(picked), scan["username"]))
    except TelegramError:
        store.release_daily(user_id, len(picked))
        raise _fail(409, "E-NOCHAT")

    items = []
    for video in picked:
        item = {"video_id": video["id"], "id": None, "status": "queued"}
        try:
            db_id = jobs.submit(tg, token=secrets.token_urlsafe(8), user_id=user_id, chat_id=user_id,
                                message_id=None, url=video["url"], platform="tiktok", fmt="video",
                                source="profile", delivery="chat", title=video["title"] or None)
            item.update(id=db_id, status="queued" if db_id else "done")
        except TelegramError:
            log.warning("profile batch item failed", extra={"user_id": user_id, "platform": "tiktok"})
            item["status"] = "failed"
        items.append(item)

    store.drop_scan(user_id, scan_id)   # one-time listing: gone once the videos are on their way
    log.info("profile batch queued", extra={"user_id": user_id, "platform": "tiktok",
                                            "code": f"n={len(items)}"})
    return {"username": scan["username"], "items": items,
            "queued": sum(i["status"] == "queued" for i in items)}


@router.post("/tiktok/scan/discard")
def discard_scan(user: dict = Depends(current_user)):
    """The app is closing or starting over: forget the listing now instead of waiting for the TTL."""
    return {"discarded": store.drop_scan(user["id"])}


@files_router.api_route("/files/{token}", methods=["GET", "HEAD"])
def get_file(token: str):
    """Stream a finished "save to device" file. 410 once the link expired (FILE_TTL_MIN)."""
    meta = store.get_file(token) if _FILE_TOKEN.match(token) else None
    if meta is None or not os.path.isfile(meta["path"]):
        raise _fail(410, "E-GONE")
    media_type = mimetypes.guess_type(meta["name"])[0] or "application/octet-stream"
    # filename* (RFC 5987) is added by Starlette for non-ASCII titles.
    return FileResponse(meta["path"], media_type=media_type, filename=meta["name"],
                        content_disposition_type="attachment",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
