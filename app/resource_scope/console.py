"""Reauthorize ephemeral console capabilities against their saved principal/scope."""
from app.database import session
from app.models import Credential, Provider
from app.providers.registry import provider_for
from app.resource_scope.authorization import Scope, authorize
from app.resource_scope.database import bind_scope
from app.resource_scope.service import guard_raw_provider, guard_vm_identity
from app.tenancy.authorization import Principal, fail


def console_access(record, *, adapter=False):
    try:
        principal = Principal(int(record['user_id']), int(record['token_id']))
        scope = Scope(record['tenant_id'], record['project_id'])
        provider_id, vmid = int(record['provider_id']), int(record['vmid'])
    except (KeyError, TypeError, ValueError):
        fail(410, 'CONSOLE_EXPIRED', 'Console session is no longer valid')
    with session() as db:
        permission = str(record.get('permission') or 'vms.console')
        if permission not in {'vms.console', 'proxmox_admin.console.use'}:
            fail(410, 'CONSOLE_EXPIRED', 'Console session permission is invalid')
        actor, scoped = authorize(db, principal, scope, permission)
        bind_scope(db, scope)
        if permission == 'vms.console':
            guard_raw_provider(db, provider_id, scope)
            guard_vm_identity(db, provider_id, vmid, scope)
        elif 'proxmox_admin.scope.all' not in actor.global_permissions:
            from app.resource_scope.service import filter_provider_vms
            if not filter_provider_vms(db, provider_id, [{'vmid': vmid}]):
                fail(404, 'RESOURCE_NOT_FOUND', 'Console resource is no longer accessible')
        provider = db.get(Provider, provider_id)
        credential = db.get(Credential, provider.credentials_id) if provider else None
        if provider is None or credential is None or provider.type != 'proxmox' or credential.type != 'proxmox':
            fail(404, 'RESOURCE_NOT_FOUND', 'Console infrastructure is no longer accessible')
        return provider_for(credential) if adapter else None
