"""Start a download: answer from the file_id cache, or enqueue a worker job.

Shared by the chat flow (inline button callback) and the Mini App API so both behave the same.
Chat delivery: the status message (chat_id, message_id) must already exist; the worker edits it.
Device delivery (Mini App "Save to device"): no chat message; the worker keeps the file behind a
short-lived link instead of uploading it to Telegram.
"""

from . import messages, store
from .tg import TelegramClient
from .url_utils import url_hash


def cache_key(fmt: str, quality: int | None) -> str:
    """file_id cache key. The chat flow (no quality) keeps the original keys."""
    return fmt if quality is None or fmt == "audio" else f"{fmt}:{quality}"


def submit(tg: TelegramClient, *, token: str, user_id: int, chat_id: int | None, message_id: int | None,
           url: str, platform: str, fmt: str, source: str = "chat", quality: int | None = None,
           delivery: str = "chat") -> str | None:
    """Return the request id of the queued job, or None if it was served from cache."""
    if fmt == "audio":
        quality = None
    if delivery == "chat":
        cached = store.cache_get(url_hash(url), cache_key(fmt, quality))
        if cached:
            tg.send_cached(chat_id, fmt, cached, messages.CACHED_CAPTION)
            tg.edit_message(chat_id, message_id, messages.DONE)
            return None

    db_id = store.create_request(user_id, url, platform, fmt, source=source)
    store.set_progress(db_id, token=token, user_id=user_id, stage="queued", delivery=delivery,
                       quality=quality)
    job = {
        "token": token,
        "db_id": db_id,
        "fmt": fmt,
        "url": url,
        "platform": platform,
        "user_id": user_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "attempt": 1,
    }
    if quality is not None:
        job["quality"] = quality
    if delivery != "chat":
        job["delivery"] = delivery
    store.enqueue(job)
    store.set_active(user_id, token)
    if delivery == "chat":
        tg.edit_message(chat_id, message_id, messages.QUEUED)
    return db_id
