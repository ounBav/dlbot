from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    webhook_secret: str
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

    @property
    def admin_id_set(self) -> set[int]:
        return {int(x) for x in self.admin_ids.split(",") if x.strip().isdigit()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
