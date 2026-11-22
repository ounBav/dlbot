"""yt-dlp wrapper. No shell is ever used; URLs are validated upstream by url_utils."""

import glob
import logging
import os
from dataclasses import dataclass

import yt_dlp
from yt_dlp.utils import YoutubeDLError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadResult:
    path: str
    title: str
    duration: int
    size: int


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


def _options(fmt: str, out_dir: str, max_bytes: int) -> dict:
    options = {
        "outtmpl": os.path.join(out_dir, "media.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "max_filesize": max_bytes,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "restrictfilenames": True,
    }
    if fmt == "audio":
        options["format"] = "bestaudio/best"
        options["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}
        ]
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


def _find_output(out_dir: str) -> str:
    candidates = [
        p for p in glob.glob(os.path.join(out_dir, "media.*"))
        if not p.endswith((".part", ".ytdl"))
    ]
    if not candidates:
        raise DownloadError("E-FAILED", "no output file produced")
    return max(candidates, key=os.path.getsize)


def download(url: str, fmt: str, out_dir: str, max_bytes: int) -> DownloadResult:
    options = _options(fmt, out_dir, max_bytes)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except YoutubeDLError as exc:
        raise DownloadError(classify_error(str(exc)), str(exc)) from exc
    except Exception as exc:
        log.exception("unexpected downloader error")
        raise DownloadError("E-FAILED", str(exc)) from exc

    if not info:
        raise DownloadError("E-FAILED", "no metadata returned")

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
