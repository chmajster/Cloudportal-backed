from sqlalchemy import select, update

from app.models import HostnameReservation, IPAllocation, now


def release_pre_execution_allocations(db, deployment_id):
    released_at = now()
    db.execute(update(HostnameReservation).where(
        HostnameReservation.resource_id == deployment_id,
        HostnameReservation.status.in_(['reserved', 'assigned']),
    ).values(status='released', released_at=released_at))
    db.execute(update(IPAllocation).where(
        IPAllocation.resource_id == deployment_id,
        IPAllocation.status.in_(['reserved', 'assigned']),
    ).values(status='released', released_at=released_at))


def has_released_allocations(db, deployment_id):
    hostname = db.scalar(select(HostnameReservation.id).where(
        HostnameReservation.resource_id == deployment_id,
        HostnameReservation.status == 'released',
    ).limit(1))
    if hostname:
        return True
    return bool(db.scalar(select(IPAllocation.id).where(
        IPAllocation.resource_id == deployment_id,
        IPAllocation.status == 'released',
    ).limit(1)))
