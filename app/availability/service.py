"""Availability Plan business logic and provider reconciliation."""
from fastapi import HTTPException
from sqlalchemy import select

from app.availability.models import AvailabilityAssignment, AvailabilityPlan
from app.database import session
from app.executors.base import ExecutionFailed
from app.models import Audit, Job, ManagedVM, now
from app.providers.registry import provider_for


ACTIVE_JOB_STATES = {'queued', 'running', 'waiting_approval'}


def scoped_plan(db, scope, plan_id: str, *, active_only: bool = False) -> AvailabilityPlan:
    query = select(AvailabilityPlan).where(
        AvailabilityPlan.id == str(plan_id),
        AvailabilityPlan.tenant_id == scope.tenant_id,
        AvailabilityPlan.project_id == scope.project_id,
    )
    if active_only:
        query = query.where(AvailabilityPlan.is_active.is_(True))
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, 'Availability Plan not found in the selected project')
    return row


def plan_snapshot(plan: AvailabilityPlan) -> dict:
    return {
        'plan_id': plan.id,
        'name': plan.name,
        'state': plan.state,
        'group': plan.group,
        'max_restart': int(plan.max_restart),
        'max_relocate': int(plan.max_relocate),
    }


def action_parameters(plan: AvailabilityPlan) -> dict:
    snapshot = plan_snapshot(plan)
    return {
        'plan_id': snapshot['plan_id'],
        'state': snapshot['state'],
        'group': snapshot['group'],
        'max_restart': snapshot['max_restart'],
        'max_relocate': snapshot['max_relocate'],
    }


def assignment_for_resource(db, scope, resource_id: str):
    return db.scalar(select(AvailabilityAssignment).where(
        AvailabilityAssignment.resource_id == str(resource_id),
        AvailabilityAssignment.tenant_id == scope.tenant_id,
        AvailabilityAssignment.project_id == scope.project_id,
    ))


def assignment_for_deployment(db, deployment_id: str):
    return db.scalar(select(AvailabilityAssignment).where(
        AvailabilityAssignment.deployment_id == str(deployment_id),
    ))


def assignment_status(db, row: AvailabilityAssignment) -> tuple[str, str | None]:
    status = row.status
    error = row.last_error
    if row.job_id:
        job = db.get(Job, row.job_id)
        if job is not None:
            if job.status == 'successful':
                status, error = 'applied', None
            elif job.status in {'failed', 'cancelled'}:
                status, error = job.status, job.error
            elif job.status in ACTIVE_JOB_STATES:
                status = job.status
    return status, error


def attach_to_deployment(db, scope, deployment, plan_id: str, user_id: int) -> AvailabilityAssignment:
    plan = scoped_plan(db, scope, plan_id, active_only=True)
    row = assignment_for_deployment(db, deployment.id)
    if row is None:
        row = AvailabilityAssignment(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            deployment_id=deployment.id,
            plan_id=plan.id,
            plan_snapshot=plan_snapshot(plan),
            status='pending',
            created_by=user_id,
        )
        db.add(row)
    else:
        row.plan_id = plan.id
        row.plan_snapshot = plan_snapshot(plan)
        row.status = 'pending'
        row.job_id = None
        row.last_error = None
        row.last_applied_at = None
    db.flush()
    return row


def mark_assignment_applied(db, resource_id: str, plan_id: str, *, job_id: str | None = None):
    row = db.scalar(select(AvailabilityAssignment).where(
        AvailabilityAssignment.resource_id == str(resource_id),
    ).with_for_update())
    if row is None or row.plan_id != str(plan_id):
        return None
    row.status = 'applied'
    row.job_id = job_id or row.job_id
    row.last_error = None
    row.last_applied_at = now()
    return row


def apply_pending_for_deployment(context):
    """Apply the immutable plan snapshot inside the provisioning job after inventory exists."""
    deployment_id = str(context.deployment.id)
    with session() as db:
        assignment = db.scalar(select(AvailabilityAssignment).where(
            AvailabilityAssignment.deployment_id == deployment_id,
        ).with_for_update())
        if assignment is None:
            return None
        vm = db.scalar(select(ManagedVM).where(ManagedVM.deployment_id == deployment_id))
        if vm is None:
            raise ExecutionFailed('Availability Plan is assigned but the managed VM is missing from inventory')
        if assignment.status == 'applied' and assignment.resource_id == vm.id:
            return assignment.id
        snapshot = dict(assignment.plan_snapshot or {})
        assignment.resource_id = vm.id
        assignment.status = 'applying'
        assignment.last_error = None
        assignment_id = assignment.id
        vm_id = vm.vm_id
        db.commit()

    if context.credential is None or context.credential.type != 'proxmox':
        with session() as db:
            row = db.get(AvailabilityAssignment, assignment_id)
            if row is not None:
                row.status = 'failed'
                row.last_error = 'Availability Plan currently requires Proxmox'
                db.commit()
        raise ExecutionFailed('Availability Plan currently requires Proxmox')

    context.stage('availability.plan.apply')
    adapter = provider_for(context.credential)
    try:
        adapter.set_vm_ha(
            vm_id,
            state=str(snapshot.get('state') or 'started'),
            group=snapshot.get('group'),
            max_restart=int(snapshot.get('max_restart', 1)),
            max_relocate=int(snapshot.get('max_relocate', 1)),
            comment='CloudPortal Availability Plan: ' + str(snapshot.get('name') or snapshot.get('plan_id') or ''),
        )
    except Exception as exc:
        with session() as db:
            row = db.get(AvailabilityAssignment, assignment_id)
            if row is not None:
                row.status = 'failed'
                row.last_error = 'Provider rejected Availability Plan application'
                db.commit()
        raise ExecutionFailed('Availability Plan could not be applied in Proxmox HA') from exc

    with session() as db:
        row = db.get(AvailabilityAssignment, assignment_id)
        if row is None:
            raise ExecutionFailed('Availability assignment disappeared during provisioning')
        row.status = 'applied'
        row.last_error = None
        row.last_applied_at = now()
        db.add(Audit(
            user_id=context.job.created_by,
            token_id=context.job.token_id,
            ip=context.job.ip,
            source=context.job.source,
            action='availability.applied',
            resource='availability_assignments',
            resource_id=row.id,
            result='successful',
            request_id=context.job.request_id,
        ))
        db.commit()
    context.log('availability.plan.applied: ' + str(snapshot.get('name') or snapshot.get('plan_id')))
    return assignment_id
