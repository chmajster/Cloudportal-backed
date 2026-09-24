"""Public API contract. Secret-bearing ORM objects are never response models."""
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict


class Output(BaseModel):
    model_config = ConfigDict(extra='forbid')


T = TypeVar('T')


class Items(Output, Generic[T]):
    items: list[T]


class UserOutput(Output):
    id: int
    username: str
    email: str
    first_name: str
    last_name: str
    is_active: bool
    is_locked: bool
    is_service_account: bool
    auth_source: Literal['local', 'ldap']
    must_change_password: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None
    failed_login_attempts: int
    locked_until: datetime | None


class VMClassificationSettingsOutput(Output):
    environments: dict[str, bool]
    apmids: list[str]
    hostname_defaults: dict[str, str]


class BlueprintExecutionSettingsOutput(Output):
    auto_approve_for_executors: bool
    approval_timeout_hours: int


class LDAPSettingsOutput(Output):
    enabled: bool
    url: str
    start_tls: bool
    verify_tls: bool
    bind_dn: str
    bind_password_configured: bool
    base_dn: str
    user_filter: str
    username_attribute: str
    email_attribute: str
    first_name_attribute: str
    last_name_attribute: str


class LDAPTestOutput(Output):
    ok: bool
    message: str


class RoleReference(Output):
    id: int
    name: str


class RoleOutput(RoleReference):
    permissions: list[str]


class IdentityOutput(Output):
    user: UserOutput
    roles: list[RoleReference]
    permissions: list[str]
    token_type: Literal['api', 'session']


class SessionOutput(Output):
    access_token: str
    refresh_token: str
    token_type: Literal['bearer']
    expires_in: int
    user: UserOutput
    roles: list[RoleReference]
    permissions: list[str]


class TokenOutput(Output):
    id: int
    name: str
    token_prefix: str
    user_id: int
    scopes: list[str]
    kind: Literal['api']
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class IssuedTokenOutput(TokenOutput):
    # Absent on idempotent replay: only the first response contains the secret.
    token: str | None = None


class IssuedResetOutput(Output):
    reset_token: str | None = None
    expires_in: int
    user_id: int


class ProviderOutput(Output):
    id: int
    name: str
    type: Literal['proxmox', 'vmware', 'aws', 'azure', 'openstack']
    credentials_id: int
    created_at: datetime
    updated_at: datetime


class JobOutput(Output):
    tenant_id: str
    project_id: str
    id: str
    deployment_id: str | None
    operation: Literal['terraform.plan', 'terraform.apply', 'terraform.destroy', 'terraform.import', 'ansible.execute']
    status: Literal['waiting_approval', 'queued', 'running', 'cancelling', 'successful', 'failed', 'cancelled']
    created_by: int
    request_id: str
    source: str
    created_at: datetime
    updated_at: datetime
    cancel_requested: bool
    error: str | None
    retry_of: str | None
    attempt: int
    current_stage: str | None = None
    provider_waiting: bool = False
    provider_retry_attempts: int = 0
    provider_next_retry_at: datetime | None = None


class DeploymentOutput(Output):
    tenant_id: str
    project_id: str
    id: str
    name: str
    provider_id: int
    provider: str
    template: str
    credentials_id: int
    workspace: str
    state_location: str
    variables: dict[str, Any]
    workflow: dict[str, Any]
    status: str
    created_by: int
    created_at: datetime
    updated_at: datetime
    destroyed_at: datetime | None
    active_job_id: str | None
    executor: Literal['terraform', 'opentofu']


class CreatedDeploymentOutput(DeploymentOutput):
    job: JobOutput


class JobLogOutput(Output):
    id: int
    timestamp: datetime
    message: str


class JobLogsOutput(Items[JobLogOutput]):
    request_id: str
    status: str
    next_after: int


class AuditOutput(Output):
    id: int
    timestamp: datetime
    user_id: int | None
    token_id: int | None
    ip: str
    source: str
    action: str
    resource: str
    resource_id: str | None
    result: str
    request_id: str


class TemplateOutput(Output):
    id: str
    name: str
    enabled: bool
    provider: str
    version: int
    importable: bool
    variables_schema: dict[str, Any]


class PlaybookOutput(Output):
    id: str
    name: str
    enabled: bool
    description: str | None = None
    category: str = 'Inne'
    version: int
    variables: list[str]
    required_variables: list[str]
    transport: Literal['ssh', 'winrm']
    custom: bool = False


class DeletedOutput(Output):
    deleted: bool


class SSHHostKeyOutput(Output):
    host: str
    port: int
    key_type: str
    fingerprint: str
    known_hosts: str


class CredentialTestOutput(Output):
    ok: bool
    provider: str
    version: str | None = None
    endpoint: str | None = None
    username: str | None = None
    auth_mode: str | None = None
    verify_ssl: bool | None = None
    latency_ms: int | None = None
    message: str | None = None


class WorkersOutput(Output):
    online: int
    expected: int


class HealthChecks(Output):
    api: bool
    database: bool
    queue: bool
    dispatcher: bool
    workers: WorkersOutput
    terraform: bool
    ansible: bool
    disk: bool
    encryption: bool


class HealthOutput(Output):
    status: Literal['ok', 'degraded']
    checks: HealthChecks


class InfoOutput(Output):
    name: str
    version: str
    api_version: str
    providers: list[str]
    credential_types: list[str]
    executors: list[str]
    openapi: str


class LogoutOutput(Output):
    logged_out: bool


class ResetOutput(Output):
    reset: bool


class PasswordChangedOutput(Output):
    changed: bool
    login_required: bool


class HostnameSchemeOutput(Output):
    id: int
    name: str
    pattern: str
    next_number: int
    padding: int
    is_active: bool
    created_by: int
    created_at: datetime
    updated_at: datetime


class HostnameReservationOutput(Output):
    id: str
    scheme_id: int
    hostname: str
    values: dict[str, Any]
    status: Literal['reserved', 'assigned', 'released']
    resource_id: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime
    released_at: datetime | None


class GeneratedHostnameOutput(Output):
    hostname: str
    reservation: HostnameReservationOutput | None


class BlueprintCreationScopeOutput(Output):
    tenant_id: str
    tenant_name: str
    tenant_slug: str
    project_id: str
    project_name: str
    project_slug: str


class BlueprintOutput(Output):
    id: int
    slug: str
    name: str
    description: str
    version: int
    is_active: bool
    visibility: dict[str, bool]
    allowed_role_ids: list[int]
    allowed_user_ids: list[int]
    manager_role_ids: list[int]
    variables_schema: dict[str, Any]
    deployment: dict[str, Any]
    workflow: list[dict[str, Any]]
    requires_approval: bool
    auto_approve_for_executors: bool | None
    approval_timeout_hours: int | None
    recovery_policy: Literal['preserve', 'destroy_on_failure']
    created_by: int
    created_at: datetime
    updated_at: datetime
