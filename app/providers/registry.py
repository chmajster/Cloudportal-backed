from fastapi import HTTPException
from app.providers.proxmox import ProxmoxProvider

PROVIDERS = {'proxmox': ProxmoxProvider}


def provider_for(credential):
    adapter = PROVIDERS.get(credential.type)
    if adapter is None:
        raise HTTPException(422, 'No installed infrastructure adapter for this credential type')
    return adapter(credential)
