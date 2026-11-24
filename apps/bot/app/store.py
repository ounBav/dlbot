"""In-memory state: one thread-safe singleton for the whole process.

Replaces Redis and Postgres. Holds the job queue, pending requests, file_id cache, rate limits,
cancel flags, update dedupe, users/bans and a bounded request log.

Trade-off (accepted for v1): everything lives in RAM, so a restart or redeploy forgets queued jobs,
bans, stats and the file_id cache. The web server MUST run as a single process (one uvicorn worker),
because each process would otherwise get its own copy of this state.
"""

import queue
import threading
from collections import deque
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from .url_utils import url_hash

REQUEST_TTL = 3600          # pending requests, claims, cancel flags, active job markers, progress
CACHE_TTL = 30 * 86400      # Telegram file_id cache
UPDATE_TTL = 86400          # Telegram update_id dedupe window
MAX_REQUEST_LOG = 5000      # request log rows kept in memory (oldest dropped first)
_SWEEP_EVERY = 60           # seconds between expired-key sweeps


class _TTLDict:
    """dict with per-key expiry. Not thread-safe on its own: MemoryStore holds the lock."""

    def __init__(self):
        self._data: dict[str, tuple[float, object]] = {}

    def get(self, key: str, now: float):
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= now:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value, ttl: float, now: float) -> None:
        self._data[key] = (now + ttl, value)

    def set_nx(self, key: str, value, ttl: float, now: float) -> bool:
        if self.get(key, now) is not None:
            return False
        self.set(key, value, ttl, now)
        return True

    def add_within(self, key: str, n: int, limit: int, ttl: float, now: float) -> tuple[bool, int]:
        """Add n to a counter only if the result stays <= limit. Returns (added, value after)."""
        item = self.get(key, now)
        current = item or 0
        if current + n > limit:
            return False, current
        value = max(current + n, 0)
        expires = self._data[key][0] if item is not None else now + ttl
        self._data[key] = (expires, value)
        return True, value

    def incr(self, key: str, ttl: float, now: float) -> int:
        """Increment a counter, keeping the expiry set when the counter was created."""
        if self.get(key, now) is None:
            self._data[key] = (now + ttl, 1)
            return 1
        expires, value = self._data[key]
        self._data[key] = (expires, value + 1)
        return value + 1

    def sweep(self, now: float) -> None:
        for key in [k for k, (exp, _) in self._data.items() if exp <= now]:
            del self._data[key]

    def __len__(self) -> int:
        return len(self._data)


class MemoryStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._kv = _TTLDict()                      # req:, claim:, active:, cancel:, upd:, rl:, fid:
        self._progress: dict[str, tuple[float, dict]] = {}
        self._queue: queue.Queue[dict] = queue.Queue()
        self._users: dict[int, dict] = {}
        self._requests: OrderedDict[str, dict] = OrderedDict()
        self._audit: list[dict] = []
        self._files: dict[str, tuple[float, dict]] = {}   # Mini App "save to device" tokens
        self._windows: dict[str, deque] = {}               # sliding-window rate limits
        self._scans: dict[int, tuple[float, dict]] = {}    # TikTok profile listing, one per user
        self._batches: dict[str, tuple[float, dict]] = {}  # TikTok profile batches (app/batches.py)
        self._last_sweep = 0.0
        self.worker_heartbeat = 0.0

    def _now(self) -> float:
        now = time.time()
        if now - self._last_sweep > _SWEEP_EVERY:
            self._last_sweep = now
            self._kv.sweep(now)
            for key in [k for k, (exp, _) in self._progress.items() if exp <= now]:
                del self._progress[key]
            for uid in [k for k, (exp, _) in self._scans.items() if exp <= now]:
                del self._scans[uid]
            for bid in [k for k, (exp, _) in self._batches.items() if exp <= now]:
                del self._batches[bid]
        return now

    # --- generic key/value with TTL ---

    def kv_get(self, key: str):
        with self._lock:
            return self._kv.get(key, self._now())

    def kv_set(self, key: str, value, ttl: float) -> None:
        with self._lock:
            self._kv.set(key, value, ttl, self._now())

    def kv_set_nx(self, key: str, value, ttl: float) -> bool:
        with self._lock:
            return self._kv.set_nx(key, value, ttl, self._now())

    def kv_incr(self, key: str, ttl: float) -> int:
        """Increment a counter; the TTL is set when the counter is created."""
        with self._lock:
            return self._kv.incr(key, ttl, self._now())

    # --- queue ---

    def enqueue(self, job: dict) -> None:
        self._queue.put(dict(job))

    def dequeue(self, timeout: float) -> dict | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def queue_size(self) -> int:
        return self._queue.qsize()

    # --- live job progress ---

    def set_progress(self, db_id: str, fields: dict) -> None:
        with self._lock:
            now = self._now()
            current = self._progress.get(db_id)
            data = dict(current[1]) if current and current[0] > now else {}
            data.update(fields)
            self._progress[db_id] = (now + REQUEST_TTL, data)

    def get_progress(self, db_id: str) -> dict | None:
        with self._lock:
            item = self._progress.get(db_id)
            if item is None or item[0] <= self._now():
                return None
            return dict(item[1])

    # --- Mini App downloadable files (token -> file metadata) ---

    def put_file(self, token: str, meta: dict, ttl: float) -> float:
        with self._lock:
            expires = self._now() + ttl
            self._files[token] = (expires, dict(meta))
            return expires

    def get_file(self, token: str) -> dict | None:
        with self._lock:
            item = self._files.get(token)
            if item is None or item[0] <= self._now():
                return None
            return {**item[1], "expires_at": item[0]}

    def pop_expired_files(self) -> list[dict]:
        """Forget expired file tokens and return their metadata so the caller deletes the files."""
        with self._lock:
            now = time.time()
            gone = [k for k, (exp, _) in self._files.items() if exp <= now]
            return [self._files.pop(k)[1] for k in gone]

    def kv_add_within(self, key: str, n: int, limit: int, ttl: float) -> tuple[bool, int]:
        with self._lock:
            return self._kv.add_within(key, n, limit, ttl, self._now())

    # --- TikTok profile scans (one-time: dropped on send, discard, expiry or restart) ---

    def put_scan(self, user_id: int, scan: dict, ttl: float) -> float:
        with self._lock:
            expires = self._now() + ttl
            self._scans[user_id] = (expires, scan)
            return expires

    def get_scan(self, user_id: int) -> dict | None:
        with self._lock:
            item = self._scans.get(user_id)
            if item is None or item[0] <= self._now():
                self._scans.pop(user_id, None)
                return None
            return {**item[1], "expires_at": item[0]}

    def drop_scan(self, user_id: int, scan_id: str | None = None) -> bool:
        """Forget the user's scan (only if it is `scan_id`, when given). True if one was removed."""
        with self._lock:
            item = self._scans.get(user_id)
            if item is None or (scan_id is not None and item[1].get("id") != scan_id):
                return False
            del self._scans[user_id]
            return True

    def scan_count(self) -> int:
        with self._lock:
            return len(self._scans)

    # --- TikTok profile batches (logic in app/batches.py; mutated only through batch_txn) ---

    def put_batch(self, batch_id: str, batch: dict, ttl: float) -> None:
        with self._lock:
            self._batches[batch_id] = (self._now() + ttl, batch)

    def batch_txn(self, batch_id: str, fn):
        """Run fn(batch) under the store lock. None if the batch is unknown or expired."""
        with self._lock:
            item = self._batches.get(batch_id)
            if item is None or item[0] <= self._now():
                self._batches.pop(batch_id, None)
                return None
            return fn(item[1])

    def touch_batch(self, batch_id: str, ttl: float) -> None:
        with self._lock:
            item = self._batches.get(batch_id)
            if item is not None:
                self._batches[batch_id] = (self._now() + ttl, item[1])

    def batch_ids(self) -> list[str]:
        with self._lock:
            now = self._now()
            return [k for k, (exp, _) in self._batches.items() if exp > now]

    # --- sliding-window rate limit ---

    def hit_window(self, key: str, limit: int, window: float) -> tuple[bool, int]:
        with self._lock:
            now = time.time()
            hits = self._windows.setdefault(key, deque())
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                return False, max(1, int(hits[0] + window - now + 0.999))
            hits.append(now)
            if len(self._windows) > 10_000:   # bound memory: drop idle keys
                for k in [k for k, v in self._windows.items() if not v or v[-1] <= now - window]:
                    del self._windows[k]
            return True, 0

    # --- users ---

    def upsert_user(self, telegram_id: int, username: str | None) -> None:
        with self._lock:
            user = self._users.setdefault(telegram_id, {"is_banned": False})
            user["username"] = username

    def is_banned(self, telegram_id: int) -> bool:
        with self._lock:
            return bool(self._users.get(telegram_id, {}).get("is_banned"))

    def set_banned(self, telegram_id: int, banned: bool) -> None:
        with self._lock:
            self._users.setdefault(telegram_id, {"username": None})["is_banned"] = banned

    def audit(self, admin_id: int, action: str, target_id: int | None) -> None:
        with self._lock:
            self._audit.append({"admin_id": admin_id, "action": action, "target_id": target_id,
                                "created_at": datetime.now(timezone.utc)})
            del self._audit[:-1000]

    # --- request log ---

    def create_request(self, telegram_id: int, url: str, platform: str, fmt: str, source: str) -> str:
        rid = str(uuid.uuid4())
        with self._lock:
            self._requests[rid] = {
                "id": rid, "telegram_id": telegram_id, "url": url, "url_hash": url_hash(url),
                "platform": platform, "format": fmt, "source": source, "status": "received",
                "error_code": None, "title": None, "file_size": None, "duration_ms": None,
                "created_at": datetime.now(timezone.utc), "finished_at": None,
            }
            while len(self._requests) > MAX_REQUEST_LOG:
                self._requests.popitem(last=False)
        return rid

    def finish_request(self, rid: str, status: str, error_code=None, file_size=None,
                       duration_ms=None, title=None) -> None:
        with self._lock:
            row = self._requests.get(rid)
            if row is None:
                return
            row.update(status=status, error_code=error_code, file_size=file_size,
                       duration_ms=duration_ms, finished_at=datetime.now(timezone.utc))
            if title is not None:
                row["title"] = title

    def get_request(self, rid: str, telegram_id: int) -> dict | None:
        with self._lock:
            row = self._requests.get(rid)
            return _public_row(row) if row and row["telegram_id"] == telegram_id else None

    def recent_requests(self, telegram_id: int, limit: int) -> list[dict]:
        with self._lock:
            rows = [r for r in reversed(self._requests.values()) if r["telegram_id"] == telegram_id]
            return [_public_row(r) for r in rows[:limit]]

    def stats(self) -> dict:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        with self._lock:
            rows = [r for r in self._requests.values() if r["created_at"] > since]
        return {
            "total": len(rows),
            "done": sum(r["status"] == "done" for r in rows),
            "failed": sum(r["status"] == "failed" for r in rows),
            "users": len({r["telegram_id"] for r in rows}),
        }


_PUBLIC_FIELDS = ("id", "url", "platform", "format", "status", "error_code", "title", "file_size", "created_at")


def _public_row(row: dict) -> dict:
    return {k: row[k] for k in _PUBLIC_FIELDS}


# --- the singleton ---

_instance = MemoryStore()


def get_store() -> MemoryStore:
    return _instance


def reset() -> None:
    """Replace the singleton with an empty one. For tests only."""
    global _instance
    _instance = MemoryStore()


# --- module-level API (what handlers, jobs, api and worker call) ---

def ping() -> bool:
    return True


def save_request(token: str, data: dict) -> None:
    get_store().kv_set(f"req:{token}", dict(data), REQUEST_TTL)


def load_request(token: str) -> dict | None:
    data = get_store().kv_get(f"req:{token}")
    return dict(data) if data else None


def claim(token: str, fmt: str) -> bool:
    """True only for the first tap on a format button (prevents duplicate jobs)."""
    return get_store().kv_set_nx(f"claim:{token}:{fmt}", "1", REQUEST_TTL)


def enqueue(job: dict) -> None:
    get_store().enqueue(job)


def dequeue(timeout: float = 5) -> dict | None:
    return get_store().dequeue(timeout)


def set_active(user_id: int, token: str) -> None:
    get_store().kv_set(f"active:{user_id}", token, REQUEST_TTL)


def active_job(user_id: int) -> str | None:
    return get_store().kv_get(f"active:{user_id}")


def mark_cancel(token: str) -> None:
    get_store().kv_set(f"cancel:{token}", "1", REQUEST_TTL)


def is_cancelled(token: str) -> bool:
    return get_store().kv_get(f"cancel:{token}") is not None


def set_progress(db_id: str, **fields) -> None:
    """Merge fields into the live progress of a job. Stages: queued, downloading, uploading, retrying,
    done, failed, cancelled. Values are stored as strings (same shape the Mini App API always had)."""
    get_store().set_progress(db_id, {k: "" if v is None else str(v) for k, v in fields.items()})


def get_progress(db_id: str) -> dict | None:
    return get_store().get_progress(db_id)


def cache_get(url_h: str, fmt: str) -> str | None:
    return get_store().kv_get(f"fid:{url_h}:{fmt}")


def cache_set(url_h: str, fmt: str, file_id: str) -> None:
    get_store().kv_set(f"fid:{url_h}:{fmt}", file_id, CACHE_TTL)


def dedupe_update(update_id: int) -> bool:
    """Return True if this update is new; False if it was already seen."""
    return get_store().kv_set_nx(f"upd:{update_id}", "1", UPDATE_TTL)


def _keys(user_id: int, now: int) -> tuple[str, str]:
    day = time.strftime("%Y%m%d", time.gmtime(now))
    return f"rl:m:{user_id}:{now // 60}", f"rl:d:{user_id}:{day}"


def rate_limit_hit(user_id: int, per_minute: int, per_day: int) -> tuple[bool, int]:
    """Count one request (fixed windows: minute and UTC day). Returns (allowed, retry_after_seconds)."""
    now = int(time.time())
    minute_key, day_key = _keys(user_id, now)
    s = get_store()
    minute_count = s.kv_incr(minute_key, 120)
    day_count = s.kv_incr(day_key, 90000)
    if minute_count > per_minute:
        return False, 60 - (now % 60)
    if day_count > per_day:
        return False, 86400 - (now % 86400)
    return True, 0


def reserve_daily(user_id: int, n: int, per_day: int) -> tuple[bool, int]:
    """Count n downloads at once against today's quota, all or nothing (Mini App profile batch).
    Returns (allowed, remaining_today). The per-minute window does not apply to a batch: its size
    is capped by PROFILE_MAX_SEND instead."""
    _, day_key = _keys(user_id, int(time.time()))
    ok, used = get_store().kv_add_within(day_key, n, per_day, 90000)
    return ok, max(per_day - used, 0)


def release_daily(user_id: int, n: int) -> None:
    """Give back a reservation that could not be used (e.g. the bot chat was never started)."""
    _, day_key = _keys(user_id, int(time.time()))
    get_store().kv_add_within(day_key, -n, 10**9, 90000)


def put_scan(user_id: int, scan: dict, ttl: float) -> float:
    return get_store().put_scan(user_id, scan, ttl)


def get_scan(user_id: int) -> dict | None:
    return get_store().get_scan(user_id)


def drop_scan(user_id: int, scan_id: str | None = None) -> bool:
    return get_store().drop_scan(user_id, scan_id)


def put_batch(batch_id: str, batch: dict, ttl: float) -> None:
    get_store().put_batch(batch_id, batch, ttl)


def batch_txn(batch_id: str, fn):
    return get_store().batch_txn(batch_id, fn)


def touch_batch(batch_id: str, ttl: float) -> None:
    get_store().touch_batch(batch_id, ttl)


def batch_ids() -> list[str]:
    return get_store().batch_ids()


def hit_window(key: str, limit: int, window: float) -> tuple[bool, int]:
    """Sliding-window limiter (Mini App preview). Returns (allowed, retry_after_seconds)."""
    return get_store().hit_window(key, limit, window)


def put_file(token: str, meta: dict, ttl: float) -> float:
    """Register a finished Mini App file under an unguessable token. Returns the expiry (epoch s)."""
    return get_store().put_file(token, meta, ttl)


def get_file(token: str) -> dict | None:
    return get_store().get_file(token)


def pop_expired_files() -> list[dict]:
    return get_store().pop_expired_files()


def usage_today(user_id: int) -> int:
    _, day_key = _keys(user_id, int(time.time()))
    return int(get_store().kv_get(day_key) or 0)


def heartbeat() -> None:
    get_store().worker_heartbeat = time.time()


def last_heartbeat() -> float:
    return get_store().worker_heartbeat


# users / bans / request log (formerly Postgres)

def upsert_user(telegram_id: int, username: str | None) -> None:
    get_store().upsert_user(telegram_id, username)


def is_banned(telegram_id: int) -> bool:
    return get_store().is_banned(telegram_id)


def set_banned(telegram_id: int, banned: bool) -> None:
    get_store().set_banned(telegram_id, banned)


def audit(admin_id: int, action: str, target_id: int | None) -> None:
    get_store().audit(admin_id, action, target_id)


def create_request(telegram_id: int, url: str, platform: str, fmt: str, source: str = "chat") -> str:
    return get_store().create_request(telegram_id, url, platform, fmt, source)


def finish_request(request_id: str, status: str, error_code: str | None = None,
                   file_size: int | None = None, duration_ms: int | None = None,
                   title: str | None = None) -> None:
    get_store().finish_request(request_id, status, error_code, file_size, duration_ms, title)


def get_request(request_id: str, telegram_id: int) -> dict | None:
    return get_store().get_request(request_id, telegram_id)


def recent_requests(telegram_id: int, limit: int = 20) -> list[dict]:
    return get_store().recent_requests(telegram_id, limit)


def stats() -> dict:
    return get_store().stats()
