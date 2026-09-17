from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CP_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://cloudportal@localhost/cloudportal"
    redis_url: str = "redis://localhost:6379/0"
    master_key_file: Path = Path("/etc/cloudportal-backed/master.key")
    data_dir: Path = Path("/var/lib/cloudportal-backed")
    source_dir: Path = Path(__file__).resolve().parents[1]
    access_seconds: int = 900
    refresh_seconds: int = 28800
    login_attempts: int = 5
    lockout_seconds: int = 900
    request_limit: int = 300
    worker_count: int = Field(default=1, ge=1, le=64)
    execution_timeout: int = 3600
    allow_http: bool = False


@lru_cache
def settings() -> Settings:
    return Settings()
