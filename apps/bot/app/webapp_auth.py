"""Validate Telegram Mini App `initData` (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).

The Mini App sends `Authorization: tma <initData>`. initData is a query string signed by Telegram
with a key derived from the bot token, so a valid hash proves the user id came from Telegram.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(Exception):
    pass


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def sign(fields: dict[str, str], bot_token: str) -> str:
    """Compute the hash Telegram would put in initData. Used by validate() and by tests."""
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    return hmac.new(_secret_key(bot_token), check_string.encode(), hashlib.sha256).hexdigest()


def validate(init_data: str, bot_token: str, max_age: int, now: int | None = None) -> dict:
    """Return the Telegram user dict, or raise InitDataError."""
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        raise InitDataError("malformed")

    received = fields.pop("hash", "")
    if not received or not hmac.compare_digest(sign(fields, bot_token), received):
        raise InitDataError("bad hash")

    try:
        auth_date = int(fields.get("auth_date", ""))
    except ValueError:
        raise InitDataError("no auth_date")
    if (now if now is not None else int(time.time())) - auth_date > max_age:
        raise InitDataError("expired")

    try:
        user = json.loads(fields.get("user", ""))
    except ValueError:
        raise InitDataError("no user")
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise InitDataError("no user")
    return user
