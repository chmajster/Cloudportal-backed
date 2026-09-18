import ipaddress

from fastapi import HTTPException
from sqlalchemy import select

from app.models import IPAllocation, IPPool


POOL_FIELDS = (
    'id name cidr gateway dns_servers excluded_addresses next_offset is_active '
    'created_by created_at updated_at'
)
ALLOCATION_FIELDS = (
    'id pool_id address prefix_length gateway hostname status resource_id '
    'created_by created_at updated_at released_at'
)


def as_public(row, fields):
    return {name: getattr(row, name) for name in fields.split()}


def pool_public(row):
    return as_public(row, POOL_FIELDS)


def allocation_public(row):
    return as_public(row, ALLOCATION_FIELDS)


def network_for(pool):
    try:
        network = ipaddress.ip_network(pool.cidr, strict=True)
    except ValueError:
        raise HTTPException(500, 'Stored IPAM pool is invalid') from None
    if network.version != 4:
        raise HTTPException(422, 'Only IPv4 pools are currently supported')
    return network


def excluded(pool):
    values = []
    for raw in pool.excluded_addresses or []:
        try:
            values.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                raise HTTPException(500, 'Stored IPAM exclusion is invalid') from None
            values.append(ipaddress.ip_network(f'{address}/{address.max_prefixlen}', strict=False))
    return values


def is_excluded(pool, address):
    if pool.gateway and str(address) == pool.gateway:
        return True
    return any(address in item for item in excluded(pool))


def active_address_exists(db, pool_id, address):
    return bool(db.scalar(select(IPAllocation.id).where(
        IPAllocation.pool_id == pool_id,
        IPAllocation.address == str(address),
        IPAllocation.status.in_(['reserved', 'assigned']),
    ).limit(1)))


def _valid_candidate(pool, network, address):
    return address in network and address != network.network_address and address != network.broadcast_address and not is_excluded(pool, address)


def allocate_address(db, pool_id, actor_id, *, preferred_address=None, hostname=None):
    pool = db.scalar(select(IPPool).where(IPPool.id == pool_id).with_for_update())
    if not pool or not pool.is_active:
        raise HTTPException(404, 'Active IPAM pool not found')
    network = network_for(pool)
    total_hosts = max(network.num_addresses - 2, 0)
    if total_hosts < 1:
        raise HTTPException(409, 'IPAM pool has no usable host addresses')

    if preferred_address:
        try:
            candidate = ipaddress.ip_address(preferred_address)
        except ValueError:
            raise HTTPException(422, 'Preferred IP address is invalid') from None
        if candidate.version != 4 or not _valid_candidate(pool, network, candidate):
            raise HTTPException(422, 'Preferred IP is outside the usable pool range')
        if active_address_exists(db, pool.id, candidate):
            raise HTTPException(409, 'Preferred IP address is already allocated')
    else:
        start = max(1, int(pool.next_offset or 1))
        used = {
            ipaddress.ip_address(value)
            for value in db.scalars(select(IPAllocation.address).where(
                IPAllocation.pool_id == pool.id,
                IPAllocation.status.in_(['reserved', 'assigned']),
            )).all()
        }
        candidate = None
        for index in range(total_hosts):
            offset = ((start - 1 + index) % total_hosts) + 1
            current = network.network_address + offset
            if _valid_candidate(pool, network, current) and current not in used:
                candidate = current
                pool.next_offset = (offset % total_hosts) + 1
                break
        if candidate is None:
            raise HTTPException(409, 'IPAM pool is exhausted')

    allocation = IPAllocation(
        pool_id=pool.id,
        address=str(candidate),
        prefix_length=network.prefixlen,
        gateway=pool.gateway,
        hostname=hostname,
        created_by=actor_id,
    )
    db.add(allocation)
    db.flush()
    return allocation
