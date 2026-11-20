import logging
from pathlib import Path
from typing import Any

from psycopg import Connection, Cursor, conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings
from .url_utils import url_hash

log = logging.getLogger("database")
_pool: ConnectionPool | None = None
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        connection_info = conninfo.make_conninfo(
            host=settings.db_host,
            port=settings.db_port,
            dbname=settings.db_schema,
            user=settings.db_username,
            password=settings.db_password,
        )
        _pool = ConnectionPool(
            connection_info,
            min_size=1,
            max_size=5,
            timeout=60,  # Increased timeout for Render database initialization
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def run_migrations() -> None:
    """Apply every migrations/*.sql file in order. Scripts must be idempotent (IF NOT EXISTS)."""
    if not get_settings().db_sync:
        log.info("database migrations disabled")
        return
    with pool().connection() as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            _execute(conn, path.read_text())


def _execute(conn: Connection, query: str, params: tuple | None = None) -> Cursor[Any]:
    if get_settings().db_logging:
        log.info("SQL: %s", query)
    if params is None:
        return conn.execute(query)
    return conn.execute(query, params)


def upsert_user(telegram_id: int, username: str | None) -> None:
    with pool().connection() as conn:
        _execute(
            conn,
            """INSERT INTO users (telegram_id, username) VALUES (%s, %s)
               ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username""",
            (telegram_id, username),
        )


def is_banned(telegram_id: int) -> bool:
    with pool().connection() as conn:
        row = _execute(conn, "SELECT is_banned FROM users WHERE telegram_id = %s", (telegram_id,)).fetchone()
    return bool(row and row["is_banned"])


def set_banned(telegram_id: int, banned: bool) -> None:
    with pool().connection() as conn:
        _execute(conn, "UPDATE users SET is_banned = %s WHERE telegram_id = %s", (banned, telegram_id))


def audit(admin_id: int, action: str, target_id: int | None) -> None:
    with pool().connection() as conn:
        _execute(
            conn,
            "INSERT INTO admin_audit (admin_id, action, target_id) VALUES (%s, %s, %s)",
            (admin_id, action, target_id),
        )


def create_request(telegram_id: int, url: str, platform: str, fmt: str) -> str:
    with pool().connection() as conn:
        row = _execute(
            conn,
            """INSERT INTO requests (telegram_id, url, url_hash, platform, format)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (telegram_id, url, url_hash(url), platform, fmt),
        ).fetchone()
    return str(row["id"])


def finish_request(request_id: str, status: str, error_code: str | None = None,
                   file_size: int | None = None, duration_ms: int | None = None) -> None:
    with pool().connection() as conn:
        _execute(
            conn,
            """UPDATE requests SET status = %s, error_code = %s, file_size = %s,
                   duration_ms = %s, finished_at = now() WHERE id = %s""",
            (status, error_code, file_size, duration_ms, request_id),
        )


def save_media_cache(url_h: str, fmt: str, file_id: str, title: str, size: int) -> None:
    with pool().connection() as conn:
        _execute(
            conn,
            """INSERT INTO media_cache (url_hash, format, telegram_file_id, title, size)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (url_hash, format) DO UPDATE SET
                   telegram_file_id = EXCLUDED.telegram_file_id,
                   title = EXCLUDED.title, size = EXCLUDED.size, created_at = now()""",
            (url_h, fmt, file_id, title, size),
        )


def stats() -> dict:
    with pool().connection() as conn:
        row = _execute(
            conn,
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE status = 'done') AS done,
                      count(*) FILTER (WHERE status = 'failed') AS failed,
                      count(DISTINCT telegram_id) AS users
               FROM requests WHERE created_at > now() - interval '24 hours'"""
        ).fetchone()
    return dict(row)
