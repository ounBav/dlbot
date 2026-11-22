"""All user-facing text lives here. English only in v1; keep keys stable for i18n later."""

import html

WELCOME = (
    "👋 <b>Welcome!</b>\n"
    "Send me a public link from TikTok, YouTube or Facebook and I'll send the "
    "video or audio back to you.\n\n"
    "Use /help for details. Only download content you have the right to use: /terms"
)

HELP = (
    "<b>How to use this bot</b>\n"
    "1. Paste a public TikTok, YouTube or Facebook video link.\n"
    "2. Choose 🎬 Video or 🎵 Audio.\n"
    "3. Wait for the file to arrive.\n\n"
    "<b>Commands</b>\n"
    "/start – welcome message\n"
    "/help – this guide\n"
    "/status – your usage today\n"
    "/cancel – cancel your active download\n"
    "/terms – acceptable use\n\n"
    "<b>Limits</b>\n"
    "Files up to 50 MB. Private or login-only videos are not supported."
)

UNKNOWN_TEXT = "🔗 Send me a TikTok, YouTube or Facebook link. Type /help for help."
UNKNOWN_COMMAND = "❓ Unknown command. Type /help to see what I can do."
UNSUPPORTED = "❌ I can't read that link. I support public TikTok, YouTube and Facebook videos."
BANNED = "🚫 Your access to this bot has been restricted. Contact the administrator."
EXPIRED = "⌛ This request has expired. Send the link again."
QUEUED = "⏳ Added to the queue…"
DOWNLOADING = "⬇️ Downloading…"
UPLOADING = "⬆️ Almost done, sending your file…"
RETRYING = "🔁 A temporary problem occurred. Retrying…"
DONE = "✅ Sent. Send another link anytime."
CANCELLED = "🛑 Cancelled."
CANCEL_REQUESTED = "🛑 Cancelling your active download…"
NO_ACTIVE_JOB = "There is no active download to cancel."
CACHED_CAPTION = "♻️ Sent from cache"
ADMIN_USAGE = "Usage: /admin stats | /admin ban <telegram_id> | /admin unban <telegram_id>"

# Mini App (apps/miniapp). Button labels are short: Telegram truncates long ones.
OPEN_APP = "📱 Open app"
MENU_BUTTON = "Download"


def from_miniapp(platform_label: str, fmt: str) -> str:
    kind = "🎵 Audio" if fmt == "audio" else "🎬 Video"
    return f"📱 {platform_label} link received from the app ({kind})."


def from_profile(count: int, username: str) -> str:
    """One message for a whole Mini App "TikTok profile" batch (no per-video status messages)."""
    noun = "video" if count == 1 else "videos"
    return f"📱 Sending {count} TikTok {noun} from @{html.escape(username)}. They will arrive here one by one."


def batch_item_failed(code: str, title: str, ref: str, limit_mb: int) -> str:
    """A profile-batch video failed. It has no status message to edit, so say which one."""
    name = html.escape((title or "TikTok video")[:80])
    return f"<b>{name}</b>\n{error(code, ref=ref, limit_mb=limit_mb)}"

# Telegram "Menu" button (setMyCommands). Same wording as HELP. /admin is deliberately hidden.
BOT_COMMANDS = [
    ("start", "Welcome message"),
    ("help", "How to use this bot"),
    ("status", "Your usage today"),
    ("cancel", "Cancel your active download"),
    ("terms", "Acceptable use"),
]

ERROR_TEXT = {
    "E-UNSUPPORTED": "❌ I can't read that link. I support public TikTok, YouTube and Facebook videos.",
    "E-PRIVATE": "🔒 This video is private or needs a login, so I can't download it.",
    "E-TOOBIG": "📦 That file is over {limit} MB. Try the 🎵 Audio option instead.",
    "E-FAILED": "⚠️ Something went wrong on my side. Please try again later.",
}


def choose_format(platform_label: str) -> str:
    return f"✅ {platform_label} link detected.\nChoose a format:"


def rate_limited(retry_after: int) -> str:
    if retry_after > 3600:
        return "⏱ You've reached today's limit. Please try again tomorrow."
    return f"⏱ Slow down a little. Try again in {retry_after} seconds."


def error(code: str, ref: str, limit_mb: int) -> str:
    text = ERROR_TEXT.get(code, ERROR_TEXT["E-FAILED"]).format(limit=limit_mb)
    return f"{text}\nRef: {ref}"


def terms(url: str) -> str:
    link = html.escape(url) if url else "not configured yet"
    return (
        "📜 <b>Terms of use</b>\n"
        "Download only content you have the right to use. Respect creators' "
        "copyright and each platform's terms.\n"
        f"Full terms: {link}"
    )


def status(used_today: int, limit_per_day: int) -> str:
    remaining = max(limit_per_day - used_today, 0)
    return f"📊 Requests today: {used_today}/{limit_per_day}\nRemaining: {remaining}"


def caption(title: str, platform_label: str, duration_s: int) -> str:
    minutes, seconds = divmod(max(duration_s, 0), 60)
    return f"<b>{html.escape(title[:200])}</b>\n{platform_label} · {minutes}:{seconds:02d}"


def admin_stats(stats: dict) -> str:
    return (
        "📈 <b>Last 24 hours</b>\n"
        f"Requests: {stats['total']}\n"
        f"Done: {stats['done']}\n"
        f"Failed: {stats['failed']}\n"
        f"Unique users: {stats['users']}"
    )


def admin_done(action: str, target: int) -> str:
    return f"✅ User {target} {'banned' if action == 'ban' else 'unbanned'}."
