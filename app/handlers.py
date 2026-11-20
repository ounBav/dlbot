"""Telegram update routing: messages, commands, inline-button callbacks."""

import logging
import secrets

from . import db, messages, store
from .config import get_settings
from .tg import TelegramClient
from .url_utils import PLATFORM_LABELS, detect_platform, extract_url, url_hash

log = logging.getLogger(__name__)
FORMATS = {"v": "video", "a": "audio"}


def handle_update(update: dict, tg: TelegramClient) -> None:
    if "message" in update:
        _handle_message(update["message"], tg)
    elif "callback_query" in update:
        _handle_callback(update["callback_query"], tg)


# --- messages ---

def _handle_message(msg: dict, tg: TelegramClient) -> None:
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    sender = msg.get("from") or {}
    user_id = sender.get("id")
    text = (msg.get("text") or "").strip()

    # v1 supports private chats only.
    if chat.get("type") != "private" or not user_id or not text or chat_id is None:
        return

    db.upsert_user(user_id, sender.get("username"))
    if db.is_banned(user_id):
        tg.send_message(chat_id, messages.BANNED)
        return

    if text.startswith("/"):
        _handle_command(text, chat_id, user_id, tg)
        return

    url = extract_url(text)
    if url is None:
        tg.send_message(chat_id, messages.UNKNOWN_TEXT)
        return

    platform = detect_platform(url)
    if platform is None:
        tg.send_message(chat_id, messages.UNSUPPORTED)
        return

    s = get_settings()
    allowed, retry_after = store.rate_limit_hit(user_id, s.rate_per_minute, s.rate_per_day)
    if not allowed:
        tg.send_message(chat_id, messages.rate_limited(retry_after))
        return

    token = secrets.token_urlsafe(8)  # short enough for the 64-byte callback_data limit
    store.save_request(token, {"url": url, "platform": platform, "user_id": user_id})
    keyboard = {"inline_keyboard": [[
        {"text": "🎬 Video", "callback_data": f"dl|v|{token}"},
        {"text": "🎵 Audio", "callback_data": f"dl|a|{token}"},
    ]]}
    tg.send_message(chat_id, messages.choose_format(PLATFORM_LABELS[platform]), reply_markup=keyboard)


# --- commands ---

def _handle_command(text: str, chat_id: int, user_id: int, tg: TelegramClient) -> None:
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()  # strip "@BotName" suffix in groups
    args = parts[1:]
    s = get_settings()

    if cmd == "/start":
        tg.send_message(chat_id, messages.WELCOME)
    elif cmd == "/help":
        tg.send_message(chat_id, messages.HELP)
    elif cmd == "/terms":
        tg.send_message(chat_id, messages.terms(s.terms_url))
    elif cmd == "/status":
        tg.send_message(chat_id, messages.status(store.usage_today(user_id), s.rate_per_day))
    elif cmd == "/cancel":
        token = store.active_job(user_id)
        if token:
            store.mark_cancel(token)
            tg.send_message(chat_id, messages.CANCEL_REQUESTED)
        else:
            tg.send_message(chat_id, messages.NO_ACTIVE_JOB)
    elif cmd == "/admin":
        _handle_admin(args, chat_id, user_id, tg)
    else:
        tg.send_message(chat_id, messages.UNKNOWN_COMMAND)


def _handle_admin(args: list[str], chat_id: int, user_id: int, tg: TelegramClient) -> None:
    # Non-admins get the same reply as an unknown command, so the command is not revealed.
    if user_id not in get_settings().admin_id_set:
        tg.send_message(chat_id, messages.UNKNOWN_COMMAND)
        return

    action = args[0].lower() if args else "stats"
    if action == "stats" and len(args) <= 1:
        tg.send_message(chat_id, messages.admin_stats(db.stats()))
    elif action in ("ban", "unban") and len(args) == 2 and args[1].isdigit():
        target = int(args[1])
        db.set_banned(target, action == "ban")
        db.audit(user_id, action, target)
        log.info("admin action", extra={"user_id": target})
        tg.send_message(chat_id, messages.admin_done(action, target))
    else:
        tg.send_message(chat_id, messages.ADMIN_USAGE)


# --- inline button callbacks ---

def _handle_callback(cq: dict, tg: TelegramClient) -> None:
    tg.answer_callback(cq["id"])  # stop the loading spinner immediately

    user_id = (cq.get("from") or {}).get("id")
    msg = cq.get("message") or {}
    parts = (cq.get("data") or "").split("|")
    if len(parts) != 3 or parts[0] != "dl" or parts[1] not in FORMATS or not msg:
        return

    fmt, token = FORMATS[parts[1]], parts[2]
    chat_id, message_id = msg["chat"]["id"], msg["message_id"]

    req = store.load_request(token)
    if req is None:
        tg.edit_message(chat_id, message_id, messages.EXPIRED)
        return
    if req["user_id"] != user_id or db.is_banned(user_id):
        return  # someone else tapping the button, or banned user
    if not store.claim(token, fmt):
        return  # duplicate tap on the same format

    h = url_hash(req["url"])
    cached = store.cache_get(h, fmt)
    if cached:
        tg.send_cached(chat_id, fmt, cached, messages.CACHED_CAPTION)
        tg.edit_message(chat_id, message_id, messages.DONE)
        return

    db_id = db.create_request(user_id, req["url"], req["platform"], fmt)
    store.enqueue({
        "token": token,
        "db_id": db_id,
        "fmt": fmt,
        "url": req["url"],
        "platform": req["platform"],
        "user_id": user_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "attempt": 1,
    })
    store.set_active(user_id, token)
    tg.edit_message(chat_id, message_id, messages.QUEUED)
