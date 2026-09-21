from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Timestamp, now, uid


class Day2ActionRequest(Timestamp, Base):
    __tablename__ = 'day2_action_requests'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey('deployments.id'), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default='')
    requested_by: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    requested_at: Mapped[DateTime] = mapped_column(DateTime, default=now, index=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    approved_at: Mapped[DateTime | None] = mapped_column(DateTime)
    approval_state: Mapped[str] = mapped_column(String(24), default='not_required', index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('jobs.id'), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default='REQUESTED', index=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime)
    finished_at: Mapped[DateTime | None] = mapped_column(DateTime)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(36), default=uid, index=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    retry_of: Mapped[str | None] = mapped_column(ForeignKey('day2_action_requests.id'), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    safe_diff: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class Day2ResourceLock(Base):
    __tablename__ = 'day2_resource_locks'

    resource_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action_request_id: Mapped[str] = mapped_column(
        ForeignKey('day2_action_requests.id', ondelete='CASCADE'), unique=True, index=True
    )
    expires_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=now)


class Day2ResourceState(Timestamp, Base):
    __tablename__ = 'day2_resource_states'

    resource_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    protected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    platform_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    provider_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    desired_configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    actual_configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    last_synced_at: Mapped[DateTime | None] = mapped_column(DateTime)


class BulkDay2ActionRequest(Timestamp, Base):
    __tablename__ = 'bulk_day2_action_requests'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_ids: Mapped[list] = mapped_column(JSON, default=list)
    requested_by: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    status: Mapped[str] = mapped_column(String(24), default='QUEUED', index=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
