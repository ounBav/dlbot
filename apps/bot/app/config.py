import os
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


# Load .env only in development (not present in Docker image)
_env_file = ".env" if os.path.exists(".env") else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file,
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str
    # Telegram long polling: seconds Telegram holds each getUpdates call open (0 = short polling).
    poll_timeout: int = 25
    # Optional self-ping of RENDER_EXTERNAL_URL/health (Render sets that var). Off by default: it
    # only keeps an instance awake that is already awake; an external pinger is still needed.
    keepalive_self_ping: bool = False
    keepalive_interval_sec: int = 600
    render_external_url: str = ""
    admin_ids: str = ""
    terms_url: str = ""

    max_upload_mb: int = 50
    rate_per_minute: int = 5
    rate_per_day: int = 30
    worker_concurrency: int = 2
    max_job_attempts: int = 3
    # Per-job download folders live here and are deleted right after the file is sent.
    # Relative paths resolve against the working directory (/app in Docker -> /app/tmp).
    temp_dir: str = "tmp"

    # Telegram Mini App (apps/miniapp). Empty MINIAPP_URL = Mini App disabled (no menu button).
    miniapp_url: str = ""
    miniapp_auth_max_age: int = 86400       # reject initData older than this (seconds)
    cors_origins: str = ""                   # extra comma-separated origins, e.g. local Vite dev
    # "Save to device": finished files are kept under an unguessable link for this long, then deleted.
    file_ttl_min: int = 15
    # Preview (/api/info) limit per user, sliding window of 60 s. Downloads use RATE_PER_MINUTE/DAY.
    info_rate_per_minute: int = 20
    # Longest video accepted, in seconds. 0 = no limit (open question Q4 in CLAUDE.md).
    max_duration_sec: int = 0

    # Mini App "TikTok profile" tab (app/profiles.py). "All" lists at most PROFILE_MAX_VIDEOS.
    profile_max_videos: int = 200
    # Videos sent to the chat in one batch (capped at 20 in code: the UI tracks them through
    # GET /api/downloads, which returns the last 20 jobs).
    profile_max_send: int = 10
    # A listing is one-time: dropped on send/discard, otherwise forgotten after this many minutes.
    profile_scan_ttl_min: int = 15
    profile_scan_rate_per_minute: int = 3
    profile_scan_attempts: int = 2

    @property
    def admin_id_set(self) -> set[int]:
        return {int(x) for x in self.admin_ids.split(",") if x.strip().isdigit()}

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]
        if self.miniapp_url:
            parts = urlsplit(self.miniapp_url)
            origins.append(f"{parts.scheme}://{parts.netloc}")
        return sorted(set(origins))


@lru_cache
def get_settings() -> Settings:
    return Settings()
