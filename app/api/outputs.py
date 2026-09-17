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
    must_change_password: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None
    failed_login_attempts: int
    locked_until: datetime | None


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


class CredentialOutput(Output):
    id: int
    name: str
    type: str
    endpoint: str
    username: str
    verify_ssl: bool
    created_at: datetime
    updated_at: datetime
    configured: bool
    secret: Literal['********']


class ProviderOutput(Output):
    id: int
    name: str
    type: Literal['proxmox']
    credentials_id: int
    created_at: datetime
    updated_at: datetime


class JobOutput(Output):
    id: str
    deployment_id: str | None
    operation: Literal['terraform.plan', 'terraform.apply', 'terraform.destroy', 'ansible.execute']
    status: Literal['queued', 'running', 'successful', 'failed', 'cancelled']
    created_by: int
    request_id: str
    created_at: datetime
    updated_at: datetime
    cancel_requested: bool
    error: str | None


class DeploymentOutput(Output):
    id: str
    name: str
    provider_id: int
    provider: str
    template: str
    credentials_id: int
    workspace: str
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
    action: str
    resource: str
    resource_id: str | None
    result: str
    request_id: str


class TemplateOutput(Output):
    id: str
    name: str
    provider: str
    variables_schema: dict[str, Any]


class PlaybookOutput(Output):
    id: str
    name: str
    variables: list[str]
    transport: Literal['ssh', 'winrm']


class DeletedOutput(Output):
    deleted: bool


class CredentialTestOutput(Output):
    ok: bool
    provider: str
    version: str | None = None


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
