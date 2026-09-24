from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RestoreRequest(BaseModel):
    confirmation: str
    safety_backup: bool = True

    @field_validator("confirmation")
    @classmethod
    def exact_confirmation(cls, value: str) -> str:
        if value != "RESTORE":
            raise ValueError("Wpisz dokładnie RESTORE")
        return value


class BackupStatus(BaseModel):
    id: int
    backup_uuid: str
    status: str
    progress: int
    stage: str
    download_ready: bool
    filename: str
    created_at: str | None = None
    expires_at: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    application_version: str | None = None
    git_commit: str | None = None
    error: str | None = None


class BackupList(BaseModel):
    items: list[BackupStatus] = Field(default_factory=list)
