"""Hierarchical tenant/project quota accounting with durable reservations."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Timestamp, now, uid
from app.resource_scope.columns import ResourceScope


class TenantQuotaLimit(Timestamp, Base):
    __tablename__ = 'tenant_quota_limits'
    __table_args__ = (UniqueConstraint('tenant_id', 'dimension', name='uq_tenant_quota_limit_dimension'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    dimension: Mapped[str] = mapped_column(String(32), index=True)
    limit_value: Mapped[int] = mapped_column(BigInteger)


class ProjectQuotaLimit(ResourceScope, Timestamp, Base):
    __tablename__ = 'project_quota_limits'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                             name='fk_project_quota_limit_scope', ondelete='CASCADE'),
        UniqueConstraint('tenant_id', 'project_id', 'dimension', name='uq_project_quota_limit_dimension'),
        Index('ix_project_quota_limit_scope', 'tenant_id', 'project_id'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    dimension: Mapped[str] = mapped_column(String(32), index=True)
    limit_value: Mapped[int] = mapped_column(BigInteger)


class TenantQuotaUsage(Timestamp, Base):
    __tablename__ = 'tenant_quota_usage'
    __table_args__ = (UniqueConstraint('tenant_id', 'dimension', name='uq_tenant_quota_usage_dimension'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    dimension: Mapped[str] = mapped_column(String(32), index=True)
    used: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)


class ProjectQuotaUsage(ResourceScope, Timestamp, Base):
    __tablename__ = 'project_quota_usage'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                             name='fk_project_quota_usage_scope', ondelete='CASCADE'),
        UniqueConstraint('tenant_id', 'project_id', 'dimension', name='uq_project_quota_usage_dimension'),
        Index('ix_project_quota_usage_scope', 'tenant_id', 'project_id'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    dimension: Mapped[str] = mapped_column(String(32), index=True)
    used: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)


class QuotaReservation(ResourceScope, Timestamp, Base):
    __tablename__ = 'quota_reservations'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                             name='fk_quota_reservation_scope', ondelete='RESTRICT'),
        UniqueConstraint('request_key', name='uq_quota_reservation_request_key'),
        Index('ix_quota_reservation_scope_status', 'tenant_id', 'project_id', 'status'),
        Index('ix_quota_reservation_subject', 'tenant_id', 'project_id', 'subject_type', 'subject_id'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    request_key: Mapped[str] = mapped_column(String(128), index=True)
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(64))
    deltas: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default='reserved', index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    reconciliation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime)
    released_at: Mapped[datetime | None] = mapped_column(DateTime)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime)


class QuotaAllocation(ResourceScope, Timestamp, Base):
    __tablename__ = 'quota_allocations'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                             name='fk_quota_allocation_scope', ondelete='RESTRICT'),
        UniqueConstraint('tenant_id', 'project_id', 'subject_type', 'subject_id',
                         name='uq_quota_allocation_subject'),
        Index('ix_quota_allocation_scope', 'tenant_id', 'project_id'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(128))
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)


class QuotaLedgerEntry(ResourceScope, Base):
    __tablename__ = 'quota_ledger_entries'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                             name='fk_quota_ledger_scope', ondelete='RESTRICT'),
        Index('ix_quota_ledger_scope_created', 'tenant_id', 'project_id', 'created_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    reservation_id: Mapped[str | None] = mapped_column(ForeignKey('quota_reservations.id', ondelete='SET NULL'), index=True)
    event: Mapped[str] = mapped_column(String(32), index=True)
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(128))
    deltas: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
