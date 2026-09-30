"""Write-through compatibility from legacy grants into canonical RoleAssignment rows."""
from collections import defaultdict

from sqlalchemy import delete, select

from app.iam.models import RoleAssignment
from app.models import Permission, RolePermission, UserRole
from app.projects.models import ProjectMembership, ProjectRoleAssignment, ProjectRoleGrant
from app.tenancy.models import TenantMembership, TenantRoleAssignment, TenantRoleGrant


def _ceiling(db, assignment_id: str, grant_model) -> list[str]:
    return sorted(set(db.scalars(
        select(Permission.name)
        .join(grant_model, grant_model.permission_id == Permission.id)
        .where(grant_model.assignment_id == assignment_id)
    )))


def sync_global_user_bindings(db, user_id: int, *, created_by: int | None = None) -> None:
    subject_id = str(user_id)
    db.execute(delete(RoleAssignment).where(
        RoleAssignment.subject_type == 'USER',
        RoleAssignment.subject_id == subject_id,
        RoleAssignment.scope_type == 'GLOBAL',
        RoleAssignment.source == 'MIGRATION',
        RoleAssignment.source_ref.like(f'user_roles:{user_id}:%'),
    ))
    for role_id in sorted(set(db.scalars(
        select(UserRole.role_id).where(UserRole.user_id == user_id)
    ))):
        db.add(RoleAssignment(
            subject_type='USER', subject_id=subject_id, role_id=role_id,
            effect='ALLOW', scope_type='GLOBAL', inherit=True,
            conditions={}, permission_ceiling=None, approval_required=False,
            enabled=True, source='MIGRATION',
            source_ref=f'user_roles:{user_id}:{role_id}', created_by=created_by,
        ))
    db.flush()


def sync_organization_user_bindings(
    db, organization_id: str, user_id: int, *, created_by: int | None = None,
) -> None:
    organization_id = str(organization_id)
    subject_id = str(user_id)
    membership = db.get(TenantMembership, (organization_id, user_id))
    active = membership is not None and membership.status == 'active'
    db.execute(delete(RoleAssignment).where(
        RoleAssignment.subject_type == 'USER',
        RoleAssignment.subject_id == subject_id,
        RoleAssignment.scope_type == 'ORGANIZATION',
        RoleAssignment.tenant_id == organization_id,
        RoleAssignment.source == 'MIGRATION',
    ))
    rows = db.scalars(select(TenantRoleAssignment).where(
        TenantRoleAssignment.tenant_id == organization_id,
        TenantRoleAssignment.user_id == user_id,
    )).all()
    for row in rows:
        db.add(RoleAssignment(
            subject_type='USER', subject_id=subject_id, role_id=row.role_id,
            effect='ALLOW', scope_type='ORGANIZATION', scope_id=organization_id,
            tenant_id=organization_id, inherit=True, conditions={},
            permission_ceiling=_ceiling(db, row.id, TenantRoleGrant),
            approval_required=False, enabled=active, source='MIGRATION',
            source_ref=f'organization:{row.id}', created_by=created_by or row.created_by,
        ))
    db.flush()


def sync_project_user_bindings(
    db, organization_id: str, project_id: str, user_id: int, *, created_by: int | None = None,
) -> None:
    organization_id, project_id = str(organization_id), str(project_id)
    subject_id = str(user_id)
    membership = db.get(ProjectMembership, (project_id, user_id))
    active = membership is not None and membership.status == 'active'
    db.execute(delete(RoleAssignment).where(
        RoleAssignment.subject_type == 'USER',
        RoleAssignment.subject_id == subject_id,
        RoleAssignment.scope_type == 'PROJECT',
        RoleAssignment.project_id == project_id,
        RoleAssignment.source == 'MIGRATION',
    ))
    rows = db.scalars(select(ProjectRoleAssignment).where(
        ProjectRoleAssignment.project_id == project_id,
        ProjectRoleAssignment.user_id == user_id,
    )).all()
    for row in rows:
        db.add(RoleAssignment(
            subject_type='USER', subject_id=subject_id, role_id=row.role_id,
            effect='ALLOW', scope_type='PROJECT', scope_id=project_id,
            tenant_id=organization_id, project_id=project_id, inherit=True,
            conditions={}, permission_ceiling=_ceiling(db, row.id, ProjectRoleGrant),
            approval_required=False, enabled=active, source='MIGRATION',
            source_ref=f'project:{row.id}', created_by=created_by or row.created_by,
        ))
    db.flush()
