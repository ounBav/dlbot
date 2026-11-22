"""Telegram long polling (getUpdates). Replaces the webhook: no public URL, no tunnel, no secret.

Runs as one daemon thread inside the web process (started by app/main.py), next to the WorkerPool.
Updates are handled one at a time, in order, by handlers.handle_update; handlers only route and
enqueue, so a long download never blocks polling.

Only ONE poller may run per bot token: Telegram answers 409 Conflict to a second one (for example a
local `docker compose up` while Render is running with the same token).
"""

import logging
import threading
import time

import httpx

from . import store
from .handlers import handle_update
from .tg import TelegramClient, TelegramError

log = logging.getLogger("poller")

ALLOWED_UPDATES = ["message", "callback_query"]
MAX_BACKOFF = 60.0


class Poller:
    def __init__(self, tg: TelegramClient, poll_timeout: int = 25):
        self.tg = tg
        self.poll_timeout = poll_timeout
        self.offset: int | None = None
        self.last_ok: float | None = None    # time.monotonic() of the last successful getUpdates
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._backoff = 1.0

    # --- lifecycle ---

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        """Signal the loop to stop. An in-flight getUpdates may hold up to poll_timeout seconds;
        the thread is a daemon, so shutdown does not wait for it beyond `timeout`."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- loop ---

    def run(self) -> None:
        log.info("polling started", extra={"code": f"timeout={self.poll_timeout}"})
        while not self._stop.is_set() and not self._delete_webhook():
            pass
        while not self._stop.is_set():
            self.poll_once()
        log.info("polling stopped")

    def _delete_webhook(self) -> bool:
        """One deleteWebhook attempt (sleeps with backoff on failure). True when it succeeded."""
        try:
            self.tg.delete_webhook()
        except (TelegramError, httpx.HTTPError) as exc:
            self._sleep_after_error("deleteWebhook failed", exc)
            return False
        self._backoff = 1.0
        return True

    def poll_once(self) -> int:
        """One getUpdates round. Returns the number of updates handled."""
        try:
            updates = self.tg.get_updates(self.offset, self.poll_timeout, ALLOWED_UPDATES)
        except (TelegramError, httpx.HTTPError) as exc:
            self._sleep_after_error("getUpdates failed", exc)
            return 0

        self._backoff = 1.0
        self.last_ok = time.monotonic()
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                # Advance first: a poisonous update must never be redelivered forever.
                self.offset = update_id + 1
                if not store.dedupe_update(update_id):
                    continue
            try:
                handle_update(update, self.tg)
            except Exception:
                log.exception("update handling failed", extra={"code": str(update_id)})
        return len(updates)

    def _sleep_after_error(self, what: str, exc: Exception) -> None:
        if isinstance(exc, TelegramError) and exc.code == 409:
            # Another getUpdates consumer, or a webhook set by someone else: clear it and retry.
            log.error("%s: 409 conflict, another bot instance is polling or a webhook is set", what,
                      extra={"code": "E-CONFLICT"})
            try:
                self.tg.delete_webhook()
            except (TelegramError, httpx.HTTPError):
                pass
            delay = 5.0
        elif isinstance(exc, TelegramError) and exc.retry_after:
            delay = float(exc.retry_after)
        elif isinstance(exc, TelegramError) and exc.code == 401:
            log.error("%s: bot token rejected (401), check BOT_TOKEN", what, extra={"code": "E-AUTH"})
            delay = MAX_BACKOFF
        else:
            # Network errors and 5xx: exponential backoff. Never log the URL (it contains the token).
            log.warning("%s: %s", what, exc.__class__.__name__, extra={"code": "E-NETWORK"})
            delay = self._backoff
            self._backoff = min(self._backoff * 2, MAX_BACKOFF)
        self._stop.wait(delay)

