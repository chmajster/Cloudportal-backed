from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

Name = Annotated[str, Field(min_length=1, max_length=100)]
Password = Annotated[str, Field(min_length=12, max_length=256)]
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
    secrets: dict[str, Annotated[str, Field(max_length=32768)]] | None = None

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

    @field_validator('ssh_public_key')
    @classmethod
    def ssh_key(cls, value):
        if value and (not value.startswith(('ssh-ed25519 ', 'ssh-rsa ', 'ecdsa-sha2-')) or '\n' in value):
            raise ValueError('Expected a single SSH public key')
        return value


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
    playbook: Literal['bootstrap-linux', 'validate-linux', 'validate-windows']
    credentials_id: int = Field(gt=0)
    inventory: Inventory | None = None
    variables: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def safe_variables(self):
        # No arbitrary Ansible variables (connection plugins/ansible_* allow code execution).
        if self.playbook == 'bootstrap-linux':
            if not set(self.variables) <= {'hostname', 'timezone'}:
                raise ValueError('Only hostname and timezone are permitted')
            import re
            for key, value in self.variables.items():
                pattern = r'[A-Za-z0-9][A-Za-z0-9.-]{0,62}' if key == 'hostname' else r'[A-Za-z_]+(?:/[A-Za-z_+-]+){0,2}'
                if not isinstance(value, str) or not re.fullmatch(pattern, value):
                    raise ValueError('Invalid playbook variable')
        elif self.variables:
            raise ValueError('This playbook does not accept variables')
        return self


class DeploymentInput(Input):
    name: Name
    provider_id: int = Field(gt=0)
    template: Literal['proxmox-vm'] = 'proxmox-vm'
    credentials_id: int = Field(gt=0)
    variables: VMVariables
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
