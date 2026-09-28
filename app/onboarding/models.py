from datetime import datetime

from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, JSON, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Timestamp, now, uid
from app.resource_scope.columns import ResourceScope, scope_constraints


class DiscoverySession(ResourceScope, Timestamp, Base):
    __tablename__ = 'onboarding_discovery_sessions'
    __table_args__ = scope_constraints(__tablename__)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='RESTRICT'), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('jobs.id', ondelete='SET NULL'), unique=True)
    status: Mapped[str] = mapped_column(String(24), default='QUEUED', index=True)
    scope_json: Mapped[dict] = mapped_column(JSON, default=dict)
    filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))


class DiscoveredResource(Timestamp, Base):
    __tablename__ = 'onboarding_discovered_resources'
    __table_args__ = (
        UniqueConstraint(
            'session_id', 'provider_id', 'cluster_id', 'resource_type', 'external_id',
            name='uq_onboarding_discovered_identity',
        ),
        Index('ix_onboarding_discovered_status', 'session_id', 'discovery_status'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey('onboarding_discovery_sessions.id', ondelete='CASCADE'), index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='RESTRICT'), index=True)
    cluster_id: Mapped[str] = mapped_column(String(255), default='')
    node_id: Mapped[str] = mapped_column(String(255), default='')
    resource_type: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    provider_uuid: Mapped[str | None] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(255), default='')
    hostname: Mapped[str | None] = mapped_column(String(255), index=True)
    power_state: Mapped[str] = mapped_column(String(32), default='unknown')
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    discovery_status: Mapped[str] = mapped_column(String(24), default='NEW', index=True)
    normalized_json: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    match_candidates_json: Mapped[list] = mapped_column(JSON, default=list)
    rule_trace_json: Mapped[list] = mapped_column(JSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class OnboardingRule(ResourceScope, Timestamp, Base):
    __tablename__ = 'onboarding_rules'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'project_id', 'name', name='uq_onboarding_rule_name_scope'),
        Index('ix_onboarding_rules_order', 'tenant_id', 'project_id', 'enabled', 'priority'),
        *scope_constraints(__tablename__),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_processing: Mapped[bool] = mapped_column(Boolean, default=False)
    conditions_json: Mapped[dict] = mapped_column(JSON, default=dict)
    effects_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))


class ResourceExternalIdentity(ResourceScope, Timestamp, Base):
    __tablename__ = 'resource_external_identities'
    __table_args__ = (
        UniqueConstraint(
            'provider_id', 'cluster_id', 'resource_type', 'external_id',
            name='uq_resource_external_identity',
        ),
        Index('ix_resource_external_identity_vm', 'managed_vm_id'),
        Index('ix_resource_external_identity_resource', 'managed_resource_id'),
        *scope_constraints(__tablename__),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='RESTRICT'), index=True)
    managed_vm_id: Mapped[str | None] = mapped_column(ForeignKey('managed_vms.id', ondelete='SET NULL'))
    managed_resource_id: Mapped[str | None] = mapped_column(ForeignKey('managed_resources.id', ondelete='SET NULL'))
    resource_type: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(255))
    cluster_id: Mapped[str] = mapped_column(String(255), default='')
    node_id: Mapped[str] = mapped_column(String(255), default='')
    provider_uuid: Mapped[str | None] = mapped_column(String(255), index=True)
    management_mode: Mapped[str] = mapped_column(String(24), default='INVENTORY_IMPORT')
    management_source: Mapped[str] = mapped_column(String(24), default='onboarded')
    provisioning_source: Mapped[str] = mapped_column(String(24), default='external')
    guest_credential_id: Mapped[int | None] = mapped_column(ForeignKey('credentials.id', ondelete='SET NULL'))
    awx_credential_id: Mapped[int | None] = mapped_column(ForeignKey('credentials.id', ondelete='SET NULL'))
    business_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    provider_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime)
    onboarded_by: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime)


class ResourceSyncState(Timestamp, Base):
    __tablename__ = 'resource_sync_states'

    identity_id: Mapped[str] = mapped_column(
        ForeignKey('resource_external_identities.id', ondelete='CASCADE'), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default='UNKNOWN', index=True)
    expected_json: Mapped[dict] = mapped_column(JSON, default=dict)
    actual_json: Mapped[dict] = mapped_column(JSON, default=dict)
    drift_json: Mapped[dict] = mapped_column(JSON, default=dict)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)


class OnboardingConflict(ResourceScope, Timestamp, Base):
    __tablename__ = 'onboarding_conflicts'
    __table_args__ = (
        Index('ix_onboarding_conflict_scope_status', 'tenant_id', 'project_id', 'status'),
        *scope_constraints(__tablename__),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    discovered_resource_id: Mapped[str | None] = mapped_column(
        ForeignKey('onboarding_discovered_resources.id', ondelete='SET NULL'), index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='RESTRICT'), index=True)
    conflict_type: Mapped[str] = mapped_column(String(32), default='POSSIBLE_MATCH')
    status: Mapped[str] = mapped_column(String(24), default='OPEN', index=True)
    candidates_json: Mapped[list] = mapped_column(JSON, default=list)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution: Mapped[str | None] = mapped_column(String(32))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))


class OnboardingJobItem(Timestamp, Base):
    __tablename__ = 'onboarding_job_items'
    __table_args__ = (
        UniqueConstraint('job_id', 'discovered_resource_id', name='uq_onboarding_job_resource'),
        Index('ix_onboarding_job_items_status', 'job_id', 'status'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id', ondelete='CASCADE'), index=True)
    discovered_resource_id: Mapped[str] = mapped_column(
        ForeignKey('onboarding_discovered_resources.id', ondelete='RESTRICT'), index=True)
    external_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey('resource_external_identities.id', ondelete='SET NULL'))
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='RESTRICT'))
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), default='')
    status: Mapped[str] = mapped_column(String(24), default='QUEUED', index=True)
    stage: Mapped[str] = mapped_column(String(64), default='queued')
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)


class OnboardingSchedule(ResourceScope, Timestamp, Base):
    __tablename__ = 'onboarding_schedules'
    __table_args__ = (
        Index('ix_onboarding_schedules_due', 'is_active', 'next_run_at'),
        *scope_constraints(__tablename__),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='RESTRICT'), index=True)
    discovery_scope_json: Mapped[dict] = mapped_column(JSON, default=dict)
    filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    auto_classification: Mapped[bool] = mapped_column(Boolean, default=True)
    automatic_inventory_import: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
