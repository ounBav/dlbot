import logging
import os
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("config")

# Load .env only in development (not present in Docker image)
_env_file = ".env" if os.path.exists(".env") else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file,
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str
    webhook_secret: str = "dev-webhook-secret-change-in-production"
    public_base_url: str = ""
    db_host: str
    db_port: int = 5432
    db_schema: str
    db_username: str
    db_password: str
    db_connection: Literal["postgres"] = "postgres"
    db_logging: bool = False
    db_sync: bool = True
    redis_url: str
    admin_ids: str = ""
    terms_url: str = ""

    max_upload_mb: int = 50
    rate_per_minute: int = 5
    rate_per_day: int = 30
    worker_concurrency: int = 2
    max_job_attempts: int = 3
    temp_dir: str = "/tmp/dlbot"

    # Telegram Mini App (apps/miniapp). Empty MINIAPP_URL = Mini App disabled (no menu button).
    miniapp_url: str = ""
    miniapp_auth_max_age: int = 86400       # reject initData older than this (seconds)
    cors_origins: str = ""                   # extra comma-separated origins, e.g. local Vite dev

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
    settings = Settings()

    # CRITICAL: Verify database host is NOT localhost in production (Render)
    if settings.db_host in ("localhost", "127.0.0.1", "::1"):
        # This is OK in development, but log it
        if _env_file:
            log.info(f"Development mode: DB_HOST={settings.db_host} (from .env)")
        else:
            # Production should NEVER use localhost
            log.error(f"ERROR: DB_HOST is {settings.db_host} but .env not found!")
            log.error("RENDER MUST inject DB_HOST from managed database!")
            log.error("Check: Render Dashboard → bot-webhook → Environment")
            raise ValueError(
                f"Invalid DB_HOST: {settings.db_host}. "
                "In production, Render must auto-link the database host."
            )

    # Warn if using default webhook secret (not production safe)
    if settings.webhook_secret == "dev-webhook-secret-change-in-production":
        if _env_file:
            log.info("Development mode: using default webhook secret")
        else:
            log.warning("WEBHOOK_SECRET not set - using default (not secure for production)")
            log.warning("Set WEBHOOK_SECRET in Render Environment variables")

    return settings
