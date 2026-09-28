from collections import defaultdict
from datetime import timedelta
import uuid

from sqlalchemy import select

from app.database import session
from app.models import Job, Provider, User, now
from app.onboarding.models import (DiscoverySession, OnboardingSchedule,
                                   ResourceExternalIdentity, ResourceSyncState)
from app.operations.service import scheduler_user_permissions
from app.resource_scope.authorization import Scope, permissions_for_identity
from app.resource_scope.database import bind_scope, reference_visible
from app.security.core import redis_client
from app.tenancy.authorization import Identity


_MATERIALIZE_LOCK = 'cp:onboarding:materialize'


def _scheduler_identity(db, user_id, scope):
    permissions = scheduler_user_permissions(db, user_id)
    user = db.get(User, user_id)
    if permissions is None or user is None:
        return None, frozenset()
    actor = Identity(user.id, 0, frozenset(permissions), None)
    scoped = permissions_for_identity(db, actor, scope, write=True)
    return actor, frozenset(scoped)


def _advance_schedule(row, current):
    interval = max(300, int(row.interval_seconds or 3600))
    next_run = row.next_run_at
    while next_run <= current:
        next_run += timedelta(seconds=interval)
    row.next_run_at = next_run
    row.last_run_at = current


def _materialize_discovery_schedule(schedule_id):
    current = now()
    with session() as db:
        row = db.scalar(
            select(OnboardingSchedule)
            .where(
                OnboardingSchedule.id == schedule_id,
                OnboardingSchedule.is_active.is_(True),
                OnboardingSchedule.next_run_at <= current,
            )
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return False

        scope = Scope(row.tenant_id, row.project_id)
        actor, permissions = _scheduler_identity(db, row.created_by, scope)
        bind_scope(db, scope)
        if actor is None or 'vm.discovery.read' not in permissions:
            row.is_active = False
            row.last_error = 'Schedule owner no longer has vm.discovery.read in this project'
            db.commit()
            return False

        provider = db.get(Provider, row.provider_id)
        if (
            provider is None
            or not reference_visible(db, 'provider', row.provider_id, scope)
            or not reference_visible(db, 'credential', provider.credentials_id, scope)
        ):
            row.is_active = False
            row.last_error = 'Scheduled discovery provider access has been revoked'
            db.commit()
            return False

        automatic_inventory_import = bool(row.automatic_inventory_import)
        if automatic_inventory_import and not {
            'vm.onboarding.create', 'vm.onboarding.bulk'
        } <= set(permissions):
            automatic_inventory_import = False
            row.last_error = (
                'Automatic inventory import skipped: schedule owner requires '
                'vm.onboarding.create and vm.onboarding.bulk'
            )
        else:
            row.last_error = None

        discovery = DiscoverySession(
            provider_id=row.provider_id,
            status='QUEUED',
            scope_json=dict(row.discovery_scope_json or {}),
            filters_json=dict(row.filters_json or {}),
            created_by=row.created_by,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
        )
        db.add(discovery)
        db.flush()
        job = Job(
            id=str(uuid.uuid4()),
            operation='onboarding.discovery',
            payload={
                'session_id': discovery.id,
                'provider_id': row.provider_id,
                'schedule_id': row.id,
                'auto_classification': bool(row.auto_classification),
                'automatic_inventory_import': automatic_inventory_import,
            },
            created_by=row.created_by,
            token_id=None,
            request_id=str(uuid.uuid4()),
            ip='',
            source='Scheduler',
            tenant_id=row.tenant_id,
            project_id=row.project_id,
        )
        db.add(job)
        db.flush()
        discovery.job_id = job.id
        _advance_schedule(row, current)
        db.commit()
        return True


def _sync_groups(limit=200):
    current = now()
    with session() as db:
        rows = db.execute(
            select(
                ResourceSyncState.identity_id,
                ResourceExternalIdentity.tenant_id,
                ResourceExternalIdentity.project_id,
                ResourceExternalIdentity.onboarded_by,
            )
            .join(
                ResourceExternalIdentity,
                ResourceExternalIdentity.id == ResourceSyncState.identity_id,
            )
            .where(
                ResourceExternalIdentity.retired_at.is_(None),
                ResourceSyncState.next_sync_at.is_not(None),
                ResourceSyncState.next_sync_at <= current,
            )
            .order_by(ResourceSyncState.next_sync_at.asc())
            .limit(limit)
        ).all()
    groups = defaultdict(list)
    for identity_id, tenant_id, project_id, onboarded_by in rows:
        if onboarded_by:
            groups[(tenant_id, project_id, int(onboarded_by))].append(str(identity_id))
    return groups


def _materialize_sync_group(key, identity_ids):
    tenant_id, project_id, user_id = key
    scope = Scope(tenant_id, project_id)
    current = now()
    with session() as db:
        actor, permissions = _scheduler_identity(db, user_id, scope)
        bind_scope(db, scope)
        if actor is None or 'vm.onboarding.manage' not in permissions:
            # Do not disable synchronization permanently. Leave a visible
            # UNREACHABLE state and retry later if authorization is restored.
            rows = db.scalars(select(ResourceSyncState).where(
                ResourceSyncState.identity_id.in_(identity_ids)
            )).all()
            for state in rows:
                state.status = 'UNREACHABLE'
                state.last_error = 'Background sync owner no longer has vm.onboarding.manage'
                state.next_sync_at = current + timedelta(minutes=15)
            db.commit()
            return False

        visible = []
        for identity_id in identity_ids:
            identity = db.get(ResourceExternalIdentity, identity_id)
            if identity is None or identity.retired_at is not None:
                continue
            provider = db.get(Provider, identity.provider_id)
            if (
                provider is None
                or not reference_visible(db, 'provider', provider.id, scope)
                or not reference_visible(db, 'credential', provider.credentials_id, scope)
            ):
                state = db.get(ResourceSyncState, identity.id)
                if state:
                    state.status = 'UNREACHABLE'
                    state.last_error = 'Background sync provider access has been revoked'
                    state.next_sync_at = current + timedelta(minutes=15)
                continue
            visible.append(identity.id)

        if not visible:
            db.commit()
            return False

        job = Job(
            id=str(uuid.uuid4()),
            operation='onboarding.sync',
            payload={'identity_ids': visible, 'scheduled': True},
            created_by=user_id,
            token_id=None,
            request_id=str(uuid.uuid4()),
            ip='',
            source='Scheduler',
            tenant_id=tenant_id,
            project_id=project_id,
        )
        db.add(job)
        interval = 300
        for identity_id in visible:
            state = db.get(ResourceSyncState, identity_id)
            if state:
                state.next_sync_at = current + timedelta(seconds=interval)
        db.commit()
        return True


def materialize_onboarding_jobs():
    try:
        if not redis_client().set(_MATERIALIZE_LOCK, '1', nx=True, ex=20):
            return {'skipped': True}
    except Exception:
        return {'skipped': True}

    current = now()
    with session() as db:
        schedule_ids = list(db.scalars(
            select(OnboardingSchedule.id)
            .where(
                OnboardingSchedule.is_active.is_(True),
                OnboardingSchedule.next_run_at <= current,
            )
            .order_by(OnboardingSchedule.next_run_at.asc())
            .limit(100)
        ).all())

    discovery_jobs = sum(bool(_materialize_discovery_schedule(value)) for value in schedule_ids)
    sync_jobs = sum(
        bool(_materialize_sync_group(key, values))
        for key, values in _sync_groups().items()
    )
    return {'discovery_jobs': discovery_jobs, 'sync_jobs': sync_jobs}
