from sqlalchemy import or_, select

from app.models import ManagedResource, ManagedVM
from app.onboarding.models import ResourceExternalIdentity


def identity_tuple(resource):
    location = resource.get('location') or {}
    return (
        int(resource['provider_id']),
        str(location.get('cluster') or ('provider-' + str(resource['provider_id']))),
        str(resource['resource_type']),
        str(resource['external_id']),
    )


def _candidate(kind, row, reasons):
    return {
        'kind': kind,
        'id': row.id,
        'name': getattr(row, 'name', '') or '',
        'reasons': sorted(set(reasons)),
    }


def _metadata_values(row):
    metadata = getattr(row, 'metadata_json', None)
    return metadata if isinstance(metadata, dict) else {}


def find_matches(db, resource):
    provider_id, cluster_id, resource_type, external_id = identity_tuple(resource)
    exact = db.scalar(select(ResourceExternalIdentity).where(
        ResourceExternalIdentity.provider_id == provider_id,
        ResourceExternalIdentity.cluster_id == cluster_id,
        ResourceExternalIdentity.resource_type == resource_type,
        ResourceExternalIdentity.external_id == external_id,
        ResourceExternalIdentity.retired_at.is_(None),
    ))
    if exact is not None:
        return {
            'status': 'INVENTORY_ONLY' if exact.management_mode == 'INVENTORY_IMPORT' else 'MANAGED',
            'exact_identity_id': exact.id,
            'candidates': [],
        }

    provider_uuid = str(resource.get('provider_uuid') or '').strip()
    if provider_uuid:
        duplicate = db.scalar(select(ResourceExternalIdentity).where(
            ResourceExternalIdentity.provider_uuid == provider_uuid,
            ResourceExternalIdentity.retired_at.is_(None),
        ).limit(1))
        if duplicate is not None:
            return {
                'status': 'DUPLICATE',
                'exact_identity_id': None,
                'candidates': [{
                    'kind': 'external_identity', 'id': duplicate.id,
                    'name': '', 'reasons': ['provider_uuid'],
                }],
            }

    candidates = {}
    try:
        vmid = int(external_id)
    except (TypeError, ValueError):
        vmid = None
    if vmid is not None:
        for row in db.scalars(select(ManagedVM).where(
            ManagedVM.provider_id == provider_id,
            ManagedVM.vm_id == vmid,
            ManagedVM.lifecycle_status != 'destroyed',
        )).all():
            candidates[('vm', row.id)] = _candidate('managed_vm', row, ['provider_id', 'vmid'])

    name = str(resource.get('name') or '').strip()
    hostname = str(resource.get('hostname') or '').strip()
    names = {value.casefold() for value in (name, hostname) if value}
    if names:
        for row in db.scalars(select(ManagedVM).where(ManagedVM.lifecycle_status != 'destroyed')).all():
            reasons = []
            if str(row.name or '').casefold() in names:
                reasons.append('name')
            metadata = _metadata_values(row)
            if str(metadata.get('hostname') or '').casefold() in names:
                reasons.append('hostname')
            if reasons:
                candidates.setdefault(('vm', row.id), _candidate('managed_vm', row, reasons))

    addresses = {str(value) for value in resource.get('addresses') or [] if value}
    macs = {
        str(item.get('mac') or '').lower()
        for item in resource.get('networks') or []
        if item.get('mac')
    }
    for row in db.scalars(select(ManagedVM).where(ManagedVM.lifecycle_status != 'destroyed')).all():
        metadata = _metadata_values(row)
        reasons = []
        stored_addresses = set(metadata.get('addresses') or [])
        stored_macs = {str(value).lower() for value in metadata.get('mac_addresses') or []}
        if addresses & stored_addresses:
            reasons.append('ip')
        if macs & stored_macs:
            reasons.append('mac')
        if provider_uuid and str(metadata.get('provider_uuid') or '') == provider_uuid:
            reasons.append('provider_uuid')
        if reasons:
            key = ('vm', row.id)
            if key in candidates:
                candidates[key]['reasons'] = sorted(set(candidates[key]['reasons'] + reasons))
            else:
                candidates[key] = _candidate('managed_vm', row, reasons)

    if names or addresses:
        conditions = []
        if names:
            conditions.append(ManagedResource.name.in_([name, hostname]))
        if addresses:
            conditions.append(ManagedResource.primary_ip.in_(addresses))
        if conditions:
            for row in db.scalars(select(ManagedResource).where(
                ManagedResource.lifecycle_status != 'destroyed',
                or_(*conditions),
            )).all():
                reasons = []
                if str(row.name or '').casefold() in names:
                    reasons.append('name')
                if row.primary_ip in addresses:
                    reasons.append('ip')
                candidates[('resource', row.id)] = _candidate('managed_resource', row, reasons)

    result = list(candidates.values())
    return {
        'status': 'NEW' if not result else ('POSSIBLE_MATCH' if len(result) == 1 else 'CONFLICT'),
        'exact_identity_id': None,
        'candidates': result,
    }
