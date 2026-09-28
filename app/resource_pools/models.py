"""Durable resource-pool, placement, mapping and reservation models."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Integer,
    JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Timestamp, now, uid
from app.resource_scope.columns import ResourceScope


class ResourcePool(ResourceScope, Timestamp, Base):
    __tablename__ = 'resource_pools'
    __table_args__ = (
        ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_resource_pools_project_scope', ondelete='RESTRICT',
        ),
        UniqueConstraint('tenant_id', 'project_id', 'normalized_name', name='uq_resource_pools_scope_name'),
        Index('ix_resource_pools_scope', 'tenant_id', 'project_id'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default='')
    strategy: Mapped[str] = mapped_column(String(32), default='BALANCED')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    thresholds: Mapped[dict] = mapped_column(JSON, default=dict)
    retry_limit: Mapped[int] = mapped_column(Integer, default=3)
    reservation_ttl_seconds: Mapped[int] = mapped_column(Integer, default=600)
    round_robin_cursor: Mapped[int] = mapped_column(Integer, default=0)
    placement_version: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class ResourcePoolMember(Timestamp, Base):
    __tablename__ = 'resource_pool_members'
    __table_args__ = (
        Index('ix_resource_pool_members_pool', 'pool_id'),
        Index('ix_resource_pool_members_provider', 'provider_id'),
        Index('ix_resource_pool_members_enabled', 'pool_id', 'enabled', 'maintenance_mode'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    pool_id: Mapped[str] = mapped_column(ForeignKey('resource_pools.id', ondelete='CASCADE'))
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='RESTRICT'))
    platform_id: Mapped[str | None] = mapped_column(String(100))
    provider_type: Mapped[str] = mapped_column(String(32))
    cluster: Mapped[str | None] = mapped_column(String(100))
    node: Mapped[str | None] = mapped_column(String(100))
    datacenter: Mapped[str | None] = mapped_column(String(100))
    storage: Mapped[str | None] = mapped_column(String(100))
    network: Mapped[str | None] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    weight: Mapped[int] = mapped_column(Integer, default=100)
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    max_vm_count: Mapped[int | None] = mapped_column(Integer)
    max_cpu_usage: Mapped[float | None] = mapped_column(Float)
    max_memory_usage: Mapped[float | None] = mapped_column(Float)
    min_free_memory_mb: Mapped[int | None] = mapped_column(Integer)
    min_free_storage_gb: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class PlacementRule(Timestamp, Base):
    __tablename__ = 'placement_rules'
    __table_args__ = (
        UniqueConstraint('pool_id', 'normalized_name', name='uq_placement_rules_pool_name'),
        Index('ix_placement_rules_pool_priority', 'pool_id', 'enabled', 'priority'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    pool_id: Mapped[str] = mapped_column(ForeignKey('resource_pools.id', ondelete='CASCADE'))
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    actions: Mapped[dict] = mapped_column(JSON, default=dict)
    stop_processing: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class LogicalNetwork(ResourceScope, Timestamp, Base):
    __tablename__ = 'logical_networks'
    __table_args__ = (
        ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_logical_networks_project_scope', ondelete='RESTRICT',
        ),
        UniqueConstraint('tenant_id', 'project_id', 'normalized_name', name='uq_logical_networks_scope_name'),
        Index('ix_logical_networks_scope', 'tenant_id', 'project_id'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class NetworkMapping(Timestamp, Base):
    __tablename__ = 'network_mappings'
    __table_args__ = (
        UniqueConstraint('logical_network_id', 'provider_id', 'node', name='uq_network_mapping_target'),
        Index('ix_network_mappings_logical', 'logical_network_id'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    logical_network_id: Mapped[str] = mapped_column(ForeignKey('logical_networks.id', ondelete='CASCADE'))
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='CASCADE'))
    node: Mapped[str | None] = mapped_column(String(100))
    network: Mapped[str] = mapped_column(String(100))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class StorageClass(ResourceScope, Timestamp, Base):
    __tablename__ = 'storage_classes'
    __table_args__ = (
        ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_storage_classes_project_scope', ondelete='RESTRICT',
        ),
        UniqueConstraint('tenant_id', 'project_id', 'normalized_name', name='uq_storage_classes_scope_name'),
        Index('ix_storage_classes_scope', 'tenant_id', 'project_id'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class StorageMapping(Timestamp, Base):
    __tablename__ = 'storage_mappings'
    __table_args__ = (
        UniqueConstraint('storage_class_id', 'provider_id', 'node', name='uq_storage_mapping_target'),
        Index('ix_storage_mappings_class', 'storage_class_id'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    storage_class_id: Mapped[str] = mapped_column(ForeignKey('storage_classes.id', ondelete='CASCADE'))
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='CASCADE'))
    node: Mapped[str | None] = mapped_column(String(100))
    storage: Mapped[str] = mapped_column(String(100))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class PlacementDecision(ResourceScope, Timestamp, Base):
    __tablename__ = 'placement_decisions'
    __table_args__ = (
        ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_placement_decisions_project_scope', ondelete='RESTRICT',
        ),
        Index('ix_placement_decisions_scope', 'tenant_id', 'project_id'),
        Index('ix_placement_decisions_deployment', 'deployment_id'),
        Index('ix_placement_decisions_pool', 'pool_id'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey('deployments.id', ondelete='SET NULL'))
    vm_id: Mapped[str | None] = mapped_column(String(100))
    blueprint_id: Mapped[int | None] = mapped_column(ForeignKey('blueprints.id', ondelete='SET NULL'))
    pool_id: Mapped[str | None] = mapped_column(ForeignKey('resource_pools.id', ondelete='SET NULL'))
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)
    placement_mode: Mapped[str] = mapped_column(String(16), default='FIXED')
    selected_provider: Mapped[str] = mapped_column(String(32))
    selected_provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='RESTRICT'))
    selected_platform: Mapped[str] = mapped_column(String(100))
    selected_member_id: Mapped[str | None] = mapped_column(ForeignKey('resource_pool_members.id', ondelete='SET NULL'))
    selected_node: Mapped[str | None] = mapped_column(String(100))
    selected_storage: Mapped[str | None] = mapped_column(String(100))
    selected_network: Mapped[str | None] = mapped_column(String(100))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    candidates_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    decision_reason: Mapped[str] = mapped_column(Text, default='')
    request_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    is_override: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))


class ResourceReservation(Timestamp, Base):
    __tablename__ = 'resource_reservations'
    __table_args__ = (
        Index('ix_resource_reservations_pool_status', 'pool_id', 'status', 'expires_at'),
        Index('ix_resource_reservations_target', 'provider_id', 'node', 'storage', 'status'),
        Index('ix_resource_reservations_deployment', 'deployment_id'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    pool_id: Mapped[str] = mapped_column(ForeignKey('resource_pools.id', ondelete='CASCADE'))
    member_id: Mapped[str] = mapped_column(ForeignKey('resource_pool_members.id', ondelete='CASCADE'))
    decision_id: Mapped[str | None] = mapped_column(ForeignKey('placement_decisions.id', ondelete='SET NULL'))
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey('deployments.id', ondelete='SET NULL'))
    job_id: Mapped[str | None] = mapped_column(ForeignKey('jobs.id', ondelete='SET NULL'))
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='RESTRICT'))
    node: Mapped[str | None] = mapped_column(String(100))
    storage: Mapped[str | None] = mapped_column(String(100))
    cpu: Mapped[int] = mapped_column(Integer, default=0)
    memory_mb: Mapped[int] = mapped_column(Integer, default=0)
    storage_gb: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='RESERVED', index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))

    def expired(self, at: datetime | None = None) -> bool:
        return self.status == 'RESERVED' and self.expires_at <= (at or now())
