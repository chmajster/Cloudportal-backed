"""Projects, bounded role delegations and validated preferred context.

Adds Default/Default memberships without copying or expanding global roles.
Legacy infrastructure data is not yet moved by this project-domain migration.
"""
from datetime import datetime, timezone
from alembic import op
import sqlalchemy as sa

revision = '8c42f39a50bd'
down_revision = '7b31e28f49ac'
branch_labels = None
depends_on = None


def timestamps():
    return (sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False))


def upgrade():
    op.create_table('projects',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False), sa.Column('slug', sa.String(63), nullable=False),
        sa.Column('description', sa.Text(), nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('labels', sa.JSON(), nullable=False), sa.Column('metadata', sa.JSON(), nullable=False),
        sa.Column('default_environment', sa.String(63), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('deleted_at', sa.DateTime()), sa.Column('version', sa.Integer(), nullable=False), *timestamps(),
        sa.UniqueConstraint('tenant_id', 'slug', name='uq_projects_tenant_slug'),
        sa.UniqueConstraint('tenant_id', 'id', name='uq_projects_tenant_id'),
        sa.CheckConstraint("status IN ('active', 'suspended', 'disabled')", name='ck_projects_status'),
        sa.CheckConstraint('version >= 1', name='ck_projects_version'))
    for column in ('tenant_id', 'status', 'deleted_at'):
        op.create_index('ix_projects_' + column, 'projects', [column])
    op.create_table('project_memberships',
        sa.Column('project_id', sa.String(36), primary_key=True), sa.Column('user_id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')), sa.Column('version', sa.Integer(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                                ondelete='RESTRICT', name='fk_project_memberships_project'),
        sa.ForeignKeyConstraint(['tenant_id', 'user_id'], ['tenant_memberships.tenant_id', 'tenant_memberships.user_id'],
                                ondelete='CASCADE', name='fk_project_memberships_tenant_member'),
        sa.CheckConstraint("status IN ('active', 'disabled')", name='ck_project_memberships_status'),
        sa.CheckConstraint('version >= 1', name='ck_project_memberships_version'))
    for column in ('tenant_id', 'user_id', 'status'):
        op.create_index('ix_project_memberships_' + column, 'project_memberships', [column])
    op.create_table('project_role_assignments',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')), *timestamps(),
        sa.ForeignKeyConstraint(['project_id', 'user_id'], ['project_memberships.project_id', 'project_memberships.user_id'],
                                ondelete='CASCADE', name='fk_project_role_assignments_membership'),
        sa.UniqueConstraint('project_id', 'user_id', 'role_id', name='uq_project_role_assignments'))
    for column in ('project_id', 'user_id', 'role_id'):
        op.create_index('ix_project_role_assignments_' + column, 'project_role_assignments', [column])
    op.create_table('project_role_grants',
        sa.Column('assignment_id', sa.String(36), sa.ForeignKey('project_role_assignments.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('permission_id', sa.Integer(), sa.ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True))
    op.create_index('ix_project_role_grants_permission_id', 'project_role_grants', ['permission_id'])
    op.create_table('user_project_contexts',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tenant_id', sa.String(36)), sa.Column('project_id', sa.String(36)),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                                ondelete='RESTRICT', name='fk_user_project_contexts_project'),
        sa.CheckConstraint('version >= 1', name='ck_user_project_contexts_version'),
        sa.CheckConstraint('(tenant_id IS NULL) = (project_id IS NULL)', name='ck_user_project_contexts_pair'))
    for column in ('tenant_id', 'project_id'):
        op.create_index('ix_user_project_contexts_' + column, 'user_project_contexts', [column])
    op.create_index('ix_audit_project_scope', 'audit', ['resource', 'resource_id', 'id'])
    bind = op.get_bind()
    metadata = sa.MetaData()
    projects = sa.Table('projects', metadata, autoload_with=bind)
    members = sa.Table('tenant_memberships', metadata, autoload_with=bind)
    project_members = sa.Table('project_memberships', metadata, autoload_with=bind)
    users = sa.Table('users', metadata, autoload_with=bind)
    tenant_id = '00000000-0000-0000-0000-000000000001'
    project_id = '00000000-0000-0000-0000-000000000002'
    instant = datetime.now(timezone.utc).replace(tzinfo=None)
    bind.execute(projects.insert().values(id=project_id, tenant_id=tenant_id, name='Default', slug='default',
        description='System project for backward-compatible resource ownership.', status='active',
        labels={}, metadata={}, default_environment='dev', is_system=True, version=1,
        created_at=instant, updated_at=instant))
    # SQL backfill is bounded by the database, not an all-users Python loop.
    bind.execute(members.insert().from_select(
        ['tenant_id', 'user_id', 'status', 'version', 'created_at', 'updated_at'],
        sa.select(sa.literal(tenant_id), users.c.id, sa.literal('active'), sa.literal(1),
                  sa.literal(instant), sa.literal(instant)).where(~sa.exists(sa.select(members.c.user_id).where(
                      members.c.tenant_id == tenant_id, members.c.user_id == users.c.id)))))
    bind.execute(project_members.insert().from_select(
        ['tenant_id', 'project_id', 'user_id', 'status', 'version', 'created_at', 'updated_at'],
        sa.select(sa.literal(tenant_id), sa.literal(project_id), members.c.user_id, members.c.status,
                  sa.literal(1), sa.literal(instant), sa.literal(instant)).where(members.c.tenant_id == tenant_id)))


def downgrade():
    op.drop_index('ix_audit_project_scope', table_name='audit')
    for table in ('user_project_contexts', 'project_role_grants', 'project_role_assignments', 'project_memberships', 'projects'):
        op.drop_table(table)
    # Tenant memberships are deliberately retained; they may have been used or
    # explicitly edited since upgrade. Never infer that they can be deleted.
