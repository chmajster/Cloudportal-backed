import copy
import time

from fastapi import HTTPException
from sqlalchemy import select

from app.awx import AwxClient, AwxError, render_awx_inventory_name
from app.database import session
from app.executors.base import Cancelled, ExecutionFailed
from app.models import Credential, Job, ManagedResource, ManagedVM, Provider, now
from app.onboarding.classification import classification_preview
from app.onboarding.guest import GuestDiscoveryError, discover_guest, install_qemu_guest_agent
from app.onboarding.matching import find_matches
from app.onboarding.models import (DiscoveredResource, DiscoverySession, OnboardingConflict,
                                   OnboardingJobItem, ResourceExternalIdentity,
                                   ResourceSyncState)
from app.onboarding.providers.registry import onboarding_adapter
from app.onboarding.service import (bind_job_scope, onboarding_settings, resource_snapshot,
                                    refresh_identity, worker_audit_event)
from app.projects.models import Project
from app.security.core import decrypt_secret
from app.tenancy.models import Tenant


STEPS = (
    'Provider connection',
    'Resource validation',
    'External identity check',
    'RBAC validation',
    'Organization mapping',
    'Resource creation',
    'Provider synchronization',
    'Guest discovery',
    'AWX onboarding',
    'Post-validation',
)


def _stage(context, item_id, number, status='IN_PROGRESS', detail=None):
    label = STEPS[number - 1]
    suffix = (' ' + detail) if detail else ''
    context.log(f'[{number}/10] {label} ........ {status}{suffix}')
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        row = db.get(OnboardingJobItem, item_id)
        if row:
            row.stage = label
            if status == 'IN_PROGRESS':
                row.status = 'IN_PROGRESS'
        db.commit()


def _job_item(context, item_id, *, status=None, error=None, result=None, identity_id=None):
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        row = db.get(OnboardingJobItem, item_id)
        if row:
            if status is not None:
                row.status = status
            if error is not None:
                row.error = str(error)[:4000] if error else None
            if result is not None:
                row.result_json = copy.deepcopy(result)
            if identity_id is not None:
                row.external_identity_id = identity_id
        db.commit()


def _emit(context, action, resource, resource_id, payload=None, *, result='success'):
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        worker_audit_event(
            db, job, action, resource, resource_id, payload or {}, result=result
        )
        db.commit()


def _provider_for_discovered(db, row):
    provider = db.get(Provider, row.provider_id)
    if provider is None:
        raise ExecutionFailed('Provider no longer exists or is not assigned to the project')
    credential = db.get(Credential, provider.credentials_id)
    if credential is None:
        raise ExecutionFailed('Provider credential is unavailable in this project')
    return provider, credential, onboarding_adapter(provider, credential)


def _discovery(context):
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        discovery = db.get(DiscoverySession, (job.payload or {}).get('session_id'))
        if discovery is None:
            raise ExecutionFailed('Discovery session no longer exists')
        provider, credential, adapter = _provider_for_discovered(db, type('R', (), {'provider_id': discovery.provider_id})())
        config = onboarding_settings(db)
        discovery.status = 'RUNNING'
        discovery.started_at = now()
        worker_audit_event(
            db, job, 'vm.discovery.started', 'onboarding_discovery', discovery.id,
            {'provider': {'id': provider.id, 'type': provider.type}},
        )
        scope_json = copy.deepcopy(discovery.scope_json or {})
        db.commit()

    context.stage('vm.discovery.running')
    context.log('[1/4] Provider connection ........ OK')
    context.log('[2/4] Resource enumeration ....... IN_PROGRESS')

    def cancelled():
        context.check(force=True)
        return False

    try:
        resources = adapter.discover_resources(
            scope_json,
            concurrency=config['discovery_concurrency'],
            cancelled=cancelled,
        )
    except Cancelled:
        with session() as db:
            job = db.get(Job, context.job.id)
            bind_job_scope(db, job)
            discovery = db.get(DiscoverySession, (job.payload or {}).get('session_id'))
            if discovery:
                discovery.status = 'CANCELLED'
                discovery.completed_at = now()
            db.commit()
        raise
    except Exception as exc:
        with session() as db:
            job = db.get(Job, context.job.id)
            bind_job_scope(db, job)
            discovery = db.get(DiscoverySession, (job.payload or {}).get('session_id'))
            if discovery:
                discovery.status = 'FAILED'
                discovery.error_summary = str(exc)[:2000]
                discovery.completed_at = now()
                worker_audit_event(
                    db, job, 'vm.discovery.failed', 'onboarding_discovery', discovery.id,
                    {'error': {'type': exc.__class__.__name__}},
                    result='failure',
                )
            db.commit()
        raise ExecutionFailed('Provider discovery failed') from None

    context.log(f'[2/4] Resource enumeration ....... OK ({len(resources)})')
    context.log('[3/4] Identity matching .......... IN_PROGRESS')
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        discovery = db.get(DiscoverySession, (job.payload or {}).get('session_id'))
        if discovery is None:
            raise ExecutionFailed('Discovery session disappeared during execution')
        provider = db.get(Provider, discovery.provider_id)
        auto_import_enabled = bool((job.payload or {}).get('automatic_inventory_import'))
        auto_classification_enabled = bool((job.payload or {}).get('auto_classification', True))
        auto_import_items = []
        status_counts = {}
        for index, normalized in enumerate(resources, start=1):
            context.check()
            item = normalized.public()
            item['provider_id'] = normalized.provider_id
            match = find_matches(db, item)
            preview = classification_preview(db, item, provider_type=provider.type if provider else None)
            identity = adapter.get_external_identity(normalized)
            row = DiscoveredResource(
                session_id=discovery.id,
                provider_id=normalized.provider_id,
                cluster_id=identity['cluster_id'],
                node_id=identity['node_id'],
                resource_type=normalized.resource_type,
                external_id=normalized.external_id,
                provider_uuid=normalized.provider_uuid,
                name=normalized.name,
                hostname=normalized.hostname,
                power_state=normalized.power_state,
                is_template=normalized.is_template,
                discovery_status=match['status'],
                normalized_json=item,
                raw_metadata_json=copy.deepcopy(normalized.raw_metadata or {}),
                match_candidates_json=match['candidates'],
                rule_trace_json=preview['trace'],
                first_seen_at=now(),
                last_seen_at=now(),
            )
            db.add(row)
            db.flush()
            status_counts[match['status']] = status_counts.get(match['status'], 0) + 1
            mapping = dict(preview.get('mapping') or {})
            if (
                auto_import_enabled
                and auto_classification_enabled
                and match['status'] == 'NEW'
                and mapping.get('apmid')
                and mapping.get('environment')
            ):
                auto_import_items.append({
                    'discovered_resource_id': row.id,
                    'mode': 'INVENTORY_IMPORT',
                    'mapping': mapping,
                    'mapping_sources': dict(preview.get('sources') or {}),
                    'guest_credential_id': None,
                    'awx_credential_id': None,
                    'integrations': {},
                })
            if match['status'] in {'POSSIBLE_MATCH', 'CONFLICT', 'DUPLICATE'}:
                db.add(OnboardingConflict(
                    tenant_id=job.tenant_id,
                    project_id=job.project_id,
                    discovered_resource_id=row.id,
                    provider_id=row.provider_id,
                    conflict_type=match['status'],
                    status='OPEN',
                    candidates_json=match['candidates'],
                    details_json={
                        'external_identity': identity,
                        'resource': {'name': row.name, 'external_id': row.external_id},
                    },
                    created_by=job.created_by,
                ))
            if index % 25 == 0:
                db.flush()
        discovery.discovered_count = len(resources)
        discovery.status = 'COMPLETED'
        discovery.completed_at = now()
        worker_audit_event(
            db, job, 'vm.discovery.completed', 'onboarding_discovery', discovery.id,
            {
                'provider': {'id': discovery.provider_id},
                'summary': {
                    'discovered': len(resources),
                    'available': status_counts.get('NEW', 0),
                    'managed': status_counts.get('MANAGED', 0),
                    'inventory_only': status_counts.get('INVENTORY_ONLY', 0),
                    'conflicts': (
                        status_counts.get('CONFLICT', 0)
                        + status_counts.get('POSSIBLE_MATCH', 0)
                        + status_counts.get('DUPLICATE', 0)
                    ),
                },
            },
        )
        if auto_import_items:
            auto_job = Job(
                operation='onboarding.import',
                payload={
                    'items': auto_import_items,
                    'selected': len(auto_import_items),
                    'automatic_inventory_import': True,
                    'discovery_job_id': job.id,
                },
                created_by=job.created_by,
                token_id=None if job.source == 'Scheduler' else job.token_id,
                request_id=job.request_id,
                ip=job.ip,
                source=job.source,
                tenant_id=job.tenant_id,
                project_id=job.project_id,
            )
            db.add(auto_job)
            db.flush()
            for config_item in auto_import_items:
                discovered_item = db.get(
                    DiscoveredResource, config_item['discovered_resource_id']
                )
                db.add(OnboardingJobItem(
                    job_id=auto_job.id,
                    discovered_resource_id=discovered_item.id,
                    provider_id=discovered_item.provider_id,
                    external_id=discovered_item.external_id,
                    name=discovered_item.name,
                    status='QUEUED',
                ))
            worker_audit_event(
                db, job, 'vm.onboarding.requested', 'onboarding_job', auto_job.id,
                {
                    'automatic_inventory_import': True,
                    'discovery_job_id': job.id,
                    'selected': len(auto_import_items),
                },
            )
        db.commit()
    context.log('[3/4] Identity matching .......... OK')
    if auto_import_items:
        context.log(f'[4/4] Discovery persistence ...... OK; queued {len(auto_import_items)} inventory imports')
    else:
        context.log('[4/4] Discovery persistence ...... OK')
    context.progress(100, 'Discovery completed', phase='complete')


def _mapping_metadata(config, live):
    mapping = copy.deepcopy(config.get('mapping') or {})
    return {
        **mapping,
        'hostname': live.get('hostname') or live.get('name'),
        'addresses': list(live.get('addresses') or []),
        'mac_addresses': [
            str(item.get('mac')).lower()
            for item in live.get('networks') or []
            if item.get('mac')
        ],
        'provider_uuid': live.get('provider_uuid'),
        'provider_location': copy.deepcopy(live.get('location') or {}),
        'resource_type': live.get('resource_type'),
        'onboarding_mode': config.get('mode'),
        'origin': 'onboarded',
    }


def _exact_identity(db, discovered):
    return db.scalar(select(ResourceExternalIdentity).where(
        ResourceExternalIdentity.provider_id == discovered.provider_id,
        ResourceExternalIdentity.cluster_id == discovered.cluster_id,
        ResourceExternalIdentity.resource_type == discovered.resource_type,
        ResourceExternalIdentity.external_id == discovered.external_id,
        ResourceExternalIdentity.retired_at.is_(None),
    ))


def _create_or_update_core(context, item_id, config, live):
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        discovered = db.get(DiscoveredResource, config['discovered_resource_id'])
        if discovered is None:
            raise ExecutionFailed('Discovered resource no longer exists')
        existing = _exact_identity(db, discovered)
        metadata = _mapping_metadata(config, live)
        mode = config.get('mode') or 'INVENTORY_IMPORT'

        if existing is not None:
            identity = existing
            identity.management_mode = mode
            identity.management_source = 'onboarded'
            identity.provisioning_source = 'external'
            identity.guest_credential_id = config.get('guest_credential_id')
            identity.awx_credential_id = config.get('awx_credential_id')
            identity.business_metadata_json = {
                **(identity.business_metadata_json or {}),
                **metadata,
            }
            identity.provider_metadata_json = copy.deepcopy(live)
            identity.last_seen_at = now()
            identity.onboarded_at = identity.onboarded_at or now()
            identity.onboarded_by = identity.onboarded_by or job.created_by
            if identity.managed_vm_id:
                managed = db.get(ManagedVM, identity.managed_vm_id)
                if managed:
                    managed.node = str((live.get('location') or {}).get('node') or managed.node)
                    managed.name = live.get('name') or managed.name
                    managed.management_mode = 'inventory_only' if mode == 'INVENTORY_IMPORT' else 'onboarded'
                    managed.management_source = 'onboarded'
                    managed.provisioning_source = 'external'
                    managed.metadata_json = {**(managed.metadata_json or {}), **metadata}
                    managed.lifecycle_status = 'active'
            elif identity.managed_resource_id:
                managed = db.get(ManagedResource, identity.managed_resource_id)
                if managed:
                    managed.name = live.get('name') or managed.name
                    managed.primary_ip = (live.get('addresses') or [None])[0]
                    managed.management_source = 'onboarded'
                    managed.provisioning_source = 'external'
                    managed.metadata_json = {**(managed.metadata_json or {}), **metadata}
                    managed.lifecycle_status = 'active'
        else:
            is_vm = discovered.resource_type == 'qemu' and not discovered.is_template
            if is_vm:
                managed = ManagedVM(
                    tenant_id=job.tenant_id,
                    project_id=job.project_id,
                    provider_id=discovered.provider_id,
                    deployment_id=None,
                    node=str((live.get('location') or {}).get('node') or discovered.node_id),
                    vm_id=int(discovered.external_id),
                    name=live.get('name') or discovered.name,
                    management_mode='inventory_only' if mode == 'INVENTORY_IMPORT' else 'onboarded',
                    management_source='onboarded',
                    provisioning_source='external',
                    metadata_json=metadata,
                    lifecycle_status='active',
                    created_by=job.created_by,
                )
                db.add(managed)
                db.flush()
                managed_vm_id, managed_resource_id = managed.id, None
            else:
                resource_type = 'proxmox_template' if discovered.is_template else discovered.resource_type
                managed = ManagedResource(
                    tenant_id=job.tenant_id,
                    project_id=job.project_id,
                    deployment_id=None,
                    provider_id=discovered.provider_id,
                    provider='proxmox',
                    resource_type=resource_type,
                    external_id=discovered.external_id,
                    name=live.get('name') or discovered.name,
                    primary_ip=(live.get('addresses') or [None])[0],
                    lifecycle_status='active',
                    metadata_json=metadata,
                    management_source='onboarded',
                    provisioning_source='external',
                    created_by=job.created_by,
                )
                db.add(managed)
                db.flush()
                managed_vm_id, managed_resource_id = None, managed.id

            identity = ResourceExternalIdentity(
                tenant_id=job.tenant_id,
                project_id=job.project_id,
                provider_id=discovered.provider_id,
                managed_vm_id=managed_vm_id,
                managed_resource_id=managed_resource_id,
                resource_type=discovered.resource_type,
                external_id=discovered.external_id,
                cluster_id=discovered.cluster_id,
                node_id=str((live.get('location') or {}).get('node') or discovered.node_id),
                provider_uuid=live.get('provider_uuid') or discovered.provider_uuid,
                management_mode=mode,
                management_source='onboarded',
                provisioning_source='external',
                guest_credential_id=config.get('guest_credential_id'),
                awx_credential_id=config.get('awx_credential_id'),
                business_metadata_json=metadata,
                provider_metadata_json=copy.deepcopy(live),
                first_seen_at=discovered.first_seen_at,
                last_seen_at=now(),
                onboarded_at=now(),
                onboarded_by=job.created_by,
            )
            db.add(identity)
            db.flush()

        snapshot = resource_snapshot(live)
        sync = db.get(ResourceSyncState, identity.id)
        if sync is None:
            sync = ResourceSyncState(
                identity_id=identity.id,
                status='SYNCED',
                expected_json=snapshot,
                actual_json=snapshot,
                drift_json={},
                last_sync_at=now(),
                last_seen_at=now(),
                next_sync_at=now(),
            )
            db.add(sync)
        discovered.discovery_status = (
            'INVENTORY_ONLY' if mode == 'INVENTORY_IMPORT' else 'MANAGED'
        )
        row = db.get(OnboardingJobItem, item_id)
        if row:
            row.external_identity_id = identity.id
        db.commit()
        return identity.id


def _load_live(context, config):
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        row = db.get(DiscoveredResource, config['discovered_resource_id'])
        if row is None:
            raise ExecutionFailed('Discovered resource no longer exists')
        provider, credential, adapter = _provider_for_discovered(db, row)
        external_id = row.external_id
        resource_type = row.resource_type
    live = adapter.get_resource(resource_type=resource_type, external_id=external_id)
    if live is None:
        raise ExecutionFailed('VM disappeared during onboarding')
    return adapter, live.public()


def _guest_and_agent(context, identity_id, config, adapter, live):
    integrations = config.get('integrations') or {}
    if not (
        integrations.get('enable_qemu_guest_agent')
        or integrations.get('install_guest_agent')
        or integrations.get('guest_discovery')
    ):
        return {'skipped': True}

    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        identity = db.get(ResourceExternalIdentity, identity_id)
        credential = (
            db.get(Credential, int(config['guest_credential_id']))
            if config.get('guest_credential_id') else None
        )

    if live.get('resource_type') != 'qemu':
        if integrations.get('install_guest_agent') or integrations.get('enable_qemu_guest_agent'):
            raise GuestDiscoveryError('QEMU Guest Agent operations are available only for QEMU VMs')
        if integrations.get('guest_discovery') and credential is None:
            raise GuestDiscoveryError('Guest discovery requires credential')
        facts = discover_guest(live, credential)
    else:
        if integrations.get('enable_qemu_guest_agent'):
            node = str((live.get('location') or {}).get('node') or '')
            adapter.provider.update_vm_config(node, int(live['external_id']), agent='1')
        install_result = None
        if integrations.get('install_guest_agent'):
            if credential is None:
                raise GuestDiscoveryError('Guest Agent installation requires guest credential')
            install_result = install_qemu_guest_agent(live, credential)
        facts = None
        if integrations.get('guest_discovery'):
            if credential is None:
                raise GuestDiscoveryError('Guest discovery requires credential')
            facts = discover_guest(live, credential)

    result = {'guest_discovery': facts}
    if live.get('resource_type') == 'qemu':
        result['guest_agent_installation'] = install_result
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        identity = db.get(ResourceExternalIdentity, identity_id)
        metadata = dict(identity.business_metadata_json or {})
        if facts is not None:
            metadata['guest_discovery'] = facts
        identity.business_metadata_json = metadata
        db.commit()
    return result


def _awx_client(credential):
    secret = decrypt_secret(credential)
    return AwxClient(
        credential.endpoint,
        verify_ssl=credential.verify_ssl,
        token=secret.get('token'),
        username=credential.username,
        password=secret.get('password'),
    )


def _awx_template_id(client, value):
    if value in (None, ''):
        return None
    if str(value).isdigit():
        return int(value)
    matches = client.list_resource('job_templates', params={'name': str(value)})
    exact = next(
        (row for row in matches if str(row.get('name') or '') == str(value)),
        None,
    )
    if exact is None:
        raise AwxError('AWX job template not found: ' + str(value))
    return int(exact['id'])


def _awx_onboard(context, identity_id, config, live):
    integrations = config.get('integrations') or {}
    if not integrations.get('add_to_awx'):
        return {'skipped': True}
    addresses = list(live.get('addresses') or [])
    if not addresses:
        raise AwxError('AWX onboarding requires an IP address')
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        identity = db.get(ResourceExternalIdentity, identity_id)
        credential = db.get(Credential, int(config.get('awx_credential_id') or 0))
        tenant = db.get(Tenant, job.tenant_id)
        project = db.get(Project, job.project_id)
        if credential is None or credential.type != 'awx':
            raise AwxError('AWX credential is unavailable')
        if tenant is None or project is None:
            raise AwxError('CloudPortal Organization/Project mapping is unavailable')
        mapping = dict(config.get('mapping') or {})
        resource_name = (
            (identity.business_metadata_json or {}).get('hostname')
            or live.get('hostname')
            or live.get('name')
        )
        identity_value = identity.id

    client = _awx_client(credential)
    organization = client.ensure_organization(name=tenant.name)
    awx_project = client.project_for_organization(
        name=project.name,
        organization_id=int(organization['id']),
    )
    inventory_id = integrations.get('awx_inventory_id')
    inventory_name = integrations.get('awx_inventory_name')
    if not inventory_name:
        inventory_name = render_awx_inventory_name(
            '<Projekt>-<APMID>-<ENV>',
            project=project.name,
            apmid=mapping.get('apmid'),
            environment=mapping.get('environment'),
        )
    inventory = client.ensure_inventory(
        inventory_id=inventory_id,
        name=inventory_name,
        organization_id=int(organization['id']),
    )
    variables = {
        'ansible_host': addresses[0],
        'cloudportal_managed': True,
        'cloudportal_resource_id': identity_value,
        'cloudportal_project': project.name,
    }
    if mapping.get('apmid'):
        variables['apmid'] = mapping['apmid']
    if mapping.get('environment'):
        variables['environment'] = mapping['environment']
    host = client.ensure_host(
        inventory_id=int(inventory['id']),
        hostname=resource_name,
        ansible_host=addresses[0],
        variables=variables,
    )
    groups = []
    for group_name in (
        ('env-' + str(mapping.get('environment')).lower()) if mapping.get('environment') else None,
        ('apmid-' + str(mapping.get('apmid')).lower()) if mapping.get('apmid') else None,
        integrations.get('awx_group'),
    ):
        if group_name and group_name not in groups:
            group = client.ensure_group(inventory_id=int(inventory['id']), name=group_name)
            client.add_host_to_group(group_id=int(group['id']), host_id=int(host['id']))
            groups.append(group_name)

    launches = []
    for key in ('onboarding_playbook', 'baseline_playbook', 'agent_installation_playbook'):
        template = integrations.get(key)
        if not template:
            continue
        template_id = _awx_template_id(client, template)
        launch = client.launch_job_template(
            template_id,
            hostname=resource_name,
            deployment_id=identity_value,
            environment=mapping.get('environment'),
            apmid=mapping.get('apmid'),
        )
        launches.append({'purpose': key, 'template_id': template_id, 'job_id': launch.get('job') or launch.get('id')})

    result = {
        'organization_id': int(organization['id']),
        'project_id': int(awx_project['id']),
        'inventory_id': int(inventory['id']),
        'host_id': int(host['id']),
        'groups': groups,
        'launches': launches,
    }
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        identity = db.get(ResourceExternalIdentity, identity_id)
        metadata = dict(identity.business_metadata_json or {})
        metadata['awx'] = result
        identity.business_metadata_json = metadata
        db.commit()
    return result


def _post_validate(context, identity_id, config, adapter):
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        identity = db.get(ResourceExternalIdentity, identity_id)
        if identity is None:
            raise ExecutionFailed('External identity was not persisted')
        managed = (
            db.get(ManagedVM, identity.managed_vm_id)
            if identity.managed_vm_id
            else db.get(ManagedResource, identity.managed_resource_id)
        )
        if managed is None:
            raise ExecutionFailed('Managed inventory linkage is missing')
        row = db.get(OnboardingJobItem, config['_item_id'])
        if row is None:
            raise ExecutionFailed('Onboarding item tracking row is missing')
        resource_id = managed.id
    live = adapter.refresh_resource(identity)
    if live is None:
        raise ExecutionFailed('Provider link cannot refresh the onboarded resource')
    capabilities = adapter.get_capabilities(live)
    return {
        'resource_id': resource_id,
        'identity_id': identity_id,
        'refresh_ok': True,
        'capabilities': capabilities,
    }


def _retryable(exc):
    if isinstance(exc, HTTPException):
        return exc.status_code in {408, 429, 502, 503, 504}
    return isinstance(exc, (TimeoutError, ConnectionError, OSError, AwxError, GuestDiscoveryError))


def _process_item_once(context, row, config):
    config = {**config, '_item_id': row.id}
    _stage(context, row.id, 1)
    adapter, live = _load_live(context, config)
    _stage(context, row.id, 1, 'OK')

    _stage(context, row.id, 2)
    _emit(
        context,
        'vm.onboarding.validating',
        'discovered_resource',
        row.discovered_resource_id,
        {'external_id': row.external_id, 'mode': config.get('mode')},
    )
    if live.get('external_id') != row.external_id:
        raise ExecutionFailed('Provider resource identity changed during onboarding')
    _stage(context, row.id, 2, 'OK')

    _stage(context, row.id, 3)
    _stage(context, row.id, 3, 'OK')

    _stage(context, row.id, 4)
    context.check(force=True)
    _stage(context, row.id, 4, 'OK')

    _stage(context, row.id, 5)
    mapping = config.get('mapping') or {}
    if not mapping.get('apmid') or not mapping.get('environment'):
        raise ExecutionFailed('Organization classification mapping is incomplete')
    _stage(context, row.id, 5, 'OK')

    _stage(context, row.id, 6)
    identity_id = _create_or_update_core(context, row.id, config, live)
    _job_item(context, row.id, identity_id=identity_id)
    _stage(context, row.id, 6, 'OK')

    _stage(context, row.id, 7)
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        identity = db.get(ResourceExternalIdentity, identity_id)
        identity, sync_state, refreshed = refresh_identity(db, identity)
        if sync_state.status in {'MISSING', 'UNREACHABLE'}:
            raise ExecutionFailed('Provider synchronization failed after resource creation')
        db.commit()
    _stage(context, row.id, 7, 'OK')

    _stage(context, row.id, 8)
    guest_result = _guest_and_agent(context, identity_id, config, adapter, live)
    if not guest_result.get('skipped'):
        _emit(
            context,
            'vm.onboarding.guest_discovered',
            'resource_external_identity',
            identity_id,
            {'external_id': row.external_id},
        )
    _stage(context, row.id, 8, 'OK' if not guest_result.get('skipped') else 'WARN', '(skipped)' if guest_result.get('skipped') else None)

    _stage(context, row.id, 9)
    if (config.get('integrations') or {}).get('add_to_awx'):
        _emit(
            context,
            'vm.onboarding.awx.started',
            'resource_external_identity',
            identity_id,
            {'external_id': row.external_id},
        )
    awx_result = _awx_onboard(context, identity_id, config, live)
    if not awx_result.get('skipped'):
        _emit(
            context,
            'vm.onboarding.awx.completed',
            'resource_external_identity',
            identity_id,
            {
                'external_id': row.external_id,
                'inventory_id': awx_result.get('inventory_id'),
                'host_id': awx_result.get('host_id'),
            },
        )
    _stage(context, row.id, 9, 'OK' if not awx_result.get('skipped') else 'WARN', '(skipped)' if awx_result.get('skipped') else None)

    _stage(context, row.id, 10)
    result = _post_validate(context, identity_id, config, adapter)
    result['guest'] = guest_result
    result['awx'] = awx_result
    _stage(context, row.id, 10, 'OK')
    return identity_id, result


def _import(context):
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        rows = db.scalars(
            select(OnboardingJobItem)
            .where(OnboardingJobItem.job_id == job.id)
            .order_by(OnboardingJobItem.created_at, OnboardingJobItem.name)
        ).all()
        configs = {
            item.get('discovered_resource_id'): dict(item)
            for item in (job.payload or {}).get('items') or []
        }
        settings = onboarding_settings(db)
        worker_audit_event(
            db, job, 'vm.onboarding.started', 'onboarding_job', job.id,
            {'summary': {'selected': len(rows)}},
        )
        db.commit()

    completed = partial = failed = 0
    for index, row in enumerate(rows, start=1):
        context.check(force=True)
        config = configs.get(row.discovered_resource_id)
        if config is None:
            _job_item(context, row.id, status='FAILED', error='Onboarding payload is missing')
            failed += 1
            continue
        context.progress(
            ((index - 1) / max(1, len(rows))) * 100,
            f'Onboarding {row.name or row.external_id}',
            phase='onboarding',
        )
        attempts = settings['provider_retry_attempts']
        identity_id = None
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                if attempt > 1:
                    _job_item(context, row.id, status='RETRYING')
                    context.log(f'Retry {attempt}/{attempts} for {row.name or row.external_id}')
                identity_id, result = _process_item_once(context, row, config)
                _job_item(context, row.id, status='COMPLETED', error='', result=result, identity_id=identity_id)
                with session() as db:
                    job = db.get(Job, context.job.id)
                    bind_job_scope(db, job)
                    worker_audit_event(
                        db, job, 'vm.onboarding.completed', 'resource', result['resource_id'],
                        {
                            'onboarding': {
                                'job_id': job.id,
                                'identity_id': identity_id,
                                'mode': config.get('mode'),
                                'external_id': row.external_id,
                            }
                        },
                    )
                    db.commit()
                completed += 1
                last_error = None
                break
            except Cancelled:
                _job_item(
                    context, row.id,
                    status='PARTIAL' if identity_id else 'FAILED',
                    error='Cancellation requested',
                    identity_id=identity_id,
                )
                raise
            except Exception as exc:
                last_error = exc
                with session() as db:
                    job = db.get(Job, context.job.id)
                    bind_job_scope(db, job)
                    current = db.get(OnboardingJobItem, row.id)
                    if current and current.external_identity_id:
                        identity_id = current.external_identity_id
                    db.commit()
                if attempt < attempts and _retryable(exc):
                    time.sleep(settings['provider_retry_delay_seconds'])
                    continue
                break

        if last_error is not None:
            status = 'PARTIAL' if identity_id else 'FAILED'
            _job_item(context, row.id, status=status, error=str(last_error), identity_id=identity_id)
            if status == 'PARTIAL':
                partial += 1
            else:
                failed += 1
            with session() as db:
                job = db.get(Job, context.job.id)
                bind_job_scope(db, job)
                worker_audit_event(
                    db, job, 'vm.onboarding.failed', 'onboarding_job', job.id,
                    {
                        'onboarding': {
                            'external_id': row.external_id,
                            'identity_id': identity_id,
                            'partial': bool(identity_id),
                        },
                        'error': {'type': last_error.__class__.__name__},
                    },
                    result='failure',
                )
                db.commit()

    outcome = 'COMPLETED' if failed == 0 and partial == 0 else 'PARTIAL_SUCCESS'
    if completed == 0 and partial == 0 and failed:
        outcome = 'FAILED'
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        payload = dict(job.payload or {})
        payload['onboarding_outcome'] = outcome
        payload['onboarding_summary'] = {
            'selected': len(rows),
            'completed': completed,
            'partial': partial,
            'failed': failed,
        }
        job.payload = payload
        db.commit()
    context.progress(100, 'Onboarding finished', phase='complete')
    if outcome == 'FAILED':
        raise ExecutionFailed('All onboarding resources failed')


def _sync(context):
    with session() as db:
        job = db.get(Job, context.job.id)
        bind_job_scope(db, job)
        ids = [str(value) for value in (job.payload or {}).get('identity_ids') or []]
        if not ids and (job.payload or {}).get('identity_id'):
            ids = [str(job.payload['identity_id'])]
    failures = 0
    for index, identity_id in enumerate(ids, start=1):
        context.check()
        with session() as db:
            job = db.get(Job, context.job.id)
            bind_job_scope(db, job)
            identity = db.get(ResourceExternalIdentity, identity_id)
            if identity is None or identity.retired_at is not None:
                continue
            previous = db.get(ResourceSyncState, identity.id)
            previous_status = previous.status if previous else 'UNKNOWN'
            try:
                identity, state, normalized = refresh_identity(db, identity)
            except Exception:
                failures += 1
                continue
            if state.status == 'DRIFTED' and previous_status != 'DRIFTED':
                worker_audit_event(
                    db, job, 'vm.drift.detected', 'resource', identity.managed_vm_id or identity.managed_resource_id,
                    {'identity_id': identity.id, 'drift': state.drift_json},
                )
            if state.status == 'MISSING' and previous_status != 'MISSING':
                worker_audit_event(
                    db, job, 'vm.missing.detected', 'resource', identity.managed_vm_id or identity.managed_resource_id,
                    {'identity_id': identity.id, 'last_seen_at': identity.last_seen_at.isoformat()},
                )
            worker_audit_event(
                db, job, 'vm.sync.completed', 'resource', identity.managed_vm_id or identity.managed_resource_id,
                {'identity_id': identity.id, 'sync_status': state.status},
            )
            db.commit()
        context.progress(index / max(1, len(ids)) * 100, 'Resource synchronization', phase='sync')
    if failures == len(ids) and ids:
        raise ExecutionFailed('All resource synchronization attempts failed')


def execute(context):
    operation = context.job.operation
    if operation == 'onboarding.discovery':
        return _discovery(context)
    if operation == 'onboarding.import':
        return _import(context)
    if operation == 'onboarding.sync':
        return _sync(context)
    raise ExecutionFailed('Unsupported onboarding operation')
