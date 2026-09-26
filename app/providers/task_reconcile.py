import hashlib
import json

from sqlalchemy import select

from app.database import session
from app.models import Credential, ManagedVM, Provider, now
from app.providers.registry import provider_for
from app.security.core import redis_client


TASK_PREFIX = 'cp:proxmox-task:'
TASK_TTL_SECONDS = 86400


def _task_key(upid):
    digest = hashlib.sha256(str(upid).encode()).hexdigest()
    return TASK_PREFIX + digest


def track_proxmox_task(*, provider_id, node, upid, action, created_by,
                       vm_id=None, target_node=None, target_vm_id=None, name=None,
                       convert_to_template=False):
    if not upid:
        return False
    payload = {
        'provider_id': int(provider_id),
        'node': str(node),
        'upid': str(upid),
        'action': str(action),
        'created_by': int(created_by),
        'vm_id': int(vm_id) if vm_id is not None else None,
        'target_node': str(target_node) if target_node else None,
        'target_vm_id': int(target_vm_id) if target_vm_id is not None else None,
        'name': str(name) if name else None,
        'convert_to_template': bool(convert_to_template),
    }
    try:
        redis_client().setex(_task_key(upid), TASK_TTL_SECONDS, json.dumps(payload))
        return True
    except Exception:
        # Mutation has already started; tracking failure must not trigger a retry.
        return False


def _live_vm(adapter, node, vm_id):
    try:
        value = adapter.vm_status(node, int(vm_id)) or {}
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _apply_success(db, adapter, item):
    provider_id = int(item['provider_id'])
    action = item['action']
    vm_id = item.get('vm_id')
    target_vm_id = item.get('target_vm_id')
    target_node = item.get('target_node') or item.get('node')
    from app.resource_scope.authorization import DEFAULT_SCOPE
    from app.resource_scope.service import guard_vm_identity
    identity = target_vm_id if action in {'clone', 'restore'} else vm_id
    if identity is not None:
        # The synchronous provider surface is legacy Default-only. Never let
        # a delayed task overwrite a VM claimed by another project meanwhile.
        guard_vm_identity(db, provider_id, identity, DEFAULT_SCOPE)

    if action in {'clone', 'restore'}:
        identity = int(target_vm_id)
        existing = db.scalar(select(ManagedVM).where(
            ManagedVM.provider_id == provider_id,
            ManagedVM.vm_id == identity,
        ))
        if existing is not None and existing.deployment_id is not None:
            raise RuntimeError('Task target is already managed by a deployment')
        live = _live_vm(adapter, target_node, identity)
        values = {
            'deployment_id': None,
            'node': str(live.get('node') or target_node),
            'name': str(live.get('name') or item.get('name') or f'vm-{identity}'),
            'management_mode': 'external',
            'lifecycle_status': 'active',
            'created_by': int(item['created_by']),
            'destroyed_at': None,
        }
        if existing is None:
            db.add(ManagedVM(provider_id=provider_id, vm_id=identity, **values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        return

    if vm_id is None:
        return
    row = db.scalar(select(ManagedVM).where(
        ManagedVM.provider_id == provider_id,
        ManagedVM.vm_id == int(vm_id),
    ))
    if row is None:
        return

    if action == 'migrate':
        row.node = str(target_node)
    elif action == 'config':
        if item.get('name'):
            row.name = str(item['name'])
    elif action == 'delete':
        row.lifecycle_status = 'destroyed'
        row.destroyed_at = now()
    elif action == 'template':
        db.delete(row)


def _queue_clone_template_follow_up(adapter, item):
    if item.get('action') != 'clone' or not item.get('convert_to_template'):
        return None
    target_node = str(item.get('target_node') or item.get('node') or '')
    target_vm_id = item.get('target_vm_id')
    if not target_node or target_vm_id is None:
        raise RuntimeError('Clone-to-template follow-up is missing target identity')

    task = adapter.convert_to_template(target_node, int(target_vm_id))
    if not task:
        return {
            'provider_id': int(item['provider_id']),
            'node': target_node,
            'upid': None,
            'action': 'template',
            'created_by': int(item['created_by']),
            'vm_id': int(target_vm_id),
            'target_node': None,
            'target_vm_id': None,
            'name': item.get('name'),
            'convert_to_template': False,
        }

    tracked = track_proxmox_task(
        provider_id=int(item['provider_id']),
        node=target_node,
        upid=task,
        action='template',
        created_by=int(item['created_by']),
        vm_id=int(target_vm_id),
        name=item.get('name'),
    )
    if not tracked:
        raise RuntimeError(
            'Template conversion started but its Proxmox task could not be tracked'
        )
    return None


def reconcile_proxmox_tasks_once(limit=100):
    try:
        redis = redis_client()
        keys = list(redis.scan_iter(match=TASK_PREFIX + '*', count=limit))[:limit]
    except Exception:
        return {'checked': 0, 'completed': 0}
    if not keys:
        return {'checked': 0, 'completed': 0}

    checked = completed = 0
    completed_keys = []
    with session() as db:
        for key in keys:
            try:
                raw = redis.get(key)
                if not raw:
                    continue
                item = json.loads(raw)
                # Flush/constraint failures roll back only this item. Redis
                # acknowledgements happen after the database commit, so a
                # crash cannot silently discard the ownership reconciliation.
                with db.begin_nested():
                    provider = db.get(Provider, int(item['provider_id']))
                    if provider is None or provider.type != 'proxmox':
                        completed_keys.append(key)
                        continue
                    credential = db.get(Credential, provider.credentials_id)
                    if credential is None:
                        completed_keys.append(key)
                        continue
                    adapter = provider_for(credential)
                    checked += 1
                    status = adapter.task_status(item['node'], item['upid']) or {}
                    if str(status.get('status') or '').lower() != 'stopped':
                        continue
                    if str(status.get('exitstatus') or '') == 'OK':
                        _apply_success(db, adapter, item)
                        immediate_template = _queue_clone_template_follow_up(adapter, item)
                        if immediate_template is not None:
                            _apply_success(db, adapter, immediate_template)
                    db.flush()
                completed_keys.append(key)
                completed += 1
            except Exception:
                # Keep the record for the next dispatcher pass.
                continue
        db.commit()
    for key in completed_keys:
        try:
            redis.delete(key)
        except Exception:
            # Retrying metadata reconciliation is idempotent.
            pass
    return {'checked': checked, 'completed': completed}
