from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Timestamp, now, uid


class PolicyDefinition(Timestamp, Base):
    __tablename__ = "policy_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    policy_type: Mapped[str] = mapped_column(String(40), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5000, index=True)
    enforcement: Mapped[str] = mapped_column(String(16), default="hard", index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)

    # Null/null means a platform policy. Tenant/null means tenant-wide. Both
    # values set means a project policy. Finer dimensions live in scope.
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)

    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    condition: Mapped[dict] = mapped_column(JSON, default=dict)
    effects: Mapped[list] = mapped_column(JSON, default=list)

    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_policy_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("policy_definitions.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class PolicyException(Timestamp, Base):
    __tablename__ = "policy_exceptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("policy_definitions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(Text)
    ticket: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(16), default="approved", index=True)
    condition: Mapped[dict] = mapped_column(JSON, default=dict)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(160), index=True)
    decision: Mapped[str] = mapped_column(String(24), index=True)
    matched_policy_ids: Mapped[list] = mapped_column(JSON, default=list)
    effects: Mapped[list] = mapped_column(JSON, default=list)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    context_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    dry_run: Mapped[bool] = mapped_column(default=False, index=True)
