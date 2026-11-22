"""Redis access: job queue, pending requests, cache, rate limits, cancel flags, dedupe."""

import json
import time

import redis

from .config import get_settings

REQUEST_TTL = 3600          # pending requests and active job markers
CACHE_TTL = 30 * 86400      # Telegram file_id cache
UPDATE_TTL = 86400          # Telegram update_id dedupe window
QUEUE_KEY = "queue:downloads"

_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True, socket_timeout=10)
    return _client


def ping() -> bool:
    return bool(client().ping())


# --- pending requests (user sent a link, has not chosen a format yet) ---

def save_request(token: str, data: dict) -> None:
    client().set(f"req:{token}", json.dumps(data), ex=REQUEST_TTL)


def load_request(token: str) -> dict | None:
    raw = client().get(f"req:{token}")
    return json.loads(raw) if raw else None


def claim(token: str, fmt: str) -> bool:
    """True only for the first tap on a format button (prevents duplicate jobs)."""
    return bool(client().set(f"claim:{token}:{fmt}", "1", nx=True, ex=REQUEST_TTL))


# --- job queue (FIFO: LPUSH producer, BRPOP consumer) ---

def enqueue(job: dict) -> None:
    client().lpush(QUEUE_KEY, json.dumps(job))


def dequeue(timeout: int = 5) -> dict | None:
    item = client().brpop(QUEUE_KEY, timeout=timeout)
    return json.loads(item[1]) if item else None


def set_active(user_id: int, token: str) -> None:
    client().set(f"active:{user_id}", token, ex=REQUEST_TTL)


def active_job(user_id: int) -> str | None:
    return client().get(f"active:{user_id}")


def mark_cancel(token: str) -> None:
    client().set(f"cancel:{token}", "1", ex=REQUEST_TTL)


def is_cancelled(token: str) -> bool:
    return client().exists(f"cancel:{token}") == 1


# --- live job progress (read by the Mini App API; DB holds the final state) ---

def set_progress(db_id: str, **fields) -> None:
    """Merge fields into job:<db_id>. Stages: queued, downloading, uploading, retrying, done, failed, cancelled."""
    key = f"job:{db_id}"
    pipe = client().pipeline()
    pipe.hset(key, mapping={k: "" if v is None else str(v) for k, v in fields.items()})
    pipe.expire(key, REQUEST_TTL)
    pipe.execute()


def get_progress(db_id: str) -> dict | None:
    data = client().hgetall(f"job:{db_id}")
    return data or None


# --- file_id cache ---

def cache_get(url_hash: str, fmt: str) -> str | None:
    return client().get(f"fid:{url_hash}:{fmt}")


def cache_set(url_hash: str, fmt: str, file_id: str) -> None:
    client().set(f"fid:{url_hash}:{fmt}", file_id, ex=CACHE_TTL)


# --- webhook idempotency ---

def dedupe_update(update_id: int) -> bool:
    """Return True if this update is new; False if it was already seen."""
    return bool(client().set(f"upd:{update_id}", "1", nx=True, ex=UPDATE_TTL))


# --- rate limiting (fixed windows, minute and UTC day) ---

def _keys(user_id: int, now: int) -> tuple[str, str]:
    day = time.strftime("%Y%m%d", time.gmtime(now))
    return f"rl:m:{user_id}:{now // 60}", f"rl:d:{user_id}:{day}"


def rate_limit_hit(user_id: int, per_minute: int, per_day: int) -> tuple[bool, int]:
    """Count one request. Returns (allowed, retry_after_seconds)."""
    now = int(time.time())
    minute_key, day_key = _keys(user_id, now)
    pipe = client().pipeline()
    pipe.incr(minute_key)
    pipe.expire(minute_key, 120)
    pipe.incr(day_key)
    pipe.expire(day_key, 90000)
    minute_count, _, day_count, _ = pipe.execute()
    if minute_count > per_minute:
        return False, 60 - (now % 60)
    if day_count > per_day:
        return False, 86400 - (now % 86400)
    return True, 0


def usage_today(user_id: int) -> int:
    _, day_key = _keys(user_id, int(time.time()))
    return int(client().get(day_key) or 0)


# --- worker heartbeat (Render workers have no HTTP health check) ---

def heartbeat() -> None:
    client().set("worker:heartbeat", str(int(time.time())), ex=60)
