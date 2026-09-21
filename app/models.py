from datetime import datetime, timezone
import uuid
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def uid():
    return str(uuid.uuid4())


class Timestamp:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class User(Timestamp, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    auth_source: Mapped[str] = mapped_column(String(16), default="local", index=True)
    external_id: Mapped[str | None] = mapped_column(String(1024), unique=True)
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_service_account: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    roles: Mapped[list["Role"]] = relationship(secondary="user_roles", lazy="selectin")


class Role(Timestamp, Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    permissions: Mapped[list["Permission"]] = relationship(secondary="role_permissions", lazy="selectin")


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class Token(Base):
    __tablename__ = "tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[str] = mapped_column(String(16), default="api")
    family: Mapped[str] = mapped_column(String(36), default=uid, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    user: Mapped[User] = relationship(lazy="joined")


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)


class Credential(Timestamp, Base):
    __tablename__ = "credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[str] = mapped_column(String(2048), default="")
    username: Mapped[str] = mapped_column(String(254), default="")
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    rotation_due_at: Mapped[datetime | None] = mapped_column(DateTime)
    secret_updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class Provider(Timestamp, Base):
    __tablename__ = "providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    type: Mapped[str] = mapped_column(String(32))
    credentials_id: Mapped[int] = mapped_column(ForeignKey("credentials.id"))


class Deployment(Timestamp, Base):
    __tablename__ = "deployments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"))
    provider: Mapped[str] = mapped_column(String(32), default="proxmox")
    template: Mapped[str] = mapped_column(String(100))
    credentials_id: Mapped[int] = mapped_column(ForeignKey("credentials.id"))
    workspace: Mapped[str] = mapped_column(String(36), default=uid, unique=True)
    variables: Mapped[dict] = mapped_column(JSON)
    workflow: Mapped[dict] = mapped_column(JSON, default=dict)
    state_location: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime)
    active_job_id: Mapped[str | None] = mapped_column(String(36))
    executor: Mapped[str] = mapped_column(String(20), default="terraform")


class Job(Timestamp, Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployments.id"))
    operation: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_id: Mapped[int | None] = mapped_column(ForeignKey("tokens.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    ip: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(32), default="API")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    retry_of: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)


class JobLog(Base):
    __tablename__ = "job_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=now)
    message: Mapped[str] = mapped_column(Text)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    token_id: Mapped[int | None] = mapped_column(Integer)
    ip: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), default="API")
    action: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    result: Mapped[str] = mapped_column(String(32), default="success")
    request_id: Mapped[str] = mapped_column(String(36), index=True)


class Idempotency(Base):
    __tablename__ = "idempotency"
    __table_args__ = (UniqueConstraint("user_id", "path", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(255))
    key: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class HostnameScheme(Timestamp, Base):
    __tablename__ = "hostname_schemes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    pattern: Mapped[str] = mapped_column(String(255))
    next_number: Mapped[int] = mapped_column(Integer, default=1)
    padding: Mapped[int] = mapped_column(Integer, default=3)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class HostnameReservation(Timestamp, Base):
    __tablename__ = "hostname_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scheme_id: Mapped[int] = mapped_column(ForeignKey("hostname_schemes.id"), index=True)
    hostname: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="reserved", index=True)
    resource_id: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    released_at: Mapped[datetime | None] = mapped_column(DateTime)


class BlueprintManagerRole(Base):
    __tablename__ = "blueprint_manager_roles"
    blueprint_id: Mapped[int] = mapped_column(ForeignKey("blueprints.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True, unique=True)


class Blueprint(Timestamp, Base):
    __tablename__ = "blueprints"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    visibility: Mapped[dict] = mapped_column(JSON, default=dict)
    allowed_role_ids: Mapped[list] = mapped_column(JSON, default=list)
    allowed_user_ids: Mapped[list] = mapped_column(JSON, default=list)
    manager_roles: Mapped[list["Role"]] = relationship(secondary="blueprint_manager_roles", lazy="selectin")
    variables_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    deployment: Mapped[dict] = mapped_column(JSON, default=dict)
    workflow: Mapped[list] = mapped_column(JSON, default=list)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_policy: Mapped[str] = mapped_column(String(32), default="preserve")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))



class IPPool(Timestamp, Base):
    __tablename__ = "ip_pools"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    cidr: Mapped[str] = mapped_column(String(64), unique=True)
    gateway: Mapped[str | None] = mapped_column(String(45))
    dns_servers: Mapped[list] = mapped_column(JSON, default=list)
    excluded_addresses: Mapped[list] = mapped_column(JSON, default=list)
    next_offset: Mapped[int] = mapped_column(BigInteger, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class IPAllocation(Timestamp, Base):
    __tablename__ = "ip_allocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    pool_id: Mapped[int] = mapped_column(ForeignKey("ip_pools.id"), index=True)
    address: Mapped[str] = mapped_column(String(45), index=True)
    prefix_length: Mapped[int] = mapped_column(Integer)
    gateway: Mapped[str | None] = mapped_column(String(45))
    hostname: Mapped[str | None] = mapped_column(String(253))
    status: Mapped[str] = mapped_column(String(16), default="reserved", index=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    released_at: Mapped[datetime | None] = mapped_column(DateTime)



class ManagedVM(Timestamp, Base):
    __tablename__ = "managed_vms"
    __table_args__ = (UniqueConstraint("provider_id", "vm_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployments.id"), unique=True)
    node: Mapped[str] = mapped_column(String(63))
    vm_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100), default="")
    management_mode: Mapped[str] = mapped_column(String(16), default="external")
    lifecycle_status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime)



class TerraformState(Base):
    __tablename__ = "terraform_states"
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"), primary_key=True)
    encrypted_state: Mapped[bytes] = mapped_column(LargeBinary)
    state_sha256: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)



class TerraformPlan(Base):
    __tablename__ = "terraform_plans"
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"), primary_key=True)
    encrypted_plan: Mapped[bytes] = mapped_column(LargeBinary)
    plan_sha256: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class ManagedResource(Timestamp, Base):
    __tablename__ = "managed_resources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"), unique=True, index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    resource_type: Mapped[str] = mapped_column(String(32), default="vm", index=True)
    external_id: Mapped[str] = mapped_column(String(512))
    name: Mapped[str] = mapped_column(String(100))
    primary_ip: Mapped[str | None] = mapped_column(String(64))
    lifecycle_status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime)



class ScheduledOperation(Timestamp, Base):
    __tablename__ = "scheduled_operations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"), index=True)
    operation: Mapped[str] = mapped_column(String(32))
    next_run_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_id: Mapped[int | None] = mapped_column(ForeignKey("tokens.id"))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))


class EventRecord(Base):
    __tablename__ = "event_records"
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), default=uid, unique=True, index=True)
    type: Mapped[str] = mapped_column(String(128), index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(128), default="cloudportal.backend")
    subject_type: Mapped[str] = mapped_column(String(64), default="system", index=True)
    subject_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    causation_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    token_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class ExtensionState(Timestamp, Base):
    __tablename__ = "extension_states"
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(32))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="healthy", index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    last_event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    updated_by: Mapped[int | None] = mapped_column(Integer)


class ExtensionDelivery(Timestamp, Base):
    __tablename__ = "extension_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    extension_name: Mapped[str] = mapped_column(String(128), index=True)
    event_sequence: Mapped[int] = mapped_column(
        ForeignKey("event_records.sequence", ondelete="CASCADE"), index=True
    )
    materialization_key: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    is_replay: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))


class WebhookEndpoint(Timestamp, Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    url: Mapped[str] = mapped_column(String(2048))
    events: Mapped[list] = mapped_column(JSON, default=list)
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class WebhookDelivery(Timestamp, Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))
