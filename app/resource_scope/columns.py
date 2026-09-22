"""Shared database ownership contract; no authorization decisions in model classes."""
from sqlalchemy import ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

DEFAULT_TENANT_ID = '00000000-0000-0000-0000-000000000001'
DEFAULT_PROJECT_ID = '00000000-0000-0000-0000-000000000002'


class ResourceScope:
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default=DEFAULT_TENANT_ID,
                                          server_default=DEFAULT_TENANT_ID)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, default=DEFAULT_PROJECT_ID,
                                           server_default=DEFAULT_PROJECT_ID)


PARENT_CONSTRAINTS = {
    'jobs': (('deployment_id', 'deployments'), ('retry_of', 'jobs')),
    'managed_vms': (('deployment_id', 'deployments'),),
    'managed_resources': (('deployment_id', 'deployments'),),
    'scheduled_operations': (('deployment_id', 'deployments'),),
    'ip_allocations': (('pool_id', 'ip_pools'),),
    'hostname_reservations': (('scheme_id', 'hostname_schemes'),),
}


def scope_constraints(table):
    return (
        ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                             name=f'fk_{table}_project_scope', ondelete='RESTRICT'),
        Index(f'ix_{table}_project_scope', 'tenant_id', 'project_id'),
        UniqueConstraint('tenant_id', 'project_id', 'id', name=f'uq_{table}_scoped_id'),
        *(ForeignKeyConstraint(['tenant_id', 'project_id', key],
            [f'{parent}.tenant_id', f'{parent}.project_id', f'{parent}.id'],
            name=f'fk_{table}_scope_{key}', ondelete='RESTRICT')
          for key, parent in PARENT_CONSTRAINTS.get(table, ())),
    )
