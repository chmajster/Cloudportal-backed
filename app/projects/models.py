"""Project mappings with composite foreign keys enforcing tenant consistency."""
from datetime import datetime
from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, JSON, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models import Audit, Timestamp, uid
from app.tenancy.models import Tenant, TenantMembership  # register FK targets


class Project(Timestamp, Base):
    __tablename__ = 'projects'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'slug', name='uq_projects_tenant_slug'),
        UniqueConstraint('tenant_id', 'id', name='uq_projects_tenant_id'),
        CheckConstraint("status IN ('active', 'suspended', 'disabled')", name='ck_projects_status'),
        CheckConstraint('version >= 1', name='ck_projects_version'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(63))
    description: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(16), default='active', index=True)
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column('metadata', JSON, default=dict)
    default_environment: Mapped[str] = mapped_column(String(63), default='dev')
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {'version_id_col': version}


class ProjectMembership(Timestamp, Base):
    __tablename__ = 'project_memberships'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                             ondelete='RESTRICT', name='fk_project_memberships_project'),
        ForeignKeyConstraint(['tenant_id', 'user_id'], ['tenant_memberships.tenant_id', 'tenant_memberships.user_id'],
                             ondelete='CASCADE', name='fk_project_memberships_tenant_member'),
        CheckConstraint("status IN ('active', 'disabled')", name='ck_project_memberships_status'),
        CheckConstraint('version >= 1', name='ck_project_memberships_version'),
    )
    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(16), default='active', index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {'version_id_col': version}


class ProjectRoleAssignment(Timestamp, Base):
    __tablename__ = 'project_role_assignments'
    __table_args__ = (
        ForeignKeyConstraint(['project_id', 'user_id'], ['project_memberships.project_id', 'project_memberships.user_id'],
                             ondelete='CASCADE', name='fk_project_role_assignments_membership'),
        UniqueConstraint('project_id', 'user_id', 'role_id', name='uq_project_role_assignments'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey('roles.id', ondelete='CASCADE'), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))


class ProjectRoleGrant(Base):
    __tablename__ = 'project_role_grants'
    assignment_id: Mapped[str] = mapped_column(ForeignKey('project_role_assignments.id', ondelete='CASCADE'), primary_key=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True, index=True)


class UserProjectContext(Base):
    """A server-side preference, not an authorization grant."""
    __tablename__ = 'user_project_contexts'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                             ondelete='RESTRICT', name='fk_user_project_contexts_project'),
        CheckConstraint('version >= 1', name='ck_user_project_contexts_version'),
        CheckConstraint('(tenant_id IS NULL) = (project_id IS NULL)', name='ck_user_project_contexts_pair'),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {'version_id_col': version}


Index('ix_audit_project_scope', Audit.resource, Audit.resource_id, Audit.id)
