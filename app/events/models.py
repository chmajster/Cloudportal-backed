"""Durable administrator-managed Event Broker subscriptions and delivery history."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Timestamp, now, uid


class EventContext(Base):
    """Queryable normalized context for an immutable EventRecord."""

    __tablename__ = "event_contexts"
    __table_args__ = (
        Index("ix_event_context_scope_time", "tenant_id", "project_id", "event_sequence"),
        Index("ix_event_context_resource", "resource_id", "event_sequence"),
        Index("ix_event_context_job", "job_id", "event_sequence"),
        Index("ix_event_context_workflow", "workflow_id", "event_sequence"),
    )

    event_sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("event_records.sequence", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    apmid: Mapped[str | None] = mapped_column(String(64), index=True)
    environment: Mapped[str | None] = mapped_column(String(32), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), index=True)
    deployment_id: Mapped[str | None] = mapped_column(String(36), index=True)
    provider_id: Mapped[str | None] = mapped_column(String(64), index=True)
    provider_type: Mapped[str | None] = mapped_column(String(32), index=True)
    resource_pool_id: Mapped[str | None] = mapped_column(String(64), index=True)
    blueprint_id: Mapped[str | None] = mapped_column(String(64), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(64), index=True)
    parent_event_id: Mapped[str | None] = mapped_column(String(36), index=True)
    root_event_id: Mapped[str | None] = mapped_column(String(36), index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)\n    context_json: Mapped[dict] = mapped_column(JSON, default=dict)\n

class EventSubscription(Timestamp, Base):
    __tablename__ = "event_subscriptions"
    __table_args__ = (
        Index("ix_event_subscription_match", "event_pattern", "phase", "is_enabled", "priority"),
        Index("ix_event_subscription_scope", "tenant_id", "project_id", "is_enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    event_pattern: Mapped[str] = mapped_column(String(128), index=True)
    phase: Mapped[str] = mapped_column(String(8), default="POST", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5000, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_blocking: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    filter_json: Mapped[dict] = mapped_column(JSON, default=dict)
    action_type: Mapped[str] = mapped_column(String(48), index=True)
    action_config: Mapped[dict] = mapped_column(JSON, default=dict)
    encrypted_action_secret: Mapped[bytes | None] = mapped_column(LargeBinary)
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("credentials.id", ondelete="RESTRICT"), index=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, default=10)
    backoff: Mapped[str] = mapped_column(String(16), default="exponential")
    max_retry_delay_seconds: Mapped[int] = mapped_column(Integer, default=300)
    retry_on: Mapped[list] = mapped_column(JSON, default=list)
    fail_policy: Mapped[str] = mapped_column(String(16), default="open")
    execution_policy: Mapped[str] = mapped_column(String(16), default="all")
    stop_on_block: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_on_failure: Mapped[bool] = mapped_column(Boolean, default=False)
    continue_on_failure: Mapped[bool] = mapped_column(Boolean, default=True)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=5)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    rate_limit_policy: Mapped[str] = mapped_column(String(16), default="queue")
    dedup_window_seconds: Mapped[int] = mapped_column(Integer, default=0)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))\n    run_as_token_id: Mapped[int | None] = mapped_column(ForeignKey("tokens.id", ondelete="SET NULL"), index=True)\n

class EventReplay(Base):
    __tablename__ = "event_replays"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    original_event_id: Mapped[str] = mapped_column(String(36), index=True)
    original_event_sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("event_records.sequence", ondelete="RESTRICT"),
        index=True,
    )
    target_subscription_id: Mapped[str | None] = mapped_column(
        ForeignKey("event_subscriptions.id", ondelete="SET NULL"), index=True
    )
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class EventDelivery(Timestamp, Base):
    __tablename__ = "event_subscription_deliveries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_event_subscription_delivery_idempotency"),
        Index("ix_event_delivery_due", "status", "next_retry_at"),
        Index("ix_event_delivery_subscription_status", "subscription_id", "status"),
        Index("ix_event_delivery_event_status", "event_sequence", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    event_sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("event_records.sequence", ondelete="CASCADE"),
        index=True,
    )
    event_id: Mapped[str] = mapped_column(String(36), index=True)
    subscription_id: Mapped[str | None] = mapped_column(
        ForeignKey("event_subscriptions.id", ondelete="SET NULL"), index=True
    )
    replay_id: Mapped[str | None] = mapped_column(
        ForeignKey("event_replays.id", ondelete="SET NULL"), index=True
    )
    original_delivery_id: Mapped[str | None] = mapped_column(String(36), index=True)
    subscription_version: Mapped[int] = mapped_column(Integer)
    subscription_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)\n    encrypted_secret_snapshot: Mapped[bytes | None] = mapped_column(LargeBinary)\n    action_type: Mapped[str] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    response_code: Mapped[int | None] = mapped_column(Integer)
    response_headers: Mapped[dict] = mapped_column(JSON, default=dict)
    response_body: Mapped[str | None] = mapped_column(Text)
    external_job_id: Mapped[str | None] = mapped_column(String(128), index=True)
    decision: Mapped[str | None] = mapped_column(String(24))
    decision_reason: Mapped[str | None] = mapped_column(String(1000))
    decision_patch: Mapped[dict] = mapped_column(JSON, default=dict)
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    next_retry_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class EventAttempt(Base):
    __tablename__ = "event_delivery_attempts"
    __table_args__ = (
        UniqueConstraint("delivery_id", "attempt", name="uq_event_delivery_attempt"),
        Index("ix_event_attempt_delivery_time", "delivery_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("event_subscription_deliveries.id", ondelete="CASCADE"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), index=True)
    response_code: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(String(1000))


class EventDeadLetter(Timestamp, Base):
    __tablename__ = "event_dead_letters"
    __table_args__ = (
        UniqueConstraint("delivery_id", name="uq_event_dead_letter_delivery"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("event_subscription_deliveries.id", ondelete="CASCADE"), unique=True, index=True
    )
    event_id: Mapped[str] = mapped_column(String(36), index=True)
    subscription_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error_type: Mapped[str | None] = mapped_column(String(128))
    last_error_message: Mapped[str | None] = mapped_column(String(1000))
    last_response_code: Mapped[int | None] = mapped_column(Integer)
    replay_id: Mapped[str | None] = mapped_column(String(36), index=True)
