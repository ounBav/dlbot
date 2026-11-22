"""TikTok profile listing for the Mini App "TikTok profile" tab (owner decision, 2026-11-22).

Lists a public profile's latest videos with yt-dlp's `tiktok:user` extractor in flat mode: metadata
only, nothing is downloaded. The result is held once per user in the store (store.put_scan) until
the user sends videos to the chat, discards it, or it expires; it is never written to disk.

SSRF (NFR-06): the caller's input is reduced to a bare username and the profile URL is built here,
so only https://www.tiktok.com/@<username> ever reaches yt-dlp. Video URLs are rebuilt from the
numeric id, never taken from the extractor's response.

Needs curl_cffi (requirements.txt): TikTok refuses the profile page without browser impersonation.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import YoutubeDLError

from .downloader import DownloadError, classify_probe_error
from .url_utils import PLATFORM_HOSTS

log = logging.getLogger(__name__)

# TikTok handles: letters, digits, "_" and "."; we accept up to 32 to be lenient with old accounts.
_USERNAME = re.compile(r"^[A-Za-z0-9_.]{1,32}$")
_VIDEO_ID = re.compile(r"^\d{5,25}$")
_THUMB_PREFERENCE = ("cover", "originCover", "thumbnail")

PROFILE_LIMITS = (10, 50, 100)
SCAN_TIMEOUT = 90
RETRY_DELAY = 2.0


@dataclass(frozen=True)
class ProfileVideo:
    id: str
    url: str
    title: str
    duration: int
    thumbnail: str | None
    timestamp: int | None
    view_count: int | None


@dataclass(frozen=True)
class ProfileScan:
    username: str
    nickname: str
    videos: list[ProfileVideo] = field(default_factory=list)


def normalize_username(raw: str) -> str | None:
    """'@abc', 'abc', or 'https://www.tiktok.com/@abc?lang=en' -> 'abc'. None if not a username."""
    text = (raw or "").strip()
    if "/" in text:
        try:
            parts = urlparse(text if "://" in text else f"https://{text}")
        except ValueError:
            return None
        if (parts.hostname or "").lower() not in PLATFORM_HOSTS["tiktok"]:
            return None
        segment = parts.path.strip("/").split("/")[0]
        if not segment.startswith("@"):
            return None
        text = segment
    text = text.lstrip("@")
    return text if _USERNAME.fullmatch(text) else None


def profile_url(username: str) -> str:
    return f"https://www.tiktok.com/@{username}"


def classify_profile_error(message: str) -> str:
    m = message.lower()
    if "account is private" in m or "log into an account" in m or "private" in m:
        return "E-PRIVATE"
    if "does not have any videos" in m:
        return "E-NOVIDEOS"
    # yt-dlp says this both for unknown users and when TikTok refuses the request.
    if "secondary user id" in m or "unable to download user webpage" in m:
        return "E-PROFILE"
    code = classify_probe_error(message)
    return "E-PROFILE" if code == "E-UNSUPPORTED" else code


def _thumbnail(entry: dict) -> str | None:
    thumbs = {t.get("id"): t.get("url") for t in entry.get("thumbnails") or [] if isinstance(t, dict)}
    for key in _THUMB_PREFERENCE:
        url = thumbs.get(key)
        if isinstance(url, str) and url.startswith("https://"):
            return url
    url = entry.get("thumbnail")
    return url if isinstance(url, str) and url.startswith("https://") else None


def _int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _video(entry: dict, username: str) -> ProfileVideo | None:
    video_id = str(entry.get("id") or "")
    if not _VIDEO_ID.fullmatch(video_id):
        return None
    return ProfileVideo(
        id=video_id,
        url=f"https://www.tiktok.com/@{username}/video/{video_id}",
        title=(entry.get("title") or entry.get("description") or "").strip()[:200],
        duration=_int(entry.get("duration")) or 0,
        thumbnail=_thumbnail(entry),
        timestamp=_int(entry.get("timestamp")),
        view_count=_int(entry.get("view_count")),
    )


def _options(limit: int) -> dict:
    return {
        "extract_flat": "in_playlist",
        "playlistend": limit,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "extractor_retries": 2,
        "cachedir": False,
    }


def _scan_once(username: str, limit: int) -> ProfileScan:
    try:
        with yt_dlp.YoutubeDL(_options(limit)) as ydl:
            info = ydl.extract_info(profile_url(username), download=False)
            entries = list((info or {}).get("entries") or [])[:limit]
    except YoutubeDLError as exc:
        raise DownloadError(classify_profile_error(str(exc)), str(exc)) from exc
    if not info:
        raise DownloadError("E-FAILED", "no metadata returned")
    seen: set[str] = set()
    videos = []
    for entry in entries:
        video = _video(entry, username) if isinstance(entry, dict) else None
        if video and video.id not in seen:
            seen.add(video.id)
            videos.append(video)
    if not videos:
        raise DownloadError("E-NOVIDEOS", "no public videos listed")
    first = entries[0] if entries and isinstance(entries[0], dict) else {}
    nickname = first.get("channel") or info.get("uploader") or ""
    return ProfileScan(username=username, nickname=str(nickname)[:100], videos=videos)


def _scan(username: str, limit: int, attempts: int) -> ProfileScan:
    """TikTok refuses some profile requests at random; one more try usually gets through."""
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return _scan_once(username, limit)
        except DownloadError as exc:
            if exc.code not in ("E-PROFILE", "E-FAILED") or attempt >= attempts:
                raise
            log.info("profile scan retry", extra={"platform": "tiktok", "code": exc.code})
            time.sleep(RETRY_DELAY)
    raise DownloadError("E-FAILED", "unreachable")


# Separate from the preview pool so a slow profile listing never delays link previews.
_scan_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="profile")


def scan(username: str, limit: int, *, attempts: int = 2, timeout: float = SCAN_TIMEOUT) -> ProfileScan:
    """List up to `limit` of the latest public videos of @username. Raises DownloadError."""
    future = _scan_pool.submit(_scan, username, limit, attempts)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        raise DownloadError("E-FAILED", "profile scan timed out")
    except DownloadError:
        raise
    except Exception as exc:
        log.exception("unexpected profile scan error")
        raise DownloadError("E-FAILED", str(exc)) from exc
