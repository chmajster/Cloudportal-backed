"""HTTP quota scope, RBAC and limit lifecycle regression tests."""
from app.database import session
from app.quotas.service import QuotaDelta, mark_uncertain, reserve
from app.resource_scope.authorization import Scope
from conftest import new_user


def create_scope(client, headers, slug):
    tenant = client.post('/api/v1/tenants', headers=headers, json={'name': slug, 'slug': slug})
    assert tenant.status_code == 201, tenant.text
    project = client.post('/api/v1/projects', headers=headers, json={
        'tenant_id': tenant.json()['id'], 'name': slug, 'slug': slug,
    })
    assert project.status_code == 201, project.text
    return Scope(tenant.json()['id'], project.json()['id'])


def scope_headers(headers, scope):
    return {
        **headers,
        'X-Tenant-ID': scope.tenant_id,
        'X-Project-ID': scope.project_id,
    }


def test_quota_limit_can_return_to_unlimited_and_rbac_is_separated(system):
    client, headers, _ = system

    updated = client.put('/api/v1/quotas/project/vm_count', headers=headers, json={'limit': 5})
    assert updated.status_code == 200, updated.text
    snapshot = client.get('/api/v1/quotas', headers=headers).json()
    assert next(item for item in snapshot['items'] if item['dimension'] == 'vm_count')['project_limit'] == 5

    removed = client.delete('/api/v1/quotas/project/vm_count', headers=headers)
    assert removed.status_code == 200 and removed.json()['deleted'] is True
    snapshot = client.get('/api/v1/quotas', headers=headers).json()
    assert next(item for item in snapshot['items'] if item['dimension'] == 'vm_count')['project_limit'] is None

    _, reader = new_user(client, headers, username='quota-reader', permissions=['quotas.read'])
    assert client.get('/api/v1/quotas', headers=reader).status_code == 200
    assert client.put('/api/v1/quotas/project/vm_count', headers=reader, json={'limit': 1}).status_code == 403
    assert client.delete('/api/v1/quotas/project/vm_count', headers=reader).status_code == 403

    _, manager = new_user(client, headers, username='quota-manager', permissions=['quotas.read', 'quotas.manage'])
    assert client.put('/api/v1/quotas/project/vm_count', headers=manager, json={'limit': 2}).status_code == 200
    assert client.put('/api/v1/quotas/tenant/vm_count', headers=manager, json={'limit': 2}).status_code == 403


def test_quota_reservations_are_scope_isolated(system):
    client, headers, _ = system
    other = create_scope(client, headers, 'quota-other')

    with session() as db:
        row = reserve(
            db,
            QuotaDelta(other, 'deployment', 'other-deployment', 'terraform.apply', {'vm_count': 1}),
            'quota-http-other',
            1,
        )
        mark_uncertain(db, row.id)
        reservation_id = row.id
        db.commit()

    default_items = client.get('/api/v1/quotas/reservations', headers=headers)
    assert default_items.status_code == 200
    assert reservation_id not in {item['id'] for item in default_items.json()['items']}

    hidden = client.post(
        f'/api/v1/quotas/reservations/{reservation_id}/reconcile',
        headers=headers,
        json={'outcome': 'release'},
    )
    assert hidden.status_code == 404

    visible = client.post(
        f'/api/v1/quotas/reservations/{reservation_id}/reconcile',
        headers=scope_headers(headers, other),
        json={'outcome': 'release'},
    )
    assert visible.status_code == 200, visible.text
    assert visible.json()['status'] == 'released'
