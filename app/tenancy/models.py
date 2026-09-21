"""Domain-owned mappings; existing global identity models remain unchanged."""
from datetime import datetime

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, JSON, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Audit, Timestamp, uid


class Tenant(Timestamp, Base):
    __tablename__ = 'tenants'
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'disabled')", name='ck_tenants_status'),
        CheckConstraint('version >= 1', name='ck_tenants_version'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    description: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(16), default='active', index=True)
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column('metadata', JSON, default=dict)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {'version_id_col': version}


class TenantMembership(Timestamp, Base):
    __tablename__ = 'tenant_memberships'
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name='ck_tenant_memberships_status'),
        CheckConstraint('version >= 1', name='ck_tenant_memberships_version'),
    )
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default='active', index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {'version_id_col': version}


class TenantRoleAssignment(Timestamp, Base):
    __tablename__ = 'tenant_role_assignments'
    __table_args__ = (
        ForeignKeyConstraint(
            ['tenant_id', 'user_id'], ['tenant_memberships.tenant_id', 'tenant_memberships.user_id'],
            ondelete='CASCADE', name='fk_tenant_role_assignments_membership',
        ),
        UniqueConstraint('tenant_id', 'user_id', 'role_id', name='uq_tenant_role_assignments'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey('roles.id', ondelete='CASCADE'), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))


class TenantRoleGrant(Base):
    """Assignment-time permission ceiling, intersected with the live role.

    A relational ceiling avoids backend-specific JSON membership operators and
    permits scope filtering in SQL before pagination. Role revocations apply
    immediately; role additions require an explicitly authorized reassignment.
    """
    __tablename__ = 'tenant_role_grants'
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey('tenant_role_assignments.id', ondelete='CASCADE'), primary_key=True)
    permission_id: Mapped[int] = mapped_column(
        ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True, index=True)


# Tenant audit filtering uses the existing audit table, not a parallel log.
Index('ix_audit_tenant_scope', Audit.resource, Audit.resource_id, Audit.id)
