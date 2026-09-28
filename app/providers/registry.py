from fastapi import HTTPException
from app.models import now
from app.providers.proxmox import ProxmoxProvider
from app.providers.cloud import AWSProvider, AzureProvider, OpenStackProvider, VMwareProvider

PROVIDERS = {
    'proxmox': ProxmoxProvider,
    'aws': AWSProvider,
    'azure': AzureProvider,
    'openstack': OpenStackProvider,
    'vmware': VMwareProvider,
}

PROVIDER_TYPES = tuple(PROVIDERS)
PROVIDER_LABELS = {
    'proxmox': 'Proxmox',
    'vmware': 'VMware',
    'aws': 'AWS',
    'azure': 'Azure',
    'openstack': 'OpenStack',
}
DEFAULT_PROVIDER_ENABLED = {name: name == 'proxmox' for name in PROVIDER_TYPES}


def provider_for(credential):
    if credential.expires_at is not None and credential.expires_at <= now():
        raise HTTPException(409, 'Infrastructure credential is expired')
    adapter = PROVIDERS.get(credential.type)
    if adapter is None:
        raise HTTPException(422, 'No installed infrastructure adapter for this credential type')
    return adapter(credential)
