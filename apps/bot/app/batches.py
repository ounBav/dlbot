"""TikTok profile batches (Mini App): send a whole profile listing, delivered in waves.

The picked videos are kept as one batch (store.put_batch). They go to the shared worker queue
PROFILE_BATCH_SIZE at a time (default 10). The next wave is released only when every job of the
current wave has finished, so a 200-video batch never floods the queue: other users' jobs
interleave with it, and RAM and temp-disk use stay bounded.

Who releases waves: the send request releases the first one right away; after that the dispatcher
thread in app/worker.py calls tick() every second. Picking a wave happens under the store lock, so
the two can never release the same videos twice.

State is in memory like everything else: a restart forgets running batches (and their queued jobs).
"""

import logging
import math
import secrets
import time

from . import jobs, store
from .tg import TelegramClient, TelegramError

log = logging.getLogger("batches")

WAITING = "waiting"   # picked by the user, not yet released to the worker queue
FINAL = frozenset({"done", "failed", "cancelled", "expired"})
BATCH_TTL = 2 * store.REQUEST_TTL   # refreshed on every wave; a finished batch is readable this long


def create(user_id: int, username: str, videos: list[dict], batch_size: int) -> str:
    """Remember the selection as a batch. Nothing is queued yet: call release() next."""
    batch_id = secrets.token_urlsafe(12)
    store.put_batch(batch_id, {
        "id": batch_id, "user_id": user_id, "username": username, "batch_size": max(1, batch_size),
        "created_at": time.time(), "cancelled": False, "cursor": 0,
        "items": [{"video_id": v["id"], "url": v["url"], "title": v.get("title") or "",
                   "thumbnail": v.get("thumbnail"), "duration": v.get("duration"),
                   "status": WAITING, "id": None, "token": None, "error_code": None}
                  for v in videos],
    }, BATCH_TTL)
    return batch_id


def _refresh(item: dict, user_id: int) -> None:
    """Update a released item from its job's live progress (or the request log once it is gone)."""
    if item["status"] in FINAL or item["id"] is None:
        return
    progress = store.get_progress(item["id"])
    if progress:
        item["status"] = progress.get("stage") or "queued"
        item["error_code"] = progress.get("error_code") or None
        return
    row = store.get_request(item["id"], user_id)
    if row and row["status"] in FINAL:
        item["status"], item["error_code"] = row["status"], row["error_code"]
    else:
        item["status"] = "expired"   # progress dropped after REQUEST_TTL: never block the batch


def _finished(batch: dict) -> bool:
    return all(it["status"] in FINAL for it in batch["items"])


def _pick_wave(batch: dict) -> tuple[list[dict], bool]:
    """Under the store lock: claim the next wave if the current one is over.
    Returns (items to submit, batch still open)."""
    items, user_id = batch["items"], batch["user_id"]
    for it in items[:batch["cursor"]]:
        _refresh(it, user_id)
    if batch["cancelled"] or batch["cursor"] >= len(items):
        return [], not _finished(batch)
    if any(it["status"] not in FINAL for it in items[:batch["cursor"]]):
        return [], True   # current wave still running
    wave = items[batch["cursor"]:batch["cursor"] + batch["batch_size"]]
    for it in wave:
        # "queued" with no job id yet: counts as running until submit() records the id.
        it.update(status="queued", token=secrets.token_urlsafe(8))
    batch["cursor"] += len(wave)
    return [dict(it) for it in wave], True


def release(tg: TelegramClient, batch_id: str) -> int:
    """Queue the next wave of a batch if its current wave is over. Returns how many were released."""
    picked = store.batch_txn(batch_id, lambda b: (b["user_id"], *_pick_wave(b)))
    if picked is None:
        return 0
    user_id, wave, still_open = picked
    if still_open:
        store.touch_batch(batch_id, BATCH_TTL)
    if not wave:
        return 0

    results = {}
    for it in wave:
        try:
            db_id = jobs.submit(tg, token=it["token"], user_id=user_id, chat_id=user_id, message_id=None,
                                url=it["url"], platform="tiktok", fmt="video", source="profile",
                                delivery="chat", title=it["title"] or None)
            results[it["token"]] = (db_id, "queued" if db_id else "done", None)
        except TelegramError:   # cached file could not be re-sent
            log.warning("profile batch item failed", extra={"user_id": user_id, "platform": "tiktok"})
            results[it["token"]] = (None, "failed", "E-FAILED")

    def record(batch: dict) -> None:
        for item in batch["items"]:
            if item["token"] in results:
                item["id"], item["status"], item["error_code"] = results[item["token"]]

    store.batch_txn(batch_id, record)
    log.info("profile wave queued", extra={"user_id": user_id, "platform": "tiktok",
                                           "code": f"n={len(wave)}"})
    return len(wave)


def tick(tg: TelegramClient) -> int:
    """Dispatcher step: release the next wave of every batch that is ready for one."""
    released = 0
    for batch_id in store.batch_ids():
        try:
            released += release(tg, batch_id)
        except Exception:
            log.exception("batch dispatch failed")
    return released


def _view(batch: dict) -> dict:
    items, size = batch["items"], batch["batch_size"]
    counts: dict[str, int] = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    finished = _finished(batch)
    return {
        "id": batch["id"],
        "username": batch["username"],
        "state": ("cancelled" if batch["cancelled"] else "done") if finished else "running",
        "total": len(items),
        "batch_size": size,
        "waves": math.ceil(len(items) / size),
        "wave": math.ceil(batch["cursor"] / size),   # waves released so far (the current one)
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0) + counts.get("expired", 0),
        "cancelled": counts.get("cancelled", 0),
        "waiting": counts.get(WAITING, 0),
        "items": [{"video_id": it["video_id"], "id": it["id"], "title": it["title"],
                   "thumbnail": it["thumbnail"], "duration": it["duration"],
                   "status": it["status"], "error_code": it["error_code"], "wave": i // size + 1}
                  for i, it in enumerate(items)],
    }


def view(batch_id: str, user_id: int) -> dict | None:
    def fn(batch):
        if batch["user_id"] != user_id:
            return None
        for it in batch["items"]:
            _refresh(it, user_id)
        return _view(batch)
    return store.batch_txn(batch_id, fn)


def cancel(batch_id: str, user_id: int) -> dict | None:
    """Stop the batch: unreleased videos are dropped (their quota is given back), running jobs
    get the normal cancel flag and stop before upload."""
    def fn(batch):
        if batch["user_id"] != user_id:
            return None
        refund = 0
        if not batch["cancelled"]:
            batch["cancelled"] = True
            for it in batch["items"]:
                if it["status"] == WAITING:
                    it["status"] = "cancelled"
                    refund += 1
                elif it["token"] and it["status"] not in FINAL:
                    store.mark_cancel(it["token"])
        return refund, _view(batch)

    result = store.batch_txn(batch_id, fn)
    if result is None:
        return None
    refund, data = result
    if refund:
        store.release_daily(user_id, refund)
    return data


def active_for(user_id: int) -> str | None:
    """The user's batch that is still running, if any (one at a time per user)."""
    for batch_id in store.batch_ids():
        data = view(batch_id, user_id)
        if data and data["state"] == "running":
            return batch_id
    return None
