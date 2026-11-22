"""Start a download: answer from the file_id cache, or enqueue a worker job.

Shared by the chat flow (inline button callback) and the Mini App API so both behave the same.
The status message (chat_id, message_id) must already exist; the worker edits it as it goes.
"""

from . import db, messages, store
from .tg import TelegramClient
from .url_utils import url_hash


def submit(tg: TelegramClient, *, token: str, user_id: int, chat_id: int, message_id: int,
           url: str, platform: str, fmt: str, source: str = "chat") -> str | None:
    """Return the request id of the queued job, or None if it was served from cache."""
    cached = store.cache_get(url_hash(url), fmt)
    if cached:
        tg.send_cached(chat_id, fmt, cached, messages.CACHED_CAPTION)
        tg.edit_message(chat_id, message_id, messages.DONE)
        return None

    db_id = db.create_request(user_id, url, platform, fmt, source=source)
    store.set_progress(db_id, token=token, user_id=user_id, stage="queued")
    store.enqueue({
        "token": token,
        "db_id": db_id,
        "fmt": fmt,
        "url": url,
        "platform": platform,
        "user_id": user_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "attempt": 1,
    })
    store.set_active(user_id, token)
    tg.edit_message(chat_id, message_id, messages.QUEUED)
    return db_id
