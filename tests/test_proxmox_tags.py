from types import SimpleNamespace

from app.terraform.tags import (
    MAX_PROXMOX_MANAGEMENT_TAG_LENGTH,
    build_proxmox_management_tags,
)


def deployment(*, tags=None, blueprint=None):
    return SimpleNamespace(
        id='11111111-2222-3333-4444-555555555555',
        tenant_id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        project_id='ffffffff-1111-2222-3333-444444444444',
        variables={'tags': list(tags or [])},
        workflow={'blueprint': blueprint} if blueprint else {},
    )


def test_proxmox_management_tags_add_scope_and_preserve_classification():
    row = deployment(
        tags=[
            'custom',
            'apmid-leo',
            'env-dev',
            'tenant-stale',
            'project-stale',
            'deployment-stale',
            'blueprint-stale',
        ],
        blueprint={'id': 42, 'slug': 'ubuntu-standard'},
    )

    tags = build_proxmox_management_tags(
        row,
        tenant_slug='acme',
        project_slug='payments',
    )

    assert tags == sorted([
        'apmid-leo',
        'blueprint-ubuntu-standard',
        'custom',
        'deployment-11111111-2222-3333-4444-555555555555',
        'env-dev',
        'managed-by-cloudportal',
        'project-payments',
        'tenant-acme',
    ])


def test_proxmox_management_tags_cover_direct_deployments():
    row = deployment(tags=['custom'])

    tags = build_proxmox_management_tags(
        row,
        tenant_slug='default',
        project_slug='platform',
    )

    assert 'managed-by-cloudportal' in tags
    assert 'tenant-default' in tags
    assert 'project-platform' in tags
    assert 'deployment-11111111-2222-3333-4444-555555555555' in tags
    assert not any(tag.startswith('blueprint-') for tag in tags)


def test_proxmox_management_tags_bound_long_scope_slugs_deterministically():
    row = deployment()
    first = build_proxmox_management_tags(
        row,
        tenant_slug='tenant-' + ('x' * 100),
        project_slug='project-' + ('y' * 100),
    )
    second = build_proxmox_management_tags(
        row,
        tenant_slug='tenant-' + ('x' * 100),
        project_slug='project-' + ('y' * 100),
    )

    assert first == second
    managed_scope_tags = [
        tag for tag in first if tag.startswith(('tenant-', 'project-'))
    ]
    assert managed_scope_tags
    assert all(len(tag) <= MAX_PROXMOX_MANAGEMENT_TAG_LENGTH for tag in managed_scope_tags)
