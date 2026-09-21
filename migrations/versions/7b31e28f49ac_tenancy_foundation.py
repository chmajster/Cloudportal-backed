"""Tenant identity, memberships and permission-bounded role assignments.

Additive foundation only: legacy resource ownership remains unchanged until the
project/resource-scope migration. Never infer full resource isolation from this
migration's successful completion.
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = '7b31e28f49ac'
down_revision = 'c4f17b8d62a1'
branch_labels = None
depends_on = None


def timestamps():
    return (sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False))


def upgrade():
    op.create_table('tenants',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('slug', sa.String(63), nullable=False, unique=True),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('labels', sa.JSON(), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("status IN ('active', 'suspended', 'disabled')", name='ck_tenants_status'),
        sa.CheckConstraint('version >= 1', name='ck_tenants_version'),
    )
    op.create_index('ix_tenants_status', 'tenants', ['status'])
    op.create_index('ix_tenants_deleted_at', 'tenants', ['deleted_at'])
    op.create_table('tenant_memberships',
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("status IN ('active', 'disabled')", name='ck_tenant_memberships_status'),
        sa.CheckConstraint('version >= 1', name='ck_tenant_memberships_version'),
    )
    op.create_index('ix_tenant_memberships_user_id', 'tenant_memberships', ['user_id'])
    op.create_index('ix_tenant_memberships_status', 'tenant_memberships', ['status'])
    op.create_table('tenant_role_assignments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(['tenant_id', 'user_id'],
                                ['tenant_memberships.tenant_id', 'tenant_memberships.user_id'],
                                ondelete='CASCADE', name='fk_tenant_role_assignments_membership'),
        sa.UniqueConstraint('tenant_id', 'user_id', 'role_id', name='uq_tenant_role_assignments'),
    )
    for column in ('tenant_id', 'user_id', 'role_id'):
        op.create_index('ix_tenant_role_assignments_' + column, 'tenant_role_assignments', [column])
    op.create_table('tenant_role_grants',
        sa.Column('assignment_id', sa.String(36), sa.ForeignKey('tenant_role_assignments.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('permission_id', sa.Integer(), sa.ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True),
    )
    op.create_index('ix_tenant_role_grants_permission_id', 'tenant_role_grants', ['permission_id'])
    op.create_index('ix_audit_tenant_scope', 'audit', ['resource', 'resource_id', 'id'])
    default_tenant = sa.table('tenants',
        sa.column('id', sa.String()), sa.column('name', sa.String()), sa.column('slug', sa.String()),
        sa.column('description', sa.Text()), sa.column('status', sa.String()), sa.column('labels', sa.JSON()),
        sa.column('metadata', sa.JSON()), sa.column('is_system', sa.Boolean()),
        sa.column('created_by', sa.Integer()), sa.column('deleted_at', sa.DateTime()),
        sa.column('version', sa.Integer()), sa.column('created_at', sa.DateTime()), sa.column('updated_at', sa.DateTime()),
    )
    instant = datetime.now(timezone.utc).replace(tzinfo=None)
    op.bulk_insert(default_tenant, [{
        'id': '00000000-0000-0000-0000-000000000001', 'name': 'Default', 'slug': 'default',
        'description': 'System tenant reserved for backward-compatible resource migration.',
        'status': 'active', 'labels': {}, 'metadata': {}, 'is_system': True,
        'created_by': None, 'deleted_at': None, 'version': 1,
        'created_at': instant, 'updated_at': instant,
    }])


def downgrade():
    # Downgrade explicitly removes only the newly introduced domain tables.
    op.drop_index('ix_audit_tenant_scope', table_name='audit')
    op.drop_table('tenant_role_grants')
    op.drop_table('tenant_role_assignments')
    op.drop_table('tenant_memberships')
    op.drop_table('tenants')
