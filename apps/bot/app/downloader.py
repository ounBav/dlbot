"""yt-dlp wrapper. No shell is ever used; URLs are validated upstream by url_utils."""

import glob
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

import yt_dlp
from yt_dlp.utils import DownloadCancelled, YoutubeDLError

log = logging.getLogger(__name__)

# Qualities the Mini App offers, by the short side of the frame (720 = 1280x720 or 720x1280).
QUALITY_LADDER = (360, 480, 720, 1080)
PROBE_TIMEOUT = 30

# (stage, fraction 0-1 or None). Stages: "downloading", "processing".
ProgressFn = Callable[[str, float | None], None]


@dataclass(frozen=True)
class DownloadResult:
    path: str
    title: str
    duration: int
    size: int


@dataclass(frozen=True)
class MediaInfo:
    title: str
    uploader: str
    duration: int
    thumbnail: str | None
    has_video: bool
    qualities: list[int] = field(default_factory=list)


class DownloadError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def classify_error(message: str) -> str:
    """Map a yt-dlp error message to one of our user-facing error codes."""
    m = message.lower()
    if "larger than max" in m or "max-filesize" in m:
        return "E-TOOBIG"
    if "unsupported url" in m:
        return "E-UNSUPPORTED"
    if any(k in m for k in ("private video", "members only", "sign in", "login",
                            "age-restricted", "confirm your age")):
        return "E-PRIVATE"
    return "E-FAILED"


def classify_probe_error(message: str) -> str:
    """Finer codes for the Mini App preview. Falls back to classify_error."""
    m = message.lower()
    if "not a bot" in m:            # YouTube blocking the server's IP, not a private video
        return "E-BLOCKED"
    if "in your country" in m or "geo restrict" in m or "geo-restrict" in m:
        return "E-GEO"
    if any(k in m for k in ("video unavailable", "has been removed", "no longer available",
                            "does not exist", "not found", "http error 404")):
        return "E-UNAVAILABLE"
    return classify_error(message)


def _base_options() -> dict:
    return {
        "noplaylist": True,
        "playlist_items": "1:2",   # enough to tell "one video" from "a playlist" without listing it all
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "cachedir": False,
    }


def _options(fmt: str, out_dir: str, max_bytes: int, quality: int | None = None) -> dict:
    options = {
        **_base_options(),
        "outtmpl": os.path.join(out_dir, "media.%(ext)s"),
        "max_filesize": max_bytes,
        "restrictfilenames": True,
    }
    if fmt == "audio":
        options["format"] = "bestaudio/best"
        options["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}
        ]
    elif quality:
        # Mini App quality pick. "res" is the short side, so vertical TikToks work too: the best
        # stream at or below `quality` wins, h264/mp4 preferred (plays on iOS); remux any leftover
        # container to mp4.
        options["format"] = "bv*+ba/b"
        options["format_sort"] = [f"res:{quality}", "vcodec:h264", "ext:mp4:m4a"]
        options["merge_output_format"] = "mp4"
        options["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
    else:
        # Prefer mp4 so Telegram can play it inline.
        # TODO(FR-04, spike): TikTok no-watermark. Verify which format_id yt-dlp returns
        # for TikTok in the spike, then add a format filter that prefers non-watermarked streams.
        # YouTube mostly serves video and audio as separate streams, so a single-file "best"
        # often matches nothing. Merge the best <=720p mp4 video with m4a audio (ffmpeg), and fall
        # back to any single file. 720p keeps most clips under Telegram's 50 MB bot upload limit.
        options["format"] = (
            "bv*[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4][height<=720]"
            "/bv*[height<=720]+ba/b[height<=720]/b"
        )
        options["merge_output_format"] = "mp4"
    return options


def _single_entry(info: dict) -> dict:
    """Unwrap a one-item playlist; refuse a real playlist."""
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if len(entries) != 1:
            raise DownloadError("E-PLAYLIST", f"{len(entries)} entries")
        return entries[0]
    return info


def _check_playable(info: dict, max_duration: int) -> None:
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise DownloadError("E-LIVE", str(info.get("live_status")))
    duration = int(info.get("duration") or 0)
    if max_duration and duration > max_duration:
        raise DownloadError("E-TOOLONG", f"{duration}s")


def _qualities(info: dict) -> list[int]:
    sides = [
        min(x for x in (f.get("height"), f.get("width")) if x)
        for f in info.get("formats") or []
        if f.get("vcodec") != "none" and (f.get("height") or f.get("width"))
    ]
    if not sides:
        return []
    best = max(sides)
    return [q for q in QUALITY_LADDER if q <= best] or [QUALITY_LADDER[0]]


def _has_video(info: dict) -> bool:
    formats = info.get("formats") or []
    if not formats:
        return info.get("vcodec") not in (None, "none") or bool(info.get("height"))
    return any(f.get("vcodec") not in (None, "none") for f in formats)


def _probe(url: str, max_duration: int) -> MediaInfo:
    try:
        with yt_dlp.YoutubeDL({**_base_options(), "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except YoutubeDLError as exc:
        raise DownloadError(classify_probe_error(str(exc)), str(exc)) from exc
    if not info:
        raise DownloadError("E-FAILED", "no metadata returned")
    info = _single_entry(info)
    _check_playable(info, max_duration)
    thumb = info.get("thumbnail")
    return MediaInfo(
        title=info.get("title") or "video",
        uploader=info.get("uploader") or info.get("channel") or info.get("uploader_id") or "",
        duration=int(info.get("duration") or 0),
        thumbnail=thumb if isinstance(thumb, str) and thumb.startswith("https://") else None,
        has_video=_has_video(info),
        qualities=_qualities(info),
    )


# A probe that hangs is abandoned after PROBE_TIMEOUT; its thread finishes on socket_timeout.
_probe_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="probe")


def probe(url: str, max_duration: int = 0, timeout: float = PROBE_TIMEOUT) -> MediaInfo:
    """Read metadata only (no download) for the Mini App preview."""
    future = _probe_pool.submit(_probe, url, max_duration)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        raise DownloadError("E-FAILED", "probe timed out")
    except DownloadError:
        raise
    except Exception as exc:
        log.exception("unexpected probe error")
        raise DownloadError("E-FAILED", str(exc)) from exc


class _Progress:
    """Turns yt-dlp hook calls into one monotonic 0-1 fraction across video+audio parts."""

    def __init__(self, on_progress: ProgressFn | None, is_cancelled: Callable[[], bool] | None):
        self.on_progress = on_progress
        self.is_cancelled = is_cancelled
        self.files: list[str] = []
        self.best = 0.0
        self.last_sent = -1.0
        self.last_time = 0.0

    def _emit(self, stage: str, fraction: float | None) -> None:
        if self.on_progress is None:
            return
        if fraction is not None:
            self.best = fraction = max(self.best, min(fraction, 1.0))
            now = time.monotonic()
            if fraction < 1.0 and fraction - self.last_sent < 0.01 and now - self.last_time < 1:
                return
            self.last_sent, self.last_time = fraction, now
        self.on_progress(stage, fraction)

    def download_hook(self, d: dict) -> None:
        if self.is_cancelled and self.is_cancelled():
            raise DownloadCancelled("cancelled by user")
        name = d.get("filename") or ""
        if name and name not in self.files:
            self.files.append(name)
        info = d.get("info_dict") or {}
        parts = max(len(info.get("requested_formats") or []), len(self.files), 1)
        part = max(len(self.files) - 1, 0)
        if d.get("status") == "finished":
            frac = 1.0
        else:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                frac = (d.get("downloaded_bytes") or 0) / total
            elif d.get("fragment_count"):
                frac = (d.get("fragment_index") or 0) / d["fragment_count"]
            else:
                frac = 0.0
        self._emit("downloading", (part + min(frac, 1.0)) / parts)

    def postprocessor_hook(self, d: dict) -> None:
        if self.is_cancelled and self.is_cancelled():
            raise DownloadCancelled("cancelled by user")
        if d.get("status") == "started":
            self._emit("processing", None)


def _find_output(out_dir: str) -> str:
    candidates = [
        p for p in glob.glob(os.path.join(out_dir, "media.*"))
        if not p.endswith((".part", ".ytdl"))
    ]
    if not candidates:
        # yt-dlp skips a file over `max_filesize` silently (no exception, no output file).
        raise DownloadError("E-TOOBIG", "no output file produced (skipped by max_filesize)")
    return max(candidates, key=os.path.getsize)


def download(url: str, fmt: str, out_dir: str, max_bytes: int, *, quality: int | None = None,
             max_duration: int = 0, on_progress: ProgressFn | None = None,
             is_cancelled: Callable[[], bool] | None = None) -> DownloadResult:
    """Download one video. Raises DownloadError; code E-CANCELLED when is_cancelled() turned true."""
    tracker = _Progress(on_progress, is_cancelled)
    options = _options(fmt, out_dir, max_bytes, quality)
    options["progress_hooks"] = [tracker.download_hook]
    options["postprocessor_hooks"] = [tracker.postprocessor_hook]
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise DownloadError("E-FAILED", "no metadata returned")
            info = _single_entry(info)
            _check_playable(info, max_duration)   # never start a live stream or an over-long video
            info = ydl.process_ie_result(info, download=True) or info
    except DownloadError:
        raise
    except DownloadCancelled as exc:
        raise DownloadError("E-CANCELLED", str(exc)) from exc
    except YoutubeDLError as exc:
        if is_cancelled and is_cancelled():
            raise DownloadError("E-CANCELLED", str(exc)) from exc
        raise DownloadError(classify_error(str(exc)), str(exc)) from exc
    except Exception as exc:
        log.exception("unexpected downloader error")
        raise DownloadError("E-FAILED", str(exc)) from exc

    path = _find_output(out_dir)
    size = os.path.getsize(path)
    if size > max_bytes:
        raise DownloadError("E-TOOBIG", f"{size} bytes")

    return DownloadResult(
        path=path,
        title=info.get("title") or "video",
        duration=int(info.get("duration") or 0),
        size=size,
    )
