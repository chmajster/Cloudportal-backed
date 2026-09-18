from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

Name = Annotated[str, Field(min_length=1, max_length=100)]
Password = Annotated[str, Field(min_length=12, max_length=256, json_schema_extra={'writeOnly': True})]
Slug = Annotated[str, Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$')]
CredentialType = Literal['proxmox', 'vmware', 'ssh', 'winrm', 'aws', 'azure', 'openstack', 'other']


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)


class Login(Input):
    username: Annotated[str, Field(min_length=1, max_length=254)]
    password: Annotated[str, Field(min_length=1, max_length=256)]


class Refresh(Input):
    refresh_token: Annotated[str, Field(min_length=32, max_length=256)]


class ResetPassword(Input):
    token: Annotated[str, Field(min_length=32, max_length=256)]
    password: Password


class ChangePassword(Input):
    current_password: Annotated[str, Field(min_length=1, max_length=256)]
    password: Password


class UserCreate(Input):
    username: Slug
    email: EmailStr
    password: Password
    first_name: Annotated[str, Field(max_length=100)] = ''
    last_name: Annotated[str, Field(max_length=100)] = ''
    is_service_account: bool = False

    @field_validator('username', 'email')
    @classmethod
    def lowercase(cls, value):
        return value.lower()


class UserUpdate(Input):
    email: EmailStr | None = None
    first_name: Annotated[str, Field(max_length=100)] | None = None
    last_name: Annotated[str, Field(max_length=100)] | None = None


class RoleInput(Input):
    name: Name
    permissions: Annotated[list[str], Field(max_length=100)]


class AssignRoles(Input):
    role_ids: Annotated[list[int], Field(max_length=100)]


class TokenInput(Input):
    name: Name
    user_id: int | None = None
    scopes: Annotated[list[str], Field(max_length=100)]
    expires_at: datetime | None = None


class CredentialInput(Input):
    name: Name
    type: CredentialType
    endpoint: Annotated[str, Field(max_length=2048)] = ''
    username: Annotated[str, Field(max_length=254)] = ''
    verify_ssl: bool = True
    secrets: dict[str, Annotated[str, Field(max_length=32768)]] | None = Field(default=None, json_schema_extra={'writeOnly': True})

    @field_validator('endpoint')
    @classmethod
    def endpoint_valid(cls, value):
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {'https', 'ssh'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Endpoint must be HTTPS (or ssh:// for SSH) without credentials, query or fragment')
        return value.rstrip('/')

    @model_validator(mode='after')
    def endpoint_scheme(self):
        if self.endpoint.startswith('ssh:') and self.type != 'ssh':
            raise ValueError('ssh:// endpoint is only valid for SSH credentials')
        return self

    @field_validator('secrets')
    @classmethod
    def keys_valid(cls, value):
        allowed = {'password', 'token_id', 'token_secret', 'private_key', 'known_hosts', 'access_key_id',
                   'secret_access_key', 'session_token', 'tenant_id', 'client_id', 'client_secret',
                   'subscription_id', 'project_name', 'domain_name', 'secret'}
        if value is not None and (not set(value) <= allowed or not value or any(not v or v == '********' for v in value.values())):
            raise ValueError('Provide real secret values; omit secrets to preserve the stored value')
        return value


class ProviderInput(Input):
    name: Name
    type: Literal['proxmox'] = 'proxmox'
    credentials_id: int = Field(gt=0)


class VMVariables(Input):
    name: Slug
    node: Slug
    template_id: int = Field(ge=100, le=999999999)
    template_node: Slug | None = None
    cpu: int = Field(default=2, ge=1, le=128)
    memory: int = Field(default=4096, ge=512, le=1048576)
    disk: int = Field(default=40, ge=1, le=65536)
    network: Slug = 'vmbr0'
    storage: Slug
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    ssh_username: Slug = 'clouduser'
    ssh_public_key: Annotated[str, Field(max_length=8192)] | None = None
    ipv4_address: Annotated[str, Field(max_length=32)] | None = None
    ipv4_gateway: Annotated[str, Field(max_length=15)] | None = None

    @field_validator('ssh_public_key')
    @classmethod
    def ssh_key(cls, value):
        if value and (not value.startswith(('ssh-ed25519 ', 'ssh-rsa ', 'ecdsa-sha2-')) or '\n' in value):
            raise ValueError('Expected a single SSH public key')
        return value

    @field_validator('ipv4_address')
    @classmethod
    def ipv4_interface(cls, value):
        if value is None:
            return value
        import ipaddress
        try:
            interface = ipaddress.ip_interface(value)
        except ValueError:
            raise ValueError('ipv4_address must use CIDR notation, for example 192.0.2.10/24') from None
        if interface.version != 4:
            raise ValueError('ipv4_address must be IPv4')
        return str(interface)

    @field_validator('ipv4_gateway')
    @classmethod
    def ipv4_gateway_valid(cls, value):
        if value is None:
            return value
        import ipaddress
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ValueError('ipv4_gateway must be an IP address') from None
        if address.version != 4:
            raise ValueError('ipv4_gateway must be IPv4')
        return str(address)

    @model_validator(mode='after')
    def static_ipv4_coherent(self):
        import ipaddress
        if self.ipv4_gateway and not self.ipv4_address:
            raise ValueError('ipv4_gateway requires ipv4_address')
        if self.ipv4_address and self.ipv4_gateway:
            interface = ipaddress.ip_interface(self.ipv4_address)
            gateway = ipaddress.ip_address(self.ipv4_gateway)
            if gateway not in interface.network or gateway == interface.ip:
                raise ValueError('ipv4_gateway must be in the same subnet as ipv4_address')
        return self


class Inventory(Input):
    hosts: Annotated[list[str], Field(min_length=1, max_length=100)]

    @field_validator('hosts')
    @classmethod
    def safe_hosts(cls, value):
        import ipaddress
        for host in value:
            ipaddress.ip_address(host)
        return value


class AnsibleInput(Input):
    playbook: Slug
    credentials_id: int = Field(gt=0)
    inventory: Inventory | None = None
    variables: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def safe_variables(self):
        # Catalog manifests are shipped with the root-owned release; API callers cannot add paths/code.
        from app.catalog import validate_playbook_variables
        from fastapi import HTTPException
        try:
            self.variables = validate_playbook_variables(self.playbook, self.variables)
        except HTTPException as error:
            raise ValueError(str(error.detail)) from None
        return self


class DeploymentInput(Input):
    name: Name
    provider_id: int = Field(gt=0)
    template: Slug = 'proxmox-vm'
    credentials_id: int = Field(gt=0)
    variables: dict[str, Any]
    executor: Literal['terraform', 'opentofu'] = 'terraform'
    ansible: AnsibleInput | None = None

    @model_validator(mode='after')
    def derived_inventory(self):
        if self.ansible and self.ansible.inventory:
            raise ValueError('Workflow inventory is discovered from the created VM')
        return self


class JobInput(Input):
    operation: Literal['terraform.plan', 'terraform.apply', 'terraform.destroy', 'ansible.execute']
    deployment_id: str | None = None
    ansible: AnsibleInput | None = None

    @model_validator(mode='after')
    def operation_input(self):
        if self.operation == 'ansible.execute':
            if not self.ansible or not self.ansible.inventory or self.deployment_id:
                raise ValueError('Ansible requires a controlled playbook, credential and inventory')
        elif not self.deployment_id or self.ansible:
            raise ValueError('Terraform requires a deployment ID')
        return self


HOSTNAME_TOKENS = {'location', 'environment', 'env', 'application', 'service', 'role', 'os', 'cluster', 'site', 'year', 'number', 'random'}


class HostnameSchemeInput(Input):
    name: Name
    pattern: Annotated[str, Field(min_length=3, max_length=255)]
    next_number: int = Field(default=1, ge=1, le=999999999)
    padding: int = Field(default=3, ge=1, le=9)
    is_active: bool = True

    @field_validator('pattern')
    @classmethod
    def safe_pattern(cls, value):
        import re
        tokens = re.findall(r'{([a-z]+)}', value)
        residue = re.sub(r'{[a-z]+}', '', value)
        if not tokens or not set(tokens) <= HOSTNAME_TOKENS or not re.fullmatch(r'[A-Za-z0-9.-]*', residue):
            raise ValueError('Pattern contains an unsupported token or character')
        if 'number' not in tokens and 'random' not in tokens:
            raise ValueError('Pattern must contain {number} or {random}')
        return value.lower()


class HostnameGenerateInput(Input):
    scheme_id: int = Field(gt=0)
    values: dict[str, Annotated[str, Field(min_length=1, max_length=63)]] = Field(default_factory=dict)
    reserve: bool = True

    @field_validator('values')
    @classmethod
    def safe_values(cls, value):
        import re
        if not set(value) <= HOSTNAME_TOKENS - {'number', 'random', 'year'}:
            raise ValueError('Unsupported hostname value')
        if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,62}', v) for v in value.values()):
            raise ValueError('Hostname values must contain letters, digits or hyphens')
        return {k: v.lower() for k, v in value.items()}


class BlueprintVariable(Input):
    type: Literal['string', 'integer', 'select', 'boolean']
    label: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    required: bool = False
    default: Any = None
    min: int | None = None
    max: int | None = None
    options: Annotated[list[str], Field(max_length=100)] = Field(default_factory=list)

    @model_validator(mode='after')
    def coherent(self):
        if self.type == 'select' and not self.options:
            raise ValueError('Select variable requires options')
        if self.type != 'select' and self.options:
            raise ValueError('Options are only valid for select variables')
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError('Variable min cannot exceed max')
        return self


class BlueprintVisibility(Input):
    backend: bool = True
    cloudportal: bool = False
    api: bool = True


class BlueprintStep(Input):
    id: Slug
    type: Literal['generate_hostname', 'allocate_ip', 'release_ip', 'create_vm', 'clone_vm', 'configure_vm', 'cloud_init', 'start_vm',
                  'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh', 'set_hostname',
                  'run_ansible_playbook', 'terraform_plan', 'terraform_apply', 'create_snapshot',
                  'health_check', 'condition', 'approval', 'delay', 'notification']
    depends_on: Annotated[list[Slug], Field(max_length=50)] = Field(default_factory=list)
    conditions: dict[str, Any] = Field(default_factory=dict)
    retry: int = Field(default=0, ge=0, le=10)
    timeout: int = Field(default=600, ge=1, le=86400)
    rollback: str | None = Field(default=None, max_length=63)


class BlueprintDeployment(Input):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    provider_id: int = Field(gt=0)
    credentials_id: int = Field(gt=0)
    template: Slug = 'proxmox-vm'
    variables: dict[str, Any]
    executor: Literal['terraform', 'opentofu'] = 'terraform'
    ansible: dict[str, Any] | None = None
    hostname_scheme_id: int | None = Field(default=None, gt=0)
    ipam_pool_id: int | None = Field(default=None, gt=0)


class BlueprintInput(Input):
    slug: Slug
    name: Name
    description: Annotated[str, Field(max_length=4000)] = ''
    is_active: bool = True
    visibility: BlueprintVisibility = Field(default_factory=BlueprintVisibility)
    allowed_role_ids: Annotated[list[int], Field(max_length=100)] = Field(default_factory=list)
    allowed_user_ids: Annotated[list[int], Field(max_length=100)] = Field(default_factory=list)
    variables_schema: Annotated[dict[Slug, BlueprintVariable], Field(max_length=100)] = Field(default_factory=dict)
    deployment: BlueprintDeployment
    workflow: Annotated[list[BlueprintStep], Field(min_length=1, max_length=100)]

    @model_validator(mode='after')
    def dag(self):
        reserved = {'hostname', 'ip_address', 'ip_address_cidr', 'ip_gateway', 'ip_prefix_length'}
        if set(self.variables_schema) & reserved:
            raise ValueError('Blueprint variables use names reserved for generated infrastructure values')
        ids = [step.id for step in self.workflow]
        if len(ids) != len(set(ids)):
            raise ValueError('Workflow step IDs must be unique')
        known = set(ids)
        if any(set(step.depends_on) - known or step.id in step.depends_on for step in self.workflow):
            raise ValueError('Workflow dependency is missing or self-referencing')
        graph = {step.id: step.depends_on for step in self.workflow}
        visiting, visited = set(), set()
        def visit(node):
            if node in visiting:
                raise ValueError('Workflow must be an acyclic graph')
            if node in visited:
                return
            visiting.add(node)
            for parent in graph[node]:
                visit(parent)
            visiting.remove(node)
            visited.add(node)
        for node in graph:
            visit(node)
        if not any(step.type in {'create_vm', 'clone_vm', 'terraform_apply'} for step in self.workflow):
            raise ValueError('Workflow must provision a VM')
        return self


class BlueprintExecuteInput(Input):
    variables: Annotated[dict[str, Any], Field(max_length=100)] = Field(default_factory=dict)
    hostname_values: dict[str, Annotated[str, Field(min_length=1, max_length=63)]] = Field(default_factory=dict)
