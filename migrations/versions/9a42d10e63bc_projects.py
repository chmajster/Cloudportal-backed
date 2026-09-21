"""Projects, bounded scoped RBAC and additive Default/Default membership backfill."""
from datetime import datetime, timezone
from alembic import op
import sqlalchemy as sa

revision = '9a42d10e63bc'
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
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('slug', sa.String(63), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('labels', sa.JSON(), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=False),
        sa.Column('default_environment', sa.String(32), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('deleted_at', sa.DateTime()),
        sa.Column('version', sa.Integer(), nullable=False), *timestamps(),
        sa.UniqueConstraint('tenant_id', 'slug', name='uq_projects_tenant_slug'),
        sa.UniqueConstraint('tenant_id', 'id', name='uq_projects_tenant_id'),
        sa.CheckConstraint("status IN ('active', 'suspended', 'disabled')", name='ck_projects_status'),
        sa.CheckConstraint('version >= 1', name='ck_projects_version'))
    op.create_index('ix_projects_tenant_id', 'projects', ['tenant_id'])
    op.create_index('ix_projects_deleted_at', 'projects', ['deleted_at'])
    op.create_index('ix_projects_tenant_status', 'projects', ['tenant_id', 'status', 'id'])
    op.create_table('project_memberships',
        sa.Column('project_id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('version', sa.Integer(), nullable=False), *timestamps(),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                                ondelete='RESTRICT', name='fk_project_membership_project'),
        sa.ForeignKeyConstraint(['tenant_id', 'user_id'], ['tenant_memberships.tenant_id', 'tenant_memberships.user_id'],
                                ondelete='CASCADE', name='fk_project_membership_tenant_member'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'user_id', name='uq_project_membership_scope'),
        sa.CheckConstraint("status IN ('active', 'disabled')", name='ck_project_memberships_status'),
        sa.CheckConstraint('version >= 1', name='ck_project_memberships_version'))
    op.create_index('ix_project_membership_user_scope', 'project_memberships', ['user_id', 'tenant_id', 'status'])
    op.create_table('project_role_assignments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')), *timestamps(),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id', 'user_id'],
            ['project_memberships.tenant_id', 'project_memberships.project_id', 'project_memberships.user_id'],
            ondelete='CASCADE', name='fk_project_role_assignment_member'),
        sa.UniqueConstraint('project_id', 'user_id', 'role_id', name='uq_project_role_assignment'))
    op.create_index('ix_project_role_assignment_lookup', 'project_role_assignments', ['project_id', 'user_id'])
    op.create_index('ix_project_role_assignments_role_id', 'project_role_assignments', ['role_id'])
    op.create_table('project_role_grants',
        sa.Column('assignment_id', sa.String(36), sa.ForeignKey('project_role_assignments.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('permission_id', sa.Integer(), sa.ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True))
    op.create_index('ix_project_role_grants_permission_id', 'project_role_grants', ['permission_id'])
    instant = datetime.now(timezone.utc).replace(tzinfo=None)
    tenant_id = '00000000-0000-0000-0000-000000000001'
    project_id = '00000000-0000-0000-0000-000000000002'
    project = sa.table('projects',
        sa.column('id'), sa.column('tenant_id'), sa.column('name'), sa.column('slug'), sa.column('description'),
        sa.column('status'), sa.column('labels', sa.JSON()), sa.column('metadata', sa.JSON()),
        sa.column('default_environment'), sa.column('is_system', sa.Boolean()), sa.column('created_by', sa.Integer()),
        sa.column('deleted_at', sa.DateTime()), sa.column('version'),
        sa.column('created_at', sa.DateTime()), sa.column('updated_at', sa.DateTime()))
    op.bulk_insert(project, [{'id': project_id, 'tenant_id': tenant_id, 'name': 'Default', 'slug': 'default',
        'description': 'Backward-compatible default execution project.', 'status': 'active', 'labels': {},
        'metadata': {}, 'default_environment': 'dev', 'is_system': True, 'created_by': None,
        'deleted_at': None, 'version': 1, 'created_at': instant, 'updated_at': instant}])
    # No role or permission is manufactured by this backfill. Existing global
    # permissions remain the ceiling within Default/Default when resource scoping lands.
    users = sa.table('users', sa.column('id', sa.Integer()))
    members = sa.table('tenant_memberships', sa.column('tenant_id'), sa.column('user_id', sa.Integer()),
        sa.column('status'), sa.column('created_by', sa.Integer()), sa.column('version'),
        sa.column('created_at', sa.DateTime()), sa.column('updated_at', sa.DateTime()))
    statement = sa.select(sa.literal(tenant_id), users.c.id, sa.literal('active'), sa.null(), sa.literal(1),
                          sa.literal(instant), sa.literal(instant)).where(~sa.exists(sa.select(members.c.user_id).where(
                              members.c.tenant_id == tenant_id, members.c.user_id == users.c.id)))
    op.execute(sa.insert(members).from_select(['tenant_id', 'user_id', 'status', 'created_by', 'version', 'created_at', 'updated_at'], statement))
    pm = sa.table('project_memberships', sa.column('project_id'), sa.column('tenant_id'), sa.column('user_id', sa.Integer()),
        sa.column('status'), sa.column('created_by', sa.Integer()), sa.column('version'),
        sa.column('created_at', sa.DateTime()), sa.column('updated_at', sa.DateTime()))
    statement = sa.select(sa.literal(project_id), sa.literal(tenant_id), users.c.id, sa.literal('active'),
                          sa.null(), sa.literal(1), sa.literal(instant), sa.literal(instant))
    op.execute(sa.insert(pm).from_select(['project_id', 'tenant_id', 'user_id', 'status', 'created_by', 'version', 'created_at', 'updated_at'], statement))


def downgrade():
    # Tenant membership rows are deliberately retained: do not remove a user's
    # subsequently assigned tenant privileges during a project-only downgrade.
    op.drop_table('project_role_grants')
    op.drop_table('project_role_assignments')
    op.drop_table('project_memberships')
    op.drop_table('projects')
