"""HTTP API for Availability Plans and VM assignment."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.availability.models import AvailabilityAssignment, AvailabilityPlan
from app.availability.schemas import AvailabilityAssignmentInput, AvailabilityPlanInput
from app.availability import service
from app.database import get_db
from app.day2.service import create_action, public_action_request, resolve_target
from app.models import Job
from app.resource_scope.http import require
from app.security.core import audit


router = APIRouter(prefix='/availability-plans', tags=['availability'])


def plan_public(row: AvailabilityPlan):
    return {
        'id': row.id,
        'tenant_id': row.tenant_id,
        'project_id': row.project_id,
        'name': row.name,
        'description': row.description,
        'state': row.state,
        'group': row.group,
        'max_restart': row.max_restart,
        'max_relocate': row.max_relocate,
        'is_active': row.is_active,
        'created_by': row.created_by,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }


def assignment_public(db, row: AvailabilityAssignment):
    status, error = service.assignment_status(db, row)
    plan = db.get(AvailabilityPlan, row.plan_id)
    return {
        'id': row.id,
        'tenant_id': row.tenant_id,
        'project_id': row.project_id,
        'plan_id': row.plan_id,
        'plan_name': plan.name if plan is not None else (row.plan_snapshot or {}).get('name'),
        'deployment_id': row.deployment_id,
        'resource_id': row.resource_id,
        'plan_snapshot': row.plan_snapshot or {},
        'status': status,
        'job_id': row.job_id,
        'last_error': error,
        'last_applied_at': row.last_applied_at,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }


def _scope_filter(model, scope):
    return (
        model.tenant_id == scope.tenant_id,
        model.project_id == scope.project_id,
    )


@router.get('')
def list_plans(request: Request, active_only: bool = False, limit: int = Query(default=200, ge=1, le=200),
               actor=Depends(require('availability.read')), db=Depends(get_db, scope='function')):
    query = select(AvailabilityPlan).where(*_scope_filter(AvailabilityPlan, request.state.resource_scope))
    if active_only:
        query = query.where(AvailabilityPlan.is_active.is_(True))
    rows = db.scalars(query.order_by(AvailabilityPlan.name.asc()).limit(limit)).all()
    return {'items': [plan_public(row) for row in rows]}


@router.post('', status_code=201)
def create_plan(data: AvailabilityPlanInput, request: Request,
                actor=Depends(require('availability.create')), db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    exists = db.scalar(select(AvailabilityPlan.id).where(
        AvailabilityPlan.tenant_id == scope.tenant_id,
        AvailabilityPlan.project_id == scope.project_id,
        AvailabilityPlan.normalized_name == data.name.strip().casefold(),
    ).limit(1))
    if exists:
        raise HTTPException(409, 'Availability Plan with this name already exists in the selected project')
    row = AvailabilityPlan(
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        created_by=actor.user_id,
        normalized_name=data.name.strip().casefold(),
        **data.model_dump(),
    )
    row.name = row.name.strip()
    db.add(row)
    db.flush()
    audit(db, request, 'availability_plan.created', 'availability_plans', row.id)
    return plan_public(row)


@router.put('/{plan_id}')
def update_plan(plan_id: str, data: AvailabilityPlanInput, request: Request,
                actor=Depends(require('availability.update')), db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    row = service.scoped_plan(db, scope, plan_id)
    duplicate = db.scalar(select(AvailabilityPlan.id).where(
        AvailabilityPlan.tenant_id == scope.tenant_id,
        AvailabilityPlan.project_id == scope.project_id,
        AvailabilityPlan.id != row.id,
AvailabilityPlan.normalized_name == data.name.strip().casefold(),
    ).limit(1))
    if duplicate:
        raise HTTPException(409, 'Availability Plan with this name already exists in the selected project')
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    row.name = row.name.strip()
    row.normalized_name = row.name.casefold()
    audit(db, request, 'availability_plan.updated', 'availability_plans', row.id)
    db.flush()
    return plan_public(row)


@router.delete('/{plan_id}')
def delete_plan(plan_id: str, request: Request,
                actor=Depends(require('availability.delete')), db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    row = service.scoped_plan(db, scope, plan_id)
    assigned = db.scalar(select(AvailabilityAssignment.id).where(
        AvailabilityAssignment.plan_id == row.id,
    ).limit(1))
    if assigned:
        raise HTTPException(409, 'Availability Plan is assigned to a deployment or VM; disable it instead')
    db.delete(row)
    audit(db, request, 'availability_plan.deleted', 'availability_plans', row.id)
    return {'deleted': True}


@router.get('/resources/{resource_id}')
def resource_assignment(resource_id: str, request: Request,
                        actor=Depends(require('availability.read')), db=Depends(get_db, scope='function')):
    resolve_target(db, resource_id, request, actor)
    row = service.assignment_for_resource(db, request.state.resource_scope, resource_id)
    return {'assignment': assignment_public(db, row) if row is not None else None}


@router.put('/resources/{resource_id}')
def assign_resource(resource_id: str, data: AvailabilityAssignmentInput, request: Request,
                    actor=Depends(require('availability.assign')), db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    plan = service.scoped_plan(db, scope, data.plan_id, active_only=True)
    target, credential, _ = resolve_target(db, resource_id, request, actor)
    if target.provider_type != 'proxmox':
        raise HTTPException(422, 'Availability Plan currently supports Proxmox VM resources only')

    row = service.assignment_for_resource(db, scope, resource_id)
    if row is not None and row.job_id:
        current_job = db.get(Job, row.job_id)
        if current_job is not None and current_job.status in service.ACTIVE_JOB_STATES:
            raise HTTPException(409, 'An Availability Plan change is already running for this VM')
    if row is None:
        row = AvailabilityAssignment(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            resource_id=resource_id,
            deployment_id=target.deployment_id,
            plan_id=plan.id,
            plan_snapshot=service.plan_snapshot(plan),
            status='pending',
            created_by=actor.user_id,
        )
        db.add(row)
    else:
        row.plan_id = plan.id
        row.plan_snapshot = service.plan_snapshot(plan)
        row.status = 'pending'
        row.last_error = None
    db.flush()

    action, validation = create_action(
        db, request, actor, target, credential,
        'apply_availability', service.action_parameters(plan),
        'Apply Availability Plan: ' + plan.name,
        request.state.permissions,
    )
    row.job_id = action.job_id
    row.status = 'waiting_approval' if validation.get('approval_required') else 'queued'
    audit(db, request, 'availability.assigned', 'availability_assignments', row.id)
    db.flush()
    return {
        'assignment': assignment_public(db, row),
        'action': public_action_request(action),
    }
