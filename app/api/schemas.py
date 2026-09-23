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


class CatalogItemStateInput(Input):
    enabled: bool


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


class VMClassificationSettingsInput(Input):
    environments: dict[str, bool] = Field(default_factory=lambda: {
        'test': True,
        'dev': True,
        'nonprod': True,
        'prod': True,
    })
    apmids: Annotated[list[str], Field(max_length=200)] = Field(default_factory=list)
    hostname_defaults: dict[str, str] | None = None

    @field_validator('environments')
    @classmethod
    def valid_environments(cls, value):
        expected = {'test', 'dev', 'nonprod', 'prod'}
        if set(value) != expected:
            raise ValueError('Environments must contain exactly test, dev, nonprod and prod')
        return {name: bool(value[name]) for name in ('test', 'dev', 'nonprod', 'prod')}

    @field_validator('apmids')
    @classmethod
    def valid_apmids(cls, value):
        import re
        result = []
        for item in value:
            normalized = str(item).strip().upper()
            if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,62}', normalized):
                raise ValueError('APMID must use letters, digits, underscore or hyphen')
            if normalized not in result:
                result.append(normalized)
        return result


    @field_validator('hostname_defaults')
    @classmethod
    def valid_hostname_defaults(cls, value):
        if value is None:
            return None
        import re
        if set(value) != {'location', 'role'}:
            raise ValueError('Hostname defaults must contain exactly location and role')
        result = {}
        for name in ('location', 'role'):
            normalized = str(value[name]).strip().lower()
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', normalized):
                raise ValueError(f'Hostname default {name} must be a valid DNS label')
            result[name] = normalized
        return result


class BlueprintExecutionSettingsInput(Input):
    auto_approve_for_executors: bool = True
    approval_timeout_hours: int = Field(default=48, ge=1, le=720)


class LDAPSettingsInput(Input):
    enabled: bool = False
    url: Annotated[str, Field(min_length=8, max_length=2048)] = 'ldap://localhost:389'
    start_tls: bool = False
    verify_tls: bool = True
    bind_dn: Annotated[str, Field(max_length=1024)] = ''
    bind_password: Annotated[str | None, Field(max_length=4096, json_schema_extra={'writeOnly': True})] = None
    base_dn: Annotated[str, Field(max_length=1024)] = ''
    user_filter: Annotated[str, Field(min_length=3, max_length=1024)] = '(&(objectClass=person)(uid={username}))'
    username_attribute: Annotated[str, Field(min_length=1, max_length=64)] = 'uid'
    email_attribute: Annotated[str, Field(min_length=1, max_length=64)] = 'mail'
    first_name_attribute: Annotated[str, Field(min_length=1, max_length=64)] = 'givenName'
    last_name_attribute: Annotated[str, Field(min_length=1, max_length=64)] = 'sn'

    @field_validator('url')
    @classmethod
    def ldap_url(cls, value):
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {'ldap', 'ldaps'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('LDAP URL must use ldap:// or ldaps:// without credentials, query or fragment')
        if parsed.path not in {'', '/'}:
            raise ValueError('LDAP URL must contain only host and optional port')
        return value.strip().rstrip('/')

    @field_validator('user_filter')
    @classmethod
    def ldap_filter(cls, value):
        if value.count('{username}') != 1:
            raise ValueError('LDAP user filter must contain exactly one {username} placeholder')
        return value

    @field_validator('username_attribute', 'email_attribute', 'first_name_attribute', 'last_name_attribute')
    @classmethod
    def ldap_attribute(cls, value):
        import re
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9;-]{0,63}', value):
            raise ValueError('Invalid LDAP attribute name')
        return value

    @model_validator(mode='after')
    def ldap_transport(self):
        if self.start_tls and self.url.startswith('ldaps://'):
            raise ValueError('StartTLS cannot be combined with LDAPS')
        if self.enabled and not self.base_dn:
            raise ValueError('Base DN is required when LDAP is enabled')
        if self.bind_password and not self.bind_dn:
            raise ValueError('Bind password requires Bind DN')
        return self


class UserCreate(Input):
    username: Slug
    email: EmailStr
    password: Password | None = None
    first_name: Annotated[str, Field(max_length=100)] = ''
    last_name: Annotated[str, Field(max_length=100)] = ''
    is_service_account: bool = False

    @field_validator('password', mode='before')
    @classmethod
    def empty_password(cls, value):
        return None if value == '' else value

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
    expires_at: datetime | None = None
    rotation_due_at: datetime | None = None
    secrets: dict[str, Annotated[str, Field(max_length=32768)]] | None = Field(default=None, json_schema_extra={'writeOnly': True})

    @field_validator('expires_at', 'rotation_due_at')
    @classmethod
    def credential_dates_utc(cls, value):
        if value is None:
            return value
        from datetime import timezone
        candidate = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return candidate.astimezone(timezone.utc).replace(tzinfo=None)

    @field_validator('endpoint')
    @classmethod
    def endpoint_valid(cls, value):
        value = value.strip().rstrip('/')
        if not value:
            return value
        if '://' not in value:
            if any(ch in value for ch in '/?#@') or value.startswith(':'):
                raise ValueError('Endpoint must be a host/IP or URL without credentials, query or fragment')
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https', 'ssh'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Endpoint must be HTTP/HTTPS (or ssh:// for SSH) without credentials, query or fragment')
        return value

    @model_validator(mode='after')
    def endpoint_scheme(self):
        if not self.endpoint:
            return self
        if self.type == 'proxmox':
            if '://' in self.endpoint:
                parsed = urlsplit(self.endpoint)
                if parsed.scheme not in {'http', 'https'} or parsed.path:
                    raise ValueError('Proxmox endpoint must use HTTP or HTTPS and contain only host/IP and optional port')
            return self
        if self.type == 'ssh':
            if not self.endpoint.startswith('ssh://'):
                raise ValueError('SSH credential endpoint must use ssh://')
            return self
        parsed = urlsplit(self.endpoint)
        if parsed.scheme != 'https' or not parsed.hostname:
            raise ValueError('This credential type requires an HTTPS endpoint')
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


class SSHHostKeyInput(Input):
    endpoint: Annotated[str, Field(min_length=7, max_length=2048)]

    @field_validator('endpoint')
    @classmethod
    def ssh_endpoint(cls, value):
        parsed = urlsplit(value.strip())
        if parsed.scheme != 'ssh' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Endpoint SSH must use ssh://host:port without credentials')
        if parsed.path not in {'', '/'}:
            raise ValueError('SSH endpoint must not contain a path')
        return value.strip().rstrip('/')


class SSHKeyBootstrapInput(Input):
    name: Name
    endpoint: Annotated[str, Field(min_length=7, max_length=2048)]
    username: Annotated[str, Field(min_length=1, max_length=254)]
    password: Annotated[str, Field(min_length=1, max_length=1024, json_schema_extra={'writeOnly': True})]
    known_hosts: Annotated[str, Field(min_length=1, max_length=32768, json_schema_extra={'writeOnly': True})]
    expires_at: datetime | None = None
    rotation_due_at: datetime | None = None

    @field_validator('endpoint')
    @classmethod
    def ssh_endpoint(cls, value):
        parsed = urlsplit(value.strip())
        if parsed.scheme != 'ssh' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Endpoint SSH must use ssh://host:port without credentials')
        if parsed.path not in {'', '/'}:
            raise ValueError('SSH endpoint must not contain a path')
        return value.strip().rstrip('/')


class ProxmoxTokenBootstrapInput(Input):
    name: Name
    endpoint: Annotated[str, Field(max_length=2048)]
    username: Annotated[str, Field(min_length=1, max_length=254)]
    password: Annotated[str, Field(min_length=1, max_length=1024, json_schema_extra={'writeOnly': True})]
    token_name: Annotated[str, Field(min_length=1, max_length=63)] = 'cloudportal'
    verify_ssl: bool = True
    privilege_separation: bool = False
    expires_at: datetime | None = None
    rotation_due_at: datetime | None = None

    @field_validator('token_name')
    @classmethod
    def token_name_valid(cls, value):
        if any(not (ch.isalnum() or ch in '_.-') for ch in value):
            raise ValueError('Token name may contain only letters, digits, dot, underscore and dash')
        return value

    @field_validator('endpoint')
    @classmethod
    def proxmox_endpoint_valid(cls, value):
        value = value.strip().rstrip('/')
        if not value:
            raise ValueError('Proxmox endpoint is required')
        if '://' not in value:
            if any(ch in value for ch in '/?#@') or value.startswith(':'):
                raise ValueError('Proxmox endpoint must be a host/IP or HTTP/HTTPS URL without credentials, query or fragment')
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError('Proxmox endpoint must use HTTP or HTTPS and contain only host/IP and optional port')
        return value

    @field_validator('expires_at', 'rotation_due_at')
    @classmethod
    def lifecycle_dates_utc(cls, value):
        if value is None:
            return value
        from datetime import timezone
        candidate = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return candidate.astimezone(timezone.utc).replace(tzinfo=None)


class ProviderInput(Input):
    name: Name
    type: Literal['proxmox', 'vmware', 'aws', 'azure', 'openstack'] = 'proxmox'
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
    install_qemu_guest_agent: bool = False
    cloud_init_snippet_storage: Slug | None = None
    ipv4_address: Annotated[str, Field(max_length=32)] | None = None
    ipv4_gateway: Annotated[str, Field(max_length=15)] | None = None
    dns_servers: Annotated[list[str], Field(max_length=8)] = Field(default_factory=list)
    dns_domain: Annotated[str | None, Field(max_length=253)] = None
    tags: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)

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

    @field_validator('dns_servers')
    @classmethod
    def cloud_init_dns_servers(cls, values):
        import ipaddress
        result = []
        for value in values:
            try:
                result.append(str(ipaddress.ip_address(value)))
            except ValueError:
                raise ValueError('dns_servers must contain valid IP addresses') from None
        return result

    @field_validator('dns_domain')
    @classmethod
    def cloud_init_dns_domain(cls, value):
        if value is None or value == '':
            return None
        import re
        value = value.lower().rstrip('.')
        if len(value) > 253 or any(
            not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
            for label in value.split('.')
        ):
            raise ValueError('dns_domain must be a valid DNS domain')
        return value

    @field_validator('tags')
    @classmethod
    def proxmox_tags(cls, values):
        import re
        normalized = []
        for value in values:
            tag = value.strip().lower()
            if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}', tag):
                raise ValueError('Proxmox tags may contain lowercase letters, digits, dot, underscore and hyphen')
            normalized.append(tag)
        return sorted(set(normalized))

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


class AWSVariables(Input):
    name: Slug
    region: Annotated[str, Field(min_length=9, max_length=32)]
    ami: Annotated[str, Field(min_length=12, max_length=40)]
    instance_type: Slug = 't3.micro'
    subnet_id: Annotated[str, Field(min_length=15, max_length=48)]
    security_group_ids: Annotated[list[str], Field(min_length=1, max_length=20)]
    key_name: Name | None = None
    root_volume_size: int = Field(default=20, ge=8, le=16384)
    associate_public_ip: bool = False

    @field_validator('region')
    @classmethod
    def aws_region(cls, value):
        import re
        if not re.fullmatch(r'[a-z]{2}(?:-gov)?-[a-z]+-[0-9]', value):
            raise ValueError('Invalid AWS region')
        return value

    @field_validator('ami')
    @classmethod
    def aws_ami(cls, value):
        import re
        if not re.fullmatch(r'ami-[0-9a-fA-F]{8,32}', value):
            raise ValueError('Invalid AWS AMI ID')
        return value

    @field_validator('subnet_id')
    @classmethod
    def aws_subnet(cls, value):
        import re
        if not re.fullmatch(r'subnet-[0-9a-fA-F]{8,32}', value):
            raise ValueError('Invalid AWS subnet ID')
        return value

    @field_validator('security_group_ids')
    @classmethod
    def security_groups(cls, values):
        import re
        if any(not re.fullmatch(r'sg-[0-9a-fA-F]{8,32}', value) for value in values):
            raise ValueError('Invalid AWS security group ID')
        return values


class AzureVariables(Input):
    name: Slug
    location: Name
    resource_group: Name
    subnet_id: Annotated[str, Field(min_length=10, max_length=2048)]
    vm_size: Slug = 'Standard_B2s'
    admin_username: Slug = 'clouduser'
    ssh_public_key: Annotated[str, Field(min_length=32, max_length=8192)]
    image_publisher: Slug = 'Canonical'
    image_offer: Slug = 'ubuntu-24_04-lts'
    image_sku: Slug = 'server'
    image_version: Slug = 'latest'
    os_disk_size_gb: int = Field(default=30, ge=30, le=32768)

    @field_validator('subnet_id')
    @classmethod
    def azure_subnet(cls, value):
        if not value.startswith('/subscriptions/') or '/subnets/' not in value or any(ch.isspace() for ch in value):
            raise ValueError('Invalid Azure subnet resource ID')
        return value

    @field_validator('ssh_public_key')
    @classmethod
    def azure_ssh_key(cls, value):
        if not value.startswith(('ssh-ed25519 ', 'ssh-rsa ', 'ecdsa-sha2-')) or '\n' in value:
            raise ValueError('Expected a single SSH public key')
        return value


class OpenStackVariables(Input):
    name: Slug
    region: Annotated[str, Field(min_length=1, max_length=100)] = 'RegionOne'
    image_name: Name
    flavor_name: Name
    network_name: Name
    key_pair: Name | None = None
    security_groups: Annotated[list[Name], Field(max_length=20)] = Field(default_factory=list)


class VMwareVariables(Input):
    name: Slug
    datacenter: Name
    datastore: Name
    cluster: Name
    network: Name
    template: Name
    folder: Annotated[str | None, Field(max_length=255)] = None
    cpu: int = Field(default=2, ge=1, le=128)
    memory: int = Field(default=4096, ge=512, le=1048576)
    disk: int = Field(default=40, ge=1, le=65536)


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
    operation: Literal['terraform.plan', 'terraform.apply', 'terraform.destroy', 'terraform.import', 'ansible.execute']
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
                  'run_ansible_playbook', 'terraform_plan', 'terraform_apply', 'terraform_destroy', 'create_snapshot',
                  'set_tags', 'health_check', 'condition', 'approval', 'delay', 'notification']
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
    hostname_values: dict[Slug, Annotated[str, Field(min_length=1, max_length=253)]] = Field(default_factory=dict)
    guest_credential_id: int | None = Field(default=None, gt=0)
    apmid: Annotated[str | None, Field(max_length=63, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$')] = None
    environment: Literal['test', 'dev', 'nonprod', 'prod'] | None = None
    select_apmid_on_execute: bool = False
    select_environment_on_execute: bool = False


class BlueprintInput(Input):
    slug: Slug
    name: Name
    description: Annotated[str, Field(max_length=4000)] = ''
    is_active: bool = True
    visibility: BlueprintVisibility = Field(default_factory=BlueprintVisibility)
    allowed_role_ids: Annotated[list[int], Field(max_length=100)] = Field(default_factory=list)
    allowed_user_ids: Annotated[list[int], Field(max_length=100)] = Field(default_factory=list)
    manager_role_ids: Annotated[list[int], Field(max_length=100)] = Field(default_factory=list)
    variables_schema: Annotated[dict[Slug, BlueprintVariable], Field(max_length=100)] = Field(default_factory=dict)
    deployment: BlueprintDeployment
    workflow: Annotated[list[BlueprintStep], Field(min_length=1, max_length=100)]
    requires_approval: bool = False
    recovery_policy: Literal['preserve', 'destroy_on_failure'] = 'preserve'

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

        by_id = {step.id: step for step in self.workflow}
        rollback_targets = {step.rollback for step in self.workflow if step.rollback}
        if any(target not in known for target in rollback_targets):
            raise ValueError('Workflow rollback references a missing step')
        if any(step.rollback == step.id for step in self.workflow if step.rollback):
            raise ValueError('Workflow step cannot rollback to itself')
        safe_rollback_types = {'terraform_destroy', 'notification', 'delay'}
        for target in rollback_targets:
            rollback_step = by_id[target]
            if rollback_step.type not in safe_rollback_types:
                raise ValueError('Rollback step must use terraform_destroy, notification or delay')
            if rollback_step.depends_on:
                raise ValueError('Rollback-only step cannot depend on normal workflow steps')
            if any(target in step.depends_on for step in self.workflow):
                raise ValueError('Rollback-only step cannot be a dependency of the normal workflow')
        if any(step.type == 'terraform_destroy' and step.id not in rollback_targets for step in self.workflow):
            raise ValueError('terraform_destroy is allowed only as a rollback target')
        if any(step.type == 'release_ip' for step in self.workflow):
            raise ValueError('release_ip is not allowed during VM provisioning; IP is released by destroy/recovery')

        legacy_markers = {
            'generate_hostname', 'allocate_ip',
            'create_vm', 'clone_vm', 'configure_vm', 'cloud_init',
            'start_vm', 'set_hostname', 'set_tags',
        }
        for step in self.workflow:
            if step.type in legacy_markers and (step.conditions or step.retry or step.rollback):
                raise ValueError(
                    'Compile-time/declarative workflow markers cannot use conditions, retry or rollback'
                )

        declarative = {'create_vm', 'clone_vm', 'configure_vm', 'cloud_init', 'start_vm', 'set_hostname', 'set_tags'}
        def ancestors(step_id):
            result = set()
            pending = list(graph[step_id])
            while pending:
                parent = pending.pop()
                if parent in result:
                    continue
                result.add(parent)
                pending.extend(graph[parent])
            return result
        for step in self.workflow:
            if step.type in declarative and any(by_id[parent].type == 'terraform_apply' for parent in ancestors(step.id)):
                raise ValueError('Declarative VM steps must run before terraform_apply')

        approval_steps = [step for step in self.workflow if step.type == 'approval']
        if approval_steps and not self.requires_approval:
            raise ValueError('Workflow approval step requires requires_approval=true')
        if len(approval_steps) > 1:
            raise ValueError('Workflow can contain at most one approval step')
        if approval_steps:
            approval_step = approval_steps[0]
            if approval_step.conditions or approval_step.retry or approval_step.rollback:
                raise ValueError('Workflow approval step cannot use conditions, retry or rollback')

        apply_steps = [step for step in self.workflow if step.type == 'terraform_apply']
        if len(apply_steps) > 1:
            raise ValueError('Workflow can contain exactly one terraform_apply step')
        legacy_provisioning = any(step.type in legacy_markers for step in self.workflow)
        if not apply_steps and not legacy_provisioning:
            raise ValueError('Workflow must contain terraform_apply or a legacy provisioning marker')

        plan_steps = [step for step in self.workflow if step.type == 'terraform_plan']
        if len(plan_steps) > 1:
            raise ValueError('Workflow can contain at most one terraform_plan step')
        if plan_steps and not apply_steps:
            raise ValueError('terraform_plan requires an explicit terraform_apply step')

        if apply_steps:
            apply_id = apply_steps[0].id
            if plan_steps and plan_steps[0].id not in ancestors(apply_id):
                raise ValueError('terraform_plan must be an ancestor of terraform_apply')

            if approval_steps:
                approval_id = approval_steps[0].id
                if approval_id not in ancestors(apply_id):
                    raise ValueError('approval must be an ancestor of terraform_apply')
                if plan_steps and plan_steps[0].id not in ancestors(approval_id):
                    raise ValueError('terraform_plan must be an ancestor of approval')

            vm_runtime_types = {
                'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh',
                'run_ansible_playbook', 'create_snapshot', 'health_check',
            }
            for step in self.workflow:
                if step.type in vm_runtime_types and apply_id not in ancestors(step.id):
                    raise ValueError(f'{step.type} must depend on terraform_apply')
        elif approval_steps:
            raise ValueError('approval requires an explicit terraform_apply step')

        return self


class BlueprintExecuteInput(Input):
    variables: Annotated[dict[str, Any], Field(max_length=100)] = Field(default_factory=dict)
    hostname_values: dict[str, Annotated[str, Field(min_length=1, max_length=63)]] = Field(default_factory=dict)
    apmid: Annotated[str | None, Field(max_length=63, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$')] = None
    environment: Literal['test', 'dev', 'nonprod', 'prod'] | None = None


class ScheduledOperationInput(Input):
    name: Name
    deployment_id: Annotated[str, Field(min_length=36, max_length=36)]
    operation: Literal['terraform.plan', 'terraform.apply', 'terraform.destroy']
    next_run_at: datetime
    interval_seconds: int | None = Field(default=None, ge=60, le=31536000)

    @field_validator('next_run_at')
    @classmethod
    def future_run(cls, value):
        from datetime import timezone
        current = datetime.now(timezone.utc)
        candidate = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if candidate <= current:
            raise ValueError('next_run_at must be in the future')
        return candidate.astimezone(timezone.utc).replace(tzinfo=None)


class WebhookEndpointInput(Input):
    name: Name
    url: Annotated[str, Field(min_length=9, max_length=2048)]
    events: Annotated[list[str], Field(min_length=1, max_length=64)]

    @field_validator('events')
    @classmethod
    def event_patterns(cls, value):
        import re
        result = []
        for item in value:
            normalized = str(item).strip().lower()
            valid = (
                normalized == '*'
                or (
                    len(normalized) <= 128
                    and re.fullmatch(
                        r'[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+',
                        normalized,
                    )
                )
                or (
                    len(normalized) <= 128
                    and re.fullmatch(
                        r'[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*\.\*',
                        normalized,
                    )
                )
            )
            if not valid:
                raise ValueError('Webhook event must be an event name, prefix wildcard such as job.* or *')
            if normalized not in result:
                result.append(normalized)
        return result
    is_active: bool = True

    @field_validator('url')
    @classmethod
    def https_webhook(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('Webhook URL must be HTTPS without embedded credentials or fragment')
        return value
