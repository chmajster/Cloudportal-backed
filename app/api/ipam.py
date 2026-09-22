import ipaddress
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select

from app.api.common import Limit, Offset, find, idempotent
from app.api.schemas import Input, Name
from app.database import get_db
from app.ipam.service import allocate_address, allocation_public, is_excluded, network_for, pool_public
from app.models import IPAllocation, IPPool, now
from app.security.core import audit
from app.resource_scope.http import require


router = APIRouter(prefix='/ipam', tags=['ipam'])


class IPPoolInput(Input):
    name: Name
    cidr: Annotated[str, Field(min_length=9, max_length=64)]
    gateway: Annotated[str | None, Field(max_length=45)] = None
    dns_servers: Annotated[list[str], Field(max_length=8)] = Field(default_factory=list)
    excluded_addresses: Annotated[list[str], Field(max_length=256)] = Field(default_factory=list)
    is_active: bool = True

    @field_validator('cidr')
    @classmethod
    def valid_cidr(cls, value):
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError:
            raise ValueError('CIDR must be a canonical network') from None
        if network.version != 4:
            raise ValueError('Only IPv4 pools are currently supported')
        if network.prefixlen < 16 or network.prefixlen > 30:
            raise ValueError('IPv4 pool prefix must be between /16 and /30')
        return str(network)

    @field_validator('gateway')
    @classmethod
    def valid_gateway(cls, value):
        if value is None:
            return value
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ValueError('Gateway must be an IP address') from None
        if address.version != 4:
            raise ValueError('Gateway must be IPv4')
        return str(address)

    @field_validator('dns_servers')
    @classmethod
    def valid_dns(cls, values):
        result = []
        for value in values:
            try:
                result.append(str(ipaddress.ip_address(value)))
            except ValueError:
                raise ValueError('DNS servers must be IP addresses') from None
        return result

    @field_validator('excluded_addresses')
    @classmethod
    def valid_exclusions(cls, values):
        result = []
        for value in values:
            try:
                item = ipaddress.ip_network(value, strict=False)
            except ValueError:
                try:
                    address = ipaddress.ip_address(value)
                except ValueError:
                    raise ValueError('IPAM exclusions must be an IP address or CIDR') from None
                item = ipaddress.ip_network(f'{address}/{address.max_prefixlen}', strict=False)
            if item.version != 4:
                raise ValueError('IPAM exclusions must be IPv4')
            result.append(str(item))
        return result

    @model_validator(mode='after')
    def coherent(self):
        network = ipaddress.ip_network(self.cidr)
        if self.gateway:
            gateway = ipaddress.ip_address(self.gateway)
            if gateway not in network or gateway in {network.network_address, network.broadcast_address}:
                raise ValueError('Gateway must be a usable address inside the pool')
        for raw in self.excluded_addresses:
            item = ipaddress.ip_network(raw)
            if not item.subnet_of(network):
                raise ValueError('Every exclusion must be contained in the pool')
        return self


class AllocateIPInput(Input):
    preferred_address: Annotated[str | None, Field(max_length=45)] = None
    hostname: Annotated[str | None, Field(max_length=253)] = None

    @field_validator('preferred_address')
    @classmethod
    def preferred_ip(cls, value):
        if value is None:
            return value
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ValueError('Preferred address is invalid') from None
        if address.version != 4:
            raise ValueError('Preferred address must be IPv4')
        return str(address)


class AssignIPInput(Input):
    resource_id: Annotated[str, Field(min_length=1, max_length=100)]
    hostname: Annotated[str | None, Field(max_length=253)] = None


def ensure_no_overlap(db, cidr, ignore_id=None):
    network = ipaddress.ip_network(cidr)
    for pool in db.scalars(select(IPPool)).all():
        if ignore_id is not None and pool.id == ignore_id:
            continue
        if network.overlaps(ipaddress.ip_network(pool.cidr)):
            raise HTTPException(409, f'IPAM pool overlaps existing pool {pool.name}')


def ensure_active_allocations_fit(db, pool, data):
    network = ipaddress.ip_network(data.cidr)
    probe = type('PoolProbe', (), {
        'gateway': data.gateway,
        'excluded_addresses': data.excluded_addresses,
    })()
    active = db.scalars(select(IPAllocation).where(
        IPAllocation.pool_id == pool.id,
        IPAllocation.status.in_(['reserved', 'assigned']),
    )).all()
    for allocation in active:
        address = ipaddress.ip_address(allocation.address)
        if address not in network or address in {network.network_address, network.broadcast_address} or is_excluded(probe, address):
            raise HTTPException(409, f'Pool update would invalidate active allocation {allocation.address}')


@router.get('/pools')
def pools(limit: Limit = 100, offset: Offset = 0, actor=Depends(require('ipam.read')),
          db=Depends(get_db, scope='function')):
    rows = db.scalars(select(IPPool).order_by(IPPool.id.desc()).offset(offset).limit(limit)).all()
    return {'items': [pool_public(row) for row in rows]}


@router.get('/pools/{id}')
def pool(id: int, actor=Depends(require('ipam.read')), db=Depends(get_db, scope='function')):
    return pool_public(find(db, IPPool, id))


@router.post('/pools', status_code=201)
def create_pool(data: IPPoolInput, request: Request, actor=Depends(require('ipam.create')),
                db=Depends(get_db, scope='function')):
    ensure_no_overlap(db, data.cidr)
    def create():
        row = IPPool(**data.model_dump(), created_by=actor.user_id)
        db.add(row)
        db.flush()
        audit(db, request, 'ipam.pool_created', 'ip_pools', row.id)
        return pool_public(row)
    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/pools/{id}')
def update_pool(id: int, data: IPPoolInput, request: Request, actor=Depends(require('ipam.update')),
                db=Depends(get_db, scope='function')):
    row = db.scalar(select(IPPool).where(IPPool.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Resource not found')
    if row.cidr != data.cidr:
        history = db.scalar(select(IPAllocation.id).where(IPAllocation.pool_id == id).limit(1))
        if history:
            raise HTTPException(409, 'Pool CIDR cannot change after allocation history exists')
        ensure_no_overlap(db, data.cidr, ignore_id=id)
        row.next_offset = 1
    ensure_active_allocations_fit(db, row, data)
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    audit(db, request, 'ipam.pool_updated', 'ip_pools', id)
    db.flush()
    return pool_public(row)


@router.delete('/pools/{id}')
def delete_pool(id: int, request: Request, actor=Depends(require('ipam.delete')),
                db=Depends(get_db, scope='function')):
    row = find(db, IPPool, id)
    if db.scalar(select(IPAllocation.id).where(IPAllocation.pool_id == id).limit(1)):
        raise HTTPException(409, 'IPAM pool has allocation history; disable it instead')
    db.delete(row)
    audit(db, request, 'ipam.pool_deleted', 'ip_pools', id)
    return {'deleted': True}


@router.get('/allocations')
def allocations(pool_id: Annotated[int | None, Query(gt=0)] = None,
                status: Annotated[Literal['reserved', 'assigned', 'released'] | None, Query()] = None,
                resource_id: Annotated[str | None, Query(max_length=100)] = None,
                limit: Limit = 100, offset: Offset = 0,
                actor=Depends(require('ipam.read')), db=Depends(get_db, scope='function')):
    query = select(IPAllocation)
    if pool_id:
        query = query.where(IPAllocation.pool_id == pool_id)
    if status:
        query = query.where(IPAllocation.status == status)
    if resource_id:
        query = query.where(IPAllocation.resource_id == resource_id)
    rows = db.scalars(query.order_by(IPAllocation.created_at.desc()).offset(offset).limit(limit)).all()
    return {'items': [allocation_public(row) for row in rows]}


@router.post('/pools/{id}/allocate', status_code=201)
def allocate(id: int, data: AllocateIPInput, request: Request,
             actor=Depends(require('ipam.allocate')), db=Depends(get_db, scope='function')):
    def create():
        row = allocate_address(
            db,
            id,
            actor.user_id,
            preferred_address=data.preferred_address,
            hostname=data.hostname,
        )
        audit(db, request, 'ipam.address_reserved', 'ip_allocations', row.id)
        return allocation_public(row)
    return idempotent(db, request, actor, data.model_dump(), create, required=True)


@router.post('/allocations/{id}/assign')
def assign(id: str, data: AssignIPInput, request: Request,
           actor=Depends(require('ipam.allocate')), db=Depends(get_db, scope='function')):
    row = db.scalar(select(IPAllocation).where(IPAllocation.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Resource not found')
    if row.status != 'reserved':
        raise HTTPException(409, 'Only a reserved address can be assigned')
    row.status = 'assigned'
    row.resource_id = data.resource_id
    if data.hostname is not None:
        row.hostname = data.hostname
    audit(db, request, 'ipam.address_assigned', 'ip_allocations', row.id)
    return allocation_public(row)


@router.post('/allocations/{id}/release')
def release(id: str, request: Request, actor=Depends(require('ipam.release')),
            db=Depends(get_db, scope='function')):
    row = db.scalar(select(IPAllocation).where(IPAllocation.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Resource not found')
    if row.status == 'released':
        raise HTTPException(409, 'Address is already released')
    row.status = 'released'
    row.released_at = now()
    audit(db, request, 'ipam.address_released', 'ip_allocations', row.id)
    return allocation_public(row)
