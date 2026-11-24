"""In-process download workers: dequeue jobs, download, upload to Telegram, clean up.

Runs as daemon threads inside the web process (started by app/main.py), because the job queue is
an in-memory singleton (app/store.py) that only this process can see.
Each job downloads into its own folder under TEMP_DIR; the folder is removed in `finally`.
Exception: Mini App "save to device" jobs hand the folder to a short-lived download link
(store.put_file); the janitor thread deletes it when the link expires (FILE_TTL_MIN).
"""

import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time

from . import batches, messages, store
from .config import get_settings
from .downloader import DownloadError, download
from .jobs import cache_key
from .tg import TelegramClient, TelegramError
from .url_utils import PLATFORM_LABELS, url_hash

log = logging.getLogger("worker")


def process(job: dict, tg: TelegramClient) -> None:
    s = get_settings()
    token, fmt, db_id = job["token"], job["fmt"], job["db_id"]
    chat_id = job["chat_id"]
    quality, to_device = job.get("quality"), job.get("delivery") == "device"
    extra = {"req_id": db_id, "user_id": job["user_id"], "platform": job["platform"]}
    started = time.monotonic()

    os.makedirs(s.temp_dir, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="job-", dir=s.temp_dir)
    keep_workdir = False
    try:
        if store.is_cancelled(token):
            _finish_cancelled(job, tg)
            return

        _progress(db_id, stage="downloading", progress=0)
        _edit(tg, job, messages.DOWNLOADING, safe=False)
        result = download(
            job["url"], fmt, workdir, s.max_upload_mb * 1024 * 1024,
            quality=quality, max_duration=s.max_duration_sec,
            on_progress=lambda stage, frac: _progress(
                db_id, stage=stage, **({} if frac is None else {"progress": f"{frac:.3f}"})),
            is_cancelled=lambda: store.is_cancelled(token),
        )

        if store.is_cancelled(token):  # cancel requested during download
            _finish_cancelled(job, tg)
            return

        if to_device:
            keep_workdir = _publish_file(job, result, workdir)
        else:
            _progress(db_id, stage="uploading", title=result.title)
            _edit(tg, job, messages.UPLOADING, safe=False)
            caption = messages.caption(result.title, PLATFORM_LABELS[job["platform"]], result.duration)
            file_id = tg.send_file(chat_id, fmt, result.path, caption)
            store.cache_set(url_hash(job["url"]), cache_key(fmt, quality), file_id)

        store.finish_request(db_id, "done", file_size=result.size, title=result.title,
                          duration_ms=int((time.monotonic() - started) * 1000))
        _progress(db_id, stage="done")
        _edit(tg, job, messages.DONE, safe=False)
        log.info("job done", extra=extra)

    except DownloadError as exc:
        if exc.code == "E-CANCELLED":
            _finish_cancelled(job, tg)
            return
        if exc.code == "E-FAILED" and job.get("attempt", 1) < s.max_job_attempts:
            job["attempt"] = job.get("attempt", 1) + 1
            log.warning("retrying job", extra={**extra, "code": exc.code})
            _progress(db_id, stage="retrying")
            _edit(tg, job, messages.RETRYING)
            time.sleep(2 * job["attempt"])
            store.enqueue(job)
            return
        _fail(job, exc.code, tg, extra, detail=str(exc))

    except TelegramError as exc:
        # Telegram rejects oversized uploads with HTTP 413
        code = "E-TOOBIG" if "413" in str(exc) or "too" in str(exc).lower() else "E-FAILED"
        _fail(job, code, tg, extra, detail=str(exc))

    except Exception as exc:
        log.exception("unexpected worker error", extra=extra)
        _fail(job, "E-FAILED", tg, extra, detail=str(exc))

    finally:
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)  # media is never kept (NFR-07)


_UNSAFE_NAME = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')


def display_name(title: str, path: str) -> str:
    """File name the user sees when saving: the title, made safe for every OS, plus the real ext."""
    ext = os.path.splitext(path)[1].lower() or ".bin"
    stem = re.sub(r"\s+", " ", _UNSAFE_NAME.sub(" ", title or "")).strip(" .")
    return (stem[:80].rstrip(" .") or "video") + ext


def _publish_file(job: dict, result, workdir: str) -> bool:
    """Keep the file behind an unguessable link for FILE_TTL_MIN. Returns True (keep the folder)."""
    s = get_settings()
    file_token = secrets.token_urlsafe(32)
    name = display_name(result.title, result.path)
    expires = store.put_file(file_token, {
        "path": result.path, "dir": workdir, "name": name, "size": result.size,
        "user_id": job["user_id"], "db_id": job["db_id"],
    }, s.file_ttl_min * 60)
    _progress(job["db_id"], title=result.title, file_token=file_token, file_name=name,
              file_size=result.size, expires_at=int(expires))
    return True


def _progress(db_id: str, **fields) -> None:
    """Best effort: the Mini App's live view must never break a job."""
    try:
        store.set_progress(db_id, **fields)
    except Exception:
        log.warning("could not record job progress", extra={"req_id": db_id})


def _edit(tg: TelegramClient, job: dict, text: str, safe: bool = True) -> None:
    """Edit the chat status message. Device-delivery jobs have none. safe=False lets errors raise."""
    if job.get("message_id") is None:
        return
    try:
        tg.edit_message(job["chat_id"], job["message_id"], text)
    except TelegramError:
        if not safe:
            raise
        log.warning("could not edit status message")


def _finish_cancelled(job: dict, tg: TelegramClient) -> None:
    store.finish_request(job["db_id"], "cancelled")
    _progress(job["db_id"], stage="cancelled")
    _edit(tg, job, messages.CANCELLED)


def _fail(job: dict, code: str, tg: TelegramClient, extra: dict, detail: str = "") -> None:
    s = get_settings()
    log.error("job failed", extra={**extra, "code": code})
    if detail:
        log.error("failure detail: %s", detail[:500], extra=extra)
    store.finish_request(job["db_id"], "failed", error_code=code)
    _progress(job["db_id"], stage="failed", error_code=code)
    text = messages.error(code, ref=job["db_id"][:8], limit_mb=s.max_upload_mb)
    if job.get("message_id") is None and job.get("delivery", "chat") == "chat" and job.get("chat_id"):
        # Profile-batch video: no status message to edit, so tell the chat which one failed.
        try:
            tg.send_message(job["chat_id"], messages.batch_item_failed(
                code, job.get("title") or "", ref=job["db_id"][:8], limit_mb=s.max_upload_mb))
        except TelegramError:
            log.warning("could not report batch failure", extra=extra)
        return
    _edit(tg, job, text)


def _safe_process(job: dict, tg: TelegramClient) -> None:
    try:
        process(job, tg)
    except Exception:
        log.exception("job crashed outside handler")


def clean_temp_dir() -> None:
    """Remove job folders left behind by a crash or a hard restart."""
    root = get_settings().temp_dir
    os.makedirs(root, exist_ok=True)
    for name in os.listdir(root):
        if name.startswith("job-"):
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)


def sweep_files() -> int:
    """Delete Mini App files whose download link expired. Returns how many were removed."""
    expired = store.pop_expired_files()
    for meta in expired:
        shutil.rmtree(meta["dir"], ignore_errors=True)
    return len(expired)


class WorkerPool:
    """N threads pulling jobs from the in-memory queue."""

    def __init__(self, tg: TelegramClient, concurrency: int):
        self.tg = tg
        self.concurrency = max(1, concurrency)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        clean_temp_dir()
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        for target, name in ((self._janitor, "janitor"), (self._dispatcher, "batch-dispatcher")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        log.info("workers started", extra={"code": f"concurrency={self.concurrency}"})

    def _loop(self) -> None:
        while not self._stop.is_set():
            store.heartbeat()
            job = store.dequeue(timeout=1)
            if job:
                _safe_process(job, self.tg)

    def _janitor(self) -> None:
        while not self._stop.wait(30):
            try:
                if sweep_files():
                    log.info("expired files removed")
            except Exception:
                log.exception("file sweep failed")

    def _dispatcher(self) -> None:
        """Feed TikTok profile batches to the queue, PROFILE_BATCH_SIZE videos per wave."""
        while not self._stop.wait(1):
            batches.tick(self.tg)   # never raises: errors are logged per batch

    def alive(self) -> bool:
        return bool(self._threads) and all(t.is_alive() for t in self._threads)

    def stop(self, timeout: float = 30) -> None:
        """Stop taking new jobs and wait (bounded) for in-flight ones."""
        log.info("workers stopping")
        self._stop.set()
        deadline = time.monotonic() + timeout
        for t in self._threads:
            t.join(max(0.0, deadline - time.monotonic()))
