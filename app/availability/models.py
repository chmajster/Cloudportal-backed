"""Project-scoped Availability Plans and durable VM/deployment assignments."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Timestamp, now, uid
from app.resource_scope.columns import ResourceScope


class AvailabilityPlan(ResourceScope, Timestamp, Base):
    __tablename__ = 'availability_plans'
    __table_args__ = (
        ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_availability_plans_project_scope', ondelete='RESTRICT',
        ),
        UniqueConstraint('tenant_id', 'project_id', 'id', name='uq_availability_plans_scoped_id'),
        UniqueConstraint('tenant_id', 'project_id', 'normalized_name', name='uq_availability_plans_scope_name'),
        Index('ix_availability_plans_project_scope', 'tenant_id', 'project_id'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default='')
    state: Mapped[str] = mapped_column(String(16), default='started')
    group: Mapped[str | None] = mapped_column(String(63))
    max_restart: Mapped[int] = mapped_column(default=1)
    max_relocate: Mapped[int] = mapped_column(default=1)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class AvailabilityAssignment(ResourceScope, Timestamp, Base):
    __tablename__ = 'availability_assignments'
    __table_args__ = (
        ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_availability_assignments_project_scope', ondelete='RESTRICT',
        ),
        ForeignKeyConstraint(
            ['tenant_id', 'project_id', 'plan_id'],
            ['availability_plans.tenant_id', 'availability_plans.project_id', 'availability_plans.id'],
            name='fk_availability_assignments_plan_scope', ondelete='RESTRICT',
        ),
        ForeignKeyConstraint(
            ['tenant_id', 'project_id', 'deployment_id'],
            ['deployments.tenant_id', 'deployments.project_id', 'deployments.id'],
            name='fk_availability_assignments_deployment_scope', ondelete='CASCADE',
        ),
        ForeignKeyConstraint(
            ['tenant_id', 'project_id', 'resource_id'],
            ['managed_vms.tenant_id', 'managed_vms.project_id', 'managed_vms.id'],
            name='fk_availability_assignments_resource_scope', ondelete='CASCADE',
        ),
        UniqueConstraint('tenant_id', 'project_id', 'id', name='uq_availability_assignments_scoped_id'),
        UniqueConstraint('deployment_id', name='uq_availability_assignments_deployment'),
        UniqueConstraint('resource_id', name='uq_availability_assignments_resource'),
        Index('ix_availability_assignments_project_scope', 'tenant_id', 'project_id'),
        Index('ix_availability_assignments_plan', 'plan_id'),
        Index('ix_availability_assignments_status', 'status'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    plan_id: Mapped[str] = mapped_column(String(36))
    deployment_id: Mapped[str | None] = mapped_column(String(36))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    plan_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default='pending')
    job_id: Mapped[str | None] = mapped_column(ForeignKey('jobs.id', ondelete='SET NULL'), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
