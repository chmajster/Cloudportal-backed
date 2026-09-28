from fastapi import HTTPException

from app.onboarding.providers.proxmox import ProxmoxOnboardingAdapter
from app.providers.registry import provider_for


ONBOARDING_ADAPTERS = {
    'proxmox': ProxmoxOnboardingAdapter,
}


def onboarding_adapter(provider_row, credential):
    adapter_type = ONBOARDING_ADAPTERS.get(provider_row.type)
    if adapter_type is None:
        raise HTTPException(422, 'Provider does not support brownfield discovery yet')
    infrastructure = provider_for(credential)
    return adapter_type(provider_row, infrastructure)


def onboarding_capabilities(provider_type: str) -> dict:
    adapter_type = ONBOARDING_ADAPTERS.get(provider_type)
    if adapter_type is None:
        return {
            'supports_discovery': False,
            'supports_vm_import': False,
            'supports_guest_agent': False,
            'supports_snapshots': False,
            'supports_power_actions': False,
            'supports_console': False,
            'supports_network_inspection': False,
            'supports_storage_inspection': False,
            'supports_guest_discovery': False,
        }
    return adapter_type.static_capabilities()
