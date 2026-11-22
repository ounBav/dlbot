"""In-process download workers: dequeue jobs, download, upload to Telegram, clean up.

Runs as daemon threads inside the web process (started by app/main.py), because the job queue is
an in-memory singleton (app/store.py) that only this process can see.
Each job downloads into its own folder under TEMP_DIR; the folder is removed in `finally`.
"""

import logging
import os
import shutil
import tempfile
import threading
import time

from . import messages, store
from .config import get_settings
from .downloader import DownloadError, download
from .tg import TelegramClient, TelegramError
from .url_utils import PLATFORM_LABELS, url_hash

log = logging.getLogger("worker")


def process(job: dict, tg: TelegramClient) -> None:
    s = get_settings()
    token, fmt, db_id = job["token"], job["fmt"], job["db_id"]
    chat_id, message_id = job["chat_id"], job["message_id"]
    extra = {"req_id": db_id, "user_id": job["user_id"], "platform": job["platform"]}
    started = time.monotonic()

    os.makedirs(s.temp_dir, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="job-", dir=s.temp_dir)
    try:
        if store.is_cancelled(token):
            _finish_cancelled(job, tg)
            return

        _progress(db_id, stage="downloading")
        tg.edit_message(chat_id, message_id, messages.DOWNLOADING)
        result = download(job["url"], fmt, workdir, s.max_upload_mb * 1024 * 1024)

        if store.is_cancelled(token):  # cancel requested during download
            _finish_cancelled(job, tg)
            return

        _progress(db_id, stage="uploading", title=result.title)
        tg.edit_message(chat_id, message_id, messages.UPLOADING)
        caption = messages.caption(result.title, PLATFORM_LABELS[job["platform"]], result.duration)
        file_id = tg.send_file(chat_id, fmt, result.path, caption)

        h = url_hash(job["url"])
        store.cache_set(h, fmt, file_id)
        store.finish_request(db_id, "done", file_size=result.size, title=result.title,
                          duration_ms=int((time.monotonic() - started) * 1000))
        _progress(db_id, stage="done")
        tg.edit_message(chat_id, message_id, messages.DONE)
        log.info("job done", extra=extra)

    except DownloadError as exc:
        if exc.code == "E-FAILED" and job.get("attempt", 1) < s.max_job_attempts:
            job["attempt"] = job.get("attempt", 1) + 1
            log.warning("retrying job", extra={**extra, "code": exc.code})
            _progress(db_id, stage="retrying")
            _safe_edit(tg, chat_id, message_id, messages.RETRYING)
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
        shutil.rmtree(workdir, ignore_errors=True)  # media is never kept (NFR-07)


def _progress(db_id: str, **fields) -> None:
    """Best effort: the Mini App's live view must never break a job."""
    try:
        store.set_progress(db_id, **fields)
    except Exception:
        log.warning("could not record job progress", extra={"req_id": db_id})


def _safe_edit(tg: TelegramClient, chat_id: int, message_id: int, text: str) -> None:
    try:
        tg.edit_message(chat_id, message_id, text)
    except TelegramError:
        log.warning("could not edit status message")


def _finish_cancelled(job: dict, tg: TelegramClient) -> None:
    store.finish_request(job["db_id"], "cancelled")
    _progress(job["db_id"], stage="cancelled")
    _safe_edit(tg, job["chat_id"], job["message_id"], messages.CANCELLED)


def _fail(job: dict, code: str, tg: TelegramClient, extra: dict, detail: str = "") -> None:
    s = get_settings()
    log.error("job failed", extra={**extra, "code": code})
    if detail:
        log.error("failure detail: %s", detail[:500], extra=extra)
    store.finish_request(job["db_id"], "failed", error_code=code)
    _progress(job["db_id"], stage="failed", error_code=code)
    text = messages.error(code, ref=job["db_id"][:8], limit_mb=s.max_upload_mb)
    _safe_edit(tg, job["chat_id"], job["message_id"], text)


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
        log.info("workers started", extra={"code": f"concurrency={self.concurrency}"})

    def _loop(self) -> None:
        while not self._stop.is_set():
            store.heartbeat()
            job = store.dequeue(timeout=1)
            if job:
                _safe_process(job, self.tg)

    def alive(self) -> bool:
        return bool(self._threads) and all(t.is_alive() for t in self._threads)

    def stop(self, timeout: float = 30) -> None:
        """Stop taking new jobs and wait (bounded) for in-flight ones."""
        log.info("workers stopping")
        self._stop.set()
        deadline = time.monotonic() + timeout
        for t in self._threads:
            t.join(max(0.0, deadline - time.monotonic()))
