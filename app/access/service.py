from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException

from app.access.groups import (
    disable_apmid_groups, ensure_apmid_groups, ensure_global_groups,
    ensure_organization_groups, ensure_project_groups,
)
from app.access.models import OrganizationAPMID
from app.models import Setting
from app.projects.models import Project
from app.tenancy.models import Tenant


LEO = 'LEO'


def normalize_apmid(value: str) -> str:
    code = str(value or '').strip().upper()
    if not code or any(not (ch.isalnum() or ch in {'-', '_'}) for ch in code):
        raise HTTPException(422, {'error': 'invalid_apmid'})
    return code


def apmid_public(row: OrganizationAPMID) -> dict:
    return {
        'id': row.id,
        'organization_id': row.organization_id,
        'code': row.code,
        'name': row.name or row.code,
        'description': row.description,
        'enabled': row.enabled,
        'is_system': row.is_system,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }


def _global_apmid_seed(db) -> list[str]:
    row = db.get(Setting, 'vm_classification')
    raw = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    result = [LEO]
    for value in raw.get('apmids') or []:
        code = str(value).strip().upper()
        if code and code not in result:
            result.append(code)
    return result


def ensure_apmid(db, organization: Tenant, code: str, *, created_by=None, is_system=False) -> OrganizationAPMID:
    code = normalize_apmid(code)
    row = db.scalar(select(OrganizationAPMID).where(
        OrganizationAPMID.organization_id == str(organization.id),
        OrganizationAPMID.code == code,
    ))
    if row is None:
        try:
            with db.begin_nested():
                row = OrganizationAPMID(
                    organization_id=str(organization.id), code=code, name=code,
                    description='', enabled=True, is_system=is_system or code == LEO,
                    created_by=created_by,
                )
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.scalar(select(OrganizationAPMID).where(
                OrganizationAPMID.organization_id == str(organization.id),
                OrganizationAPMID.code == code,
            ))
            if row is None:
                raise
    if code == LEO:
        row.is_system = True
        row.enabled = True
        row.name = row.name or LEO
    return row


def ensure_organization_access(db, organization: Tenant, *, created_by=None, seed_global_apmids=True) -> None:
    ensure_global_groups(db, created_by=created_by)
    ensure_organization_groups(db, organization, created_by=created_by)
    codes = _global_apmid_seed(db) if seed_global_apmids else [LEO]
    for code in codes:
        row = ensure_apmid(db, organization, code, created_by=created_by, is_system=(code == LEO))
        ensure_apmid_groups(db, organization, row.code, created_by=created_by)
    db.flush()


def ensure_project_access(db, project: Project, *, created_by=None) -> None:
    organization = db.get(Tenant, str(project.tenant_id))
    if organization is None:
        raise RuntimeError('Project organization is missing')
    ensure_project_groups(db, organization, project, created_by=created_by)
    db.flush()


def ensure_all_managed_access(db) -> None:
    ensure_global_groups(db)
    for organization in db.scalars(select(Tenant).where(Tenant.deleted_at.is_(None))).all():
        ensure_organization_access(db, organization, seed_global_apmids=False)
        for apmid in db.scalars(select(OrganizationAPMID).where(
            OrganizationAPMID.organization_id == organization.id,
        )).all():
            ensure_apmid_groups(db, organization, apmid.code)
    for project in db.scalars(select(Project).where(Project.deleted_at.is_(None))).all():
        ensure_project_access(db, project)
    db.flush()


def list_apmids(db, organization_id: str, *, include_disabled=False) -> list[OrganizationAPMID]:
    filters = [OrganizationAPMID.organization_id == str(organization_id)]
    if not include_disabled:
        filters.append(OrganizationAPMID.enabled.is_(True))
    return list(db.scalars(
        select(OrganizationAPMID).where(*filters)
        .order_by(OrganizationAPMID.is_system.desc(), OrganizationAPMID.code)
    ))


def create_apmid(db, organization: Tenant, data, *, created_by=None) -> OrganizationAPMID:
    code = normalize_apmid(data.code)
    if db.scalar(select(OrganizationAPMID.id).where(
        OrganizationAPMID.organization_id == str(organization.id), OrganizationAPMID.code == code,
    )):
        raise HTTPException(409, {'error': 'apmid_exists'})
    row = ensure_apmid(db, organization, code, created_by=created_by, is_system=(code == LEO))
    if data.name is not None:
        row.name = data.name.strip() or code
    row.description = data.description
    ensure_apmid_groups(db, organization, code, created_by=created_by)
    db.flush()
    return row


def update_apmid(db, organization: Tenant, code: str, data) -> OrganizationAPMID:
    row = get_apmid(db, organization.id, code)
    values = data.model_dump(exclude_unset=True)
    if row.is_system and values.get('enabled') is False:
        raise HTTPException(409, {'error': 'system_apmid_protected'})
    for field in ('name', 'description', 'enabled'):
        if field in values and values[field] is not None:
            setattr(row, field, values[field])
    if row.code == LEO:
        row.enabled = True
        row.is_system = True
    db.flush()
    return row


def delete_apmid(db, organization: Tenant, code: str) -> None:
    row = get_apmid(db, organization.id, code)
    if row.is_system or row.code == LEO:
        raise HTTPException(409, {'error': 'system_apmid_protected'})
    disable_apmid_groups(db, str(organization.id), row.code)
    db.delete(row)
    db.flush()


def get_apmid(db, organization_id: str, code: str) -> OrganizationAPMID:
    normalized = normalize_apmid(code)
    row = db.scalar(select(OrganizationAPMID).where(
        OrganizationAPMID.organization_id == str(organization_id),
        OrganizationAPMID.code == normalized,
    ))
    if row is None:
        raise HTTPException(404, {'error': 'apmid_not_found'})
    return row


def effective_project_apmids(db, organization_id: str, project_id: str | None = None) -> list[str]:
    codes = [row.code for row in list_apmids(db, organization_id)]
    if not project_id:
        return codes
    project = db.scalar(select(Project).where(
        Project.id == str(project_id), Project.tenant_id == str(organization_id), Project.deleted_at.is_(None),
    ))
    if project is None:
        raise HTTPException(404, {'error': 'resource_not_found'})
    allowed = getattr(project, 'allowed_apmids', None)
    if not allowed:
        return codes
    allowed_codes = {str(code).upper() for code in allowed}
    return [code for code in codes if code in allowed_codes]


def validate_apmid_scope(db, organization_id: str | None, project_id: str | None, code: str | None) -> None:
    if not code:
        return
    if not organization_id:
        raise HTTPException(422, {'error': 'organization_required_for_apmid'})
    normalized = normalize_apmid(code)
    row = db.scalar(select(OrganizationAPMID).where(
        OrganizationAPMID.organization_id == str(organization_id),
        OrganizationAPMID.code == normalized,
        OrganizationAPMID.enabled.is_(True),
    ))
    if row is None:
        raise HTTPException(422, {'error': 'apmid_not_available'})
    if project_id and normalized not in effective_project_apmids(db, organization_id, project_id):
        raise HTTPException(422, {'error': 'apmid_not_allowed_in_project'})
