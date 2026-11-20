"""Background worker: dequeues download jobs, downloads, uploads to Telegram, cleans up."""

import logging
import os
import shutil
import signal
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import db, messages, store
from .config import get_settings
from .downloader import DownloadError, download
from .log import setup_logging
from .tg import TelegramClient, TelegramError
from .url_utils import PLATFORM_LABELS, url_hash

log = logging.getLogger("worker")
_running = True


def _stop(*_):
    global _running
    _running = False


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

        tg.edit_message(chat_id, message_id, messages.DOWNLOADING)
        result = download(job["url"], fmt, workdir, s.max_upload_mb * 1024 * 1024)

        if store.is_cancelled(token):  # cancel requested during download
            _finish_cancelled(job, tg)
            return

        tg.edit_message(chat_id, message_id, messages.UPLOADING)
        caption = messages.caption(result.title, PLATFORM_LABELS[job["platform"]], result.duration)
        file_id = tg.send_file(chat_id, fmt, result.path, caption)

        h = url_hash(job["url"])
        store.cache_set(h, fmt, file_id)
        db.save_media_cache(h, fmt, file_id, result.title, result.size)
        db.finish_request(db_id, "done", file_size=result.size,
                          duration_ms=int((time.monotonic() - started) * 1000))
        tg.edit_message(chat_id, message_id, messages.DONE)
        log.info("job done", extra=extra)

    except DownloadError as exc:
        if exc.code == "E-FAILED" and job.get("attempt", 1) < s.max_job_attempts:
            job["attempt"] = job.get("attempt", 1) + 1
            log.warning("retrying job", extra={**extra, "code": exc.code})
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


def _safe_edit(tg: TelegramClient, chat_id: int, message_id: int, text: str) -> None:
    try:
        tg.edit_message(chat_id, message_id, text)
    except TelegramError:
        log.warning("could not edit status message")


def _finish_cancelled(job: dict, tg: TelegramClient) -> None:
    db.finish_request(job["db_id"], "cancelled")
    _safe_edit(tg, job["chat_id"], job["message_id"], messages.CANCELLED)


def _fail(job: dict, code: str, tg: TelegramClient, extra: dict, detail: str = "") -> None:
    s = get_settings()
    log.error("job failed", extra={**extra, "code": code})
    if detail:
        log.error("failure detail: %s", detail[:500], extra=extra)
    db.finish_request(job["db_id"], "failed", error_code=code)
    text = messages.error(code, ref=job["db_id"][:8], limit_mb=s.max_upload_mb)
    _safe_edit(tg, job["chat_id"], job["message_id"], text)


def _safe_process(job: dict, tg: TelegramClient) -> None:
    try:
        process(job, tg)
    except Exception:
        log.exception("job crashed outside handler")


def main() -> None:
    setup_logging()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    s = get_settings()
    tg = TelegramClient(s.bot_token)
    pool = ThreadPoolExecutor(max_workers=s.worker_concurrency)
    inflight: set = set()
    log.info("worker started", extra={"code": f"concurrency={s.worker_concurrency}"})

    while _running:
        store.heartbeat()
        inflight = {f for f in inflight if not f.done()}
        if len(inflight) >= s.worker_concurrency:
            wait(inflight, timeout=1, return_when=FIRST_COMPLETED)
            continue
        job = store.dequeue(timeout=5)
        if job:
            inflight.add(pool.submit(_safe_process, job, tg))

    log.info("worker stopping, waiting for in-flight jobs")
    pool.shutdown(wait=True)


if __name__ == "__main__":
    main()
