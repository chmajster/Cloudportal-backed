"""Automatic metadata tags for Proxmox-managed virtual machines."""
from __future__ import annotations

import hashlib
import re

from app.database import session

MAX_PROXMOX_MANAGEMENT_TAG_LENGTH = 63
_RESERVED_PREFIXES = ('tenant-', 'project-', 'deployment-', 'blueprint-')


def _management_tag(prefix, value):
    """Return a Proxmox-safe, bounded management tag."""
    normalized = re.sub(r'[^a-z0-9_.-]+', '-', str(value or '').strip().lower()).strip('-.')
    if not normalized:
        return None

    tag = f'{prefix}-{normalized}'
    if len(tag) <= MAX_PROXMOX_MANAGEMENT_TAG_LENGTH:
        return tag

    digest = hashlib.sha256(normalized.encode()).hexdigest()[:8]
    available = MAX_PROXMOX_MANAGEMENT_TAG_LENGTH - len(prefix) - len(digest) - 2
    compact = normalized[:available].rstrip('-.')
    return f'{prefix}-{compact}-{digest}'


def _is_reserved_management_tag(tag):
    normalized = str(tag or '').strip().lower()
    return (
        normalized == 'managed-by-cloudportal'
        or normalized.startswith(_RESERVED_PREFIXES)
    )


def build_proxmox_management_tags(deployment, *, tenant_slug=None, project_slug=None):
    """Merge user/classification tags with authoritative CloudPortal ownership metadata."""
    existing = [
        str(tag).strip()
        for tag in ((deployment.variables or {}).get('tags') or [])
        if str(tag).strip() and not _is_reserved_management_tag(tag)
    ]
    blueprint = ((deployment.workflow or {}).get('blueprint') or {})

    automatic = [
        'managed-by-cloudportal',
        _management_tag('tenant', tenant_slug or getattr(deployment, 'tenant_id', None)),
        _management_tag('project', project_slug or getattr(deployment, 'project_id', None)),
        _management_tag('deployment', getattr(deployment, 'id', None)),
    ]
    if blueprint:
        automatic.append(_management_tag('blueprint', blueprint.get('slug') or blueprint.get('id')))

    # Proxmox sorts tags itself. Sorting here keeps tfvars/logs deterministic too.
    return sorted({tag for tag in [*existing, *automatic] if tag})


def proxmox_management_tags(deployment):
    """Resolve tenant/project slugs and build tags for one Proxmox deployment."""
    from app.projects.models import Project
    from app.tenancy.models import Tenant

    tenant_slug = None
    project_slug = None
    with session() as db:
        tenant_id = getattr(deployment, 'tenant_id', None)
        project_id = getattr(deployment, 'project_id', None)
        tenant = db.get(Tenant, tenant_id) if tenant_id else None
        project = db.get(Project, project_id) if project_id else None
        tenant_slug = tenant.slug if tenant is not None else None
        project_slug = project.slug if project is not None else None

    return build_proxmox_management_tags(
        deployment,
        tenant_slug=tenant_slug,
        project_slug=project_slug,
    )
