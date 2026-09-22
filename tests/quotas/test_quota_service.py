from fastapi import HTTPException

from app.database import session
from app.models import Deployment
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.quotas.models import ProjectQuotaUsage, QuotaAllocation, QuotaReservation
from app.quotas.service import (
    QuotaDelta,
    commit_reservation,
    deployment_dimensions,
    deployment_unresolved_dimensions,
    mark_uncertain,
    quota_snapshot,
    reconcile_reservation,
    release_reservation,
    reserve,
    set_project_limit,
)
from app.resource_scope.authorization import Scope
from app.tenancy.permissions import DEFAULT_TENANT_ID


SCOPE = Scope(DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID)


def item(snapshot, dimension):
    return next(row for row in snapshot['items'] if row['dimension'] == dimension)


def test_reservation_commit_and_confirmed_release(system):
    with session() as db:
        set_project_limit(db, SCOPE, 'vm_count', 2)
        create = reserve(
            db, QuotaDelta(SCOPE, 'deployment', 'quota-test-vm', 'terraform.apply', {'vm_count': 1}),
            'quota-test-create', 1,
        )
        assert item(quota_snapshot(db, SCOPE), 'vm_count')['project_reserved'] == 1
        commit_reservation(db, create.id)
        assert item(quota_snapshot(db, SCOPE), 'vm_count')['project_used'] == 1
        allocation = db.query(QuotaAllocation).filter_by(subject_id='quota-test-vm').one()
        assert allocation.dimensions == {'vm_count': 1}

        destroy = reserve(
            db, QuotaDelta(SCOPE, 'deployment', 'quota-test-vm', 'terraform.destroy', {'vm_count': -1}),
            'quota-test-destroy', 1,
        )
        # Negative deltas do not release committed usage before provider confirmation.
        assert item(quota_snapshot(db, SCOPE), 'vm_count')['project_used'] == 1
        commit_reservation(db, destroy.id)
        assert item(quota_snapshot(db, SCOPE), 'vm_count')['project_used'] == 0
        assert db.query(QuotaAllocation).filter_by(subject_id='quota-test-vm').one_or_none() is None
        db.commit()


def test_released_runtime_approval_reservation_can_be_rearmed(system):
    with session() as db:
        set_project_limit(db, SCOPE, 'vm_count', 1)
        quota = QuotaDelta(SCOPE, 'deployment', 'approval-vm', 'terraform.apply', {'vm_count': 1})
        first = reserve(db, quota, 'job:approval-test', 1)
        first_id = first.id
        release_reservation(db, first.id)
        assert item(quota_snapshot(db, SCOPE), 'vm_count')['project_reserved'] == 0

        second = reserve(db, quota, 'job:approval-test', 1)
        assert second.id == first_id
        assert second.status == 'reserved'
        assert item(quota_snapshot(db, SCOPE), 'vm_count')['project_reserved'] == 1
        db.commit()


def test_uncertain_reservation_holds_capacity_until_explicit_reconciliation(system):
    with session() as db:
        set_project_limit(db, SCOPE, 'vm_count', 1)
        row = reserve(
            db, QuotaDelta(SCOPE, 'deployment', 'uncertain-vm', 'terraform.apply', {'vm_count': 1}),
            'quota-test-uncertain', 1,
        )
        mark_uncertain(db, row.id)
        assert row.status == 'uncertain'
        assert row.reconciliation_required is True
        try:
            reserve(
                db, QuotaDelta(SCOPE, 'deployment', 'second-vm', 'terraform.apply', {'vm_count': 1}),
                'quota-test-second', 1,
            )
        except HTTPException as error:
            assert error.detail['code'] == 'PROJECT_QUOTA_EXCEEDED'
        else:
            raise AssertionError('uncertain reservation must continue consuming reserved quota')

        reconcile_reservation(db, row.id, 'release')
        assert row.status == 'released'
        assert item(quota_snapshot(db, SCOPE), 'vm_count')['project_reserved'] == 0
        db.commit()



def test_provider_dimension_normalization_is_explicit_and_fail_closed():
    proxmox = Deployment(provider='proxmox', variables={'cpu': 4, 'memory': 8192, 'disk': 80})
    vmware = Deployment(provider='vmware', variables={'cpu': 6, 'memory': 12288, 'disk': 120})
    aws = Deployment(provider='aws', variables={'instance_type': 't3.large', 'root_volume_size': 40})
    azure = Deployment(provider='azure', variables={'vm_size': 'Standard_B2s', 'os_disk_size_gb': 64})
    openstack = Deployment(provider='openstack', variables={'flavor_name': 'm1.medium'})

    assert deployment_dimensions(proxmox) == {'vm_count': 1, 'vcpu': 4, 'memory_mb': 8192, 'disk_gib': 80}
    assert deployment_dimensions(vmware) == {'vm_count': 1, 'vcpu': 6, 'memory_mb': 12288, 'disk_gib': 120}
    assert deployment_dimensions(aws) == {'vm_count': 1, 'disk_gib': 40}
    assert deployment_dimensions(azure) == {'vm_count': 1, 'disk_gib': 64}
    assert deployment_dimensions(openstack) == {'vm_count': 1}

    assert deployment_unresolved_dimensions(proxmox) == set()
    assert deployment_unresolved_dimensions(vmware) == set()
    assert deployment_unresolved_dimensions(aws) == {'vcpu', 'memory_mb'}
    assert deployment_unresolved_dimensions(azure) == {'vcpu', 'memory_mb'}
    assert deployment_unresolved_dimensions(openstack) == {'vcpu', 'memory_mb', 'disk_gib'}
