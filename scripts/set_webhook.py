"""Register the Telegram webhook. Run once after each deploy URL change.

Usage (from repo root):  PYTHONPATH=. python scripts/set_webhook.py
"""

from app.config import get_settings
from app.tg import TelegramClient


def main() -> None:
    s = get_settings()
    tg = TelegramClient(s.bot_token)
    result = tg.call(
        "setWebhook",
        url=f"{s.public_base_url.rstrip('/')}/telegram/webhook",
        secret_token=s.webhook_secret,
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )
    print("setWebhook:", result)
    print("getWebhookInfo:", tg.call("getWebhookInfo"))


if __name__ == "__main__":
    main()
