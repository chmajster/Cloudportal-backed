from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CP_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://cloudportal@localhost/cloudportal"
    redis_url: str = "redis://localhost:6379/0"
    master_key_file: Path = Path("/etc/cloudportal-backed/master.key")
    secret_backend: Literal['local', 'aws-kms', 'vault-transit'] = 'local'
    aws_kms_key_id: str | None = None
    aws_kms_region: str | None = None
    vault_addr: str | None = None
    vault_token_file: Path | None = None
    vault_transit_mount: str = 'transit'
    vault_transit_key: str | None = None
    data_dir: Path = Path("/var/lib/cloudportal-backed")
    source_dir: Path = Path(__file__).resolve().parents[1]
    access_seconds: int = 900
    refresh_seconds: int = 28800
    login_attempts: int = 5
    lockout_seconds: int = 900
    request_limit: int = 300
    worker_count: int = Field(default=1, ge=1, le=64)
    execution_timeout: int = 3600
    worker_auto_resume_enabled: bool = True
    worker_auto_resume_max_attempts: int = Field(default=3, ge=0, le=20)
    provider_offline_queue_enabled: bool = True
    provider_retry_base_seconds: int = Field(default=15, ge=5, le=3600)
    provider_retry_max_seconds: int = Field(default=300, ge=15, le=86400)
    provider_retry_max_attempts: int = Field(default=48, ge=1, le=10000)
    allow_http: bool = False
    webhook_allowed_hosts: list[str] = Field(default_factory=list)
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = 'cloudportal-backed'
    credential_expiry_warning_days: int = Field(default=30, ge=1, le=3650)
    retention_job_logs_days: int = Field(default=30, ge=1, le=3650)
    retention_audit_days: int = Field(default=365, ge=1, le=3650)
    retention_webhook_deliveries_days: int = Field(default=30, ge=1, le=3650)
    retention_events_days: int = Field(default=90, ge=1, le=3650)
    event_consumer_lag_warning: int = Field(default=10000, ge=1, le=1000000000)
    retention_idempotency_days: int = Field(default=7, ge=1, le=3650)
    retention_released_allocations_days: int = Field(default=90, ge=1, le=3650)


@lru_cache
def settings() -> Settings:
    return Settings()
