from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String,
    Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Timestamp, uid


SUBJECT_TYPES = ('USER', 'GROUP', 'SERVICE_ACCOUNT', 'API_TOKEN')
SCOPE_TYPES = (
    'GLOBAL', 'ORGANIZATION', 'PROJECT', 'APMID', 'ENVIRONMENT',
    'RESOURCE_POOL', 'BLUEPRINT', 'DEPLOYMENT', 'RESOURCE', 'MACHINE',
)
EFFECTS = ('ALLOW', 'DENY')


class RoleProfile(Timestamp, Base):
    __tablename__ = 'role_profiles'

    role_id: Mapped[int] = mapped_column(
        ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True
    )
    description: Mapped[str] = mapped_column(Text, default='')
    scope_types: Mapped[list] = mapped_column(JSON, default=lambda: list(SCOPE_TYPES))
    permission_patterns: Mapped[list] = mapped_column(JSON, default=list)
    inheritance_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    assignable_by: Mapped[list] = mapped_column(JSON, default=list)


class Group(Timestamp, Base):
    __tablename__ = 'iam_groups'
    __table_args__ = (
        UniqueConstraint('name', name='uq_iam_groups_name'),
        UniqueConstraint('external_source', 'external_id', name='uq_iam_groups_external'),
        UniqueConstraint('system_key', name='uq_iam_groups_system_key'),
        Index('ix_iam_groups_external_source', 'external_source', 'enabled'),
        Index('ix_iam_groups_managed_type', 'managed_type', 'enabled'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default='')
    external_source: Mapped[str | None] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(1024))
    system_key: Mapped[str | None] = mapped_column(String(255), index=True)
    managed_type: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))


class GroupMember(Timestamp, Base):
    __tablename__ = 'iam_group_members'
    __table_args__ = (
        UniqueConstraint('group_id', 'user_id', name='uq_iam_group_member'),
        Index('ix_iam_group_members_user', 'user_id', 'group_id'),
    )

    group_id: Mapped[str] = mapped_column(
        ForeignKey('iam_groups.id', ondelete='CASCADE'), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(32), default='LOCAL')
    external_id: Mapped[str | None] = mapped_column(String(1024))
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))


class RoleAssignment(Timestamp, Base):
    __tablename__ = 'iam_role_assignments'
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('USER','GROUP','SERVICE_ACCOUNT','API_TOKEN')",
            name='ck_iam_assignment_subject_type',
        ),
        CheckConstraint(
            "effect IN ('ALLOW','DENY')",
            name='ck_iam_assignment_effect',
        ),
        CheckConstraint(
            "scope_type IN ('GLOBAL','ORGANIZATION','PROJECT','APMID','ENVIRONMENT',"
            "'RESOURCE_POOL','BLUEPRINT','DEPLOYMENT','RESOURCE','MACHINE')",
            name='ck_iam_assignment_scope_type',
        ),
        Index(
            'ix_iam_assignment_subject_active',
            'subject_type', 'subject_id', 'enabled', 'valid_until',
        ),
        Index(
            'ix_iam_assignment_scope',
            'scope_type', 'scope_id', 'tenant_id', 'project_id',
        ),
        Index('ix_iam_assignment_role', 'role_id', 'enabled'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    subject_type: Mapped[str] = mapped_column(String(24))
    # String keeps the subject model extensible (integer users/tokens and UUID groups).
    subject_id: Mapped[str] = mapped_column(String(128))
    role_id: Mapped[int] = mapped_column(
        ForeignKey('roles.id', ondelete='RESTRICT'), index=True
    )
    effect: Mapped[str] = mapped_column(String(8), default='ALLOW')
    scope_type: Mapped[str] = mapped_column(String(32))
    scope_id: Mapped[str | None] = mapped_column(String(160))
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    apmid: Mapped[str | None] = mapped_column(String(63), index=True)
    environment: Mapped[str | None] = mapped_column(String(32), index=True)
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    permission_ceiling: Mapped[list | None] = mapped_column(JSON)
    inherit: Mapped[bool] = mapped_column(Boolean, default=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default='LOCAL', index=True)
    source_ref: Mapped[str | None] = mapped_column(String(1024))
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))


class JITAccessRequest(Timestamp, Base):
    __tablename__ = 'iam_jit_requests'
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','CANCELLED','EXPIRED')",
            name='ck_iam_jit_status',
        ),
        Index('ix_iam_jit_requester_status', 'requester_id', 'status'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    requester_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey('roles.id'), index=True)
    scope_type: Mapped[str] = mapped_column(String(32))
    scope_id: Mapped[str | None] = mapped_column(String(160))
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    apmid: Mapped[str | None] = mapped_column(String(63))
    environment: Mapped[str | None] = mapped_column(String(32))
    requested_duration_minutes: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default='PENDING', index=True)
    approval_id: Mapped[str | None] = mapped_column(String(64), index=True)
    assignment_id: Mapped[str | None] = mapped_column(
        ForeignKey('iam_role_assignments.id', ondelete='SET NULL')
    )
    decided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)


class BreakGlassAccess(Timestamp, Base):
    __tablename__ = 'iam_break_glass'
    __table_args__ = (
        Index('ix_iam_break_glass_user_active', 'user_id', 'enabled', 'valid_until'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    reason: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(DateTime)
    valid_until: Mapped[datetime] = mapped_column(DateTime, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    ended_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)


class RoleConflict(Timestamp, Base):
    __tablename__ = 'iam_role_conflicts'
    __table_args__ = (
        UniqueConstraint('role_a_id', 'role_b_id', 'scope_type', name='uq_iam_role_conflict'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    role_a_id: Mapped[int] = mapped_column(ForeignKey('roles.id'), index=True)
    role_b_id: Mapped[int] = mapped_column(ForeignKey('roles.id'), index=True)
    scope_type: Mapped[str] = mapped_column(String(32), default='GLOBAL')
    severity: Mapped[str] = mapped_column(String(16), default='BLOCK')
    description: Mapped[str] = mapped_column(Text, default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
