"""Bind legacy infrastructure to Default/Default and add shared-access references.

Revision ID: b752ca806e19
Revises: 9a42d10e63bc
"""
from alembic import op
import sqlalchemy as sa

revision = 'b752ca806e19'
down_revision = '9a42d10e63bc'
branch_labels = None
depends_on = None

TENANT = '00000000-0000-0000-0000-000000000001'
PROJECT = '00000000-0000-0000-0000-000000000002'
TABLES = ('deployments', 'jobs', 'managed_vms', 'managed_resources', 'blueprints',
          'ip_pools', 'ip_allocations', 'hostname_schemes', 'hostname_reservations', 'scheduled_operations')

# Frozen migration metadata; never import mutable application model helpers.
PARENTS = {
    'jobs': (('deployment_id', 'deployments'), ('retry_of', 'jobs')),
    'managed_vms': (('deployment_id', 'deployments'),),
    'managed_resources': (('deployment_id', 'deployments'),),
    'scheduled_operations': (('deployment_id', 'deployments'),),
    'ip_allocations': (('pool_id', 'ip_pools'),),
    'hostname_reservations': (('scheme_id', 'hostname_schemes'),),
}


def upgrade():
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('tenant_id', sa.String(36), nullable=False, server_default=TENANT))
            batch.add_column(sa.Column('project_id', sa.String(36), nullable=False, server_default=PROJECT))
            batch.create_foreign_key(f'fk_{table}_project_scope', 'projects',
                                     ['tenant_id', 'project_id'], ['tenant_id', 'id'], ondelete='RESTRICT')
            batch.create_index(f'ix_{table}_project_scope', ['tenant_id', 'project_id'])
            batch.create_unique_constraint(f'uq_{table}_scoped_id', ['tenant_id', 'project_id', 'id'])
    # Add these only after every parent has its new key (important on PostgreSQL).
    for table, parents in PARENTS.items():
        with op.batch_alter_table(table) as batch:
            for key, parent in parents:
                batch.create_foreign_key(f'fk_{table}_scope_{key}', parent,
                    ['tenant_id', 'project_id', key], ['tenant_id', 'project_id', 'id'], ondelete='RESTRICT')
    for resource in ('credential', 'provider'):
        op.create_table(f'project_{resource}_access',
            sa.Column('project_id', sa.String(36), primary_key=True),
            sa.Column(f'{resource}_id', sa.Integer(), sa.ForeignKey(f'{resource}s.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('tenant_id', sa.String(36), nullable=False),
            sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                                    name=f'fk_{resource}_access_project', ondelete='RESTRICT'))
        target = sa.table(f'project_{resource}_access', sa.column('tenant_id'), sa.column('project_id'), sa.column(f'{resource}_id'))
        source = sa.table(f'{resource}s', sa.column('id'))
        op.execute(target.insert().from_select(['tenant_id', 'project_id', f'{resource}_id'],
                    sa.select(sa.literal(TENANT), sa.literal(PROJECT), source.c.id)))
    # Users created after the Projects migration also require compatibility membership.
    # Never reactivate an explicitly disabled membership or manufacture role grants.
    users = sa.table('users', sa.column('id'))
    tm = sa.table('tenant_memberships', sa.column('tenant_id'), sa.column('user_id'), sa.column('status'),
                  sa.column('version'), sa.column('created_at'), sa.column('updated_at'))
    pm = sa.table('project_memberships', sa.column('tenant_id'), sa.column('project_id'), sa.column('user_id'),
                  sa.column('status'), sa.column('version'), sa.column('created_at'), sa.column('updated_at'))
    stamp = sa.func.current_timestamp()
    op.execute(tm.insert().from_select(['tenant_id', 'user_id', 'status', 'version', 'created_at', 'updated_at'],
        sa.select(sa.literal(TENANT), users.c.id, sa.literal('active'), sa.literal(1), stamp, stamp).where(
            ~sa.exists(sa.select(tm.c.user_id).where(tm.c.tenant_id == TENANT, tm.c.user_id == users.c.id)))))
    op.execute(pm.insert().from_select(['tenant_id', 'project_id', 'user_id', 'status', 'version', 'created_at', 'updated_at'],
        sa.select(sa.literal(TENANT), sa.literal(PROJECT), tm.c.user_id, tm.c.status, sa.literal(1), stamp, stamp).where(
            tm.c.tenant_id == TENANT, ~sa.exists(sa.select(pm.c.user_id).where(
                pm.c.project_id == PROJECT, pm.c.user_id == tm.c.user_id)))))


def downgrade():
    # Removing ownership from populated non-default projects would silently merge
    # security domains. Refuse instead of flattening them into the legacy database.
    connection = op.get_bind()
    for name in TABLES:
        table = sa.table(name, sa.column('project_id'))
        if connection.scalar(sa.select(sa.func.count()).select_from(table).where(table.c.project_id != PROJECT)):
            raise RuntimeError('Cannot downgrade resource scoping with non-default resources')
    for resource in ('provider', 'credential'):
        table = sa.table(f'project_{resource}_access', sa.column('project_id'))
        if connection.scalar(sa.select(sa.func.count()).select_from(table).where(table.c.project_id != PROJECT)):
            raise RuntimeError('Cannot downgrade resource scoping with non-default assignments')
    for table, parents in reversed(tuple(PARENTS.items())):
        with op.batch_alter_table(table) as batch:
            for key, _ in parents:
                batch.drop_constraint(f'fk_{table}_scope_{key}', type_='foreignkey')
    for resource in ('provider', 'credential'):
        op.drop_table(f'project_{resource}_access')
    for table in reversed(TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f'uq_{table}_scoped_id', type_='unique')
            batch.drop_index(f'ix_{table}_project_scope')
            batch.drop_constraint(f'fk_{table}_project_scope', type_='foreignkey')
            batch.drop_column('project_id')
            batch.drop_column('tenant_id')
