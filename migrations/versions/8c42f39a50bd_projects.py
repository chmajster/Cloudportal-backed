"""Add server-side selection to the Projects schema already merged in #119.

Do not recreate projects/memberships/grants or modify revision 9a42d10e63bc.
"""
from alembic import op
import sqlalchemy as sa

revision = '8c42f39a50bd'
down_revision = '9a42d10e63bc'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_project_contexts',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=True),
        sa.Column('project_id', sa.String(36), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                                ondelete='RESTRICT', name='fk_user_project_contexts_project'),
        sa.CheckConstraint('version >= 1', name='ck_user_project_contexts_version'),
        sa.CheckConstraint('(tenant_id IS NULL) = (project_id IS NULL)', name='ck_user_project_contexts_pair'))
    op.create_index('ix_user_project_contexts_tenant_id', 'user_project_contexts', ['tenant_id'])
    op.create_index('ix_user_project_contexts_project_id', 'user_project_contexts', ['project_id'])


def downgrade():
    op.drop_table('user_project_contexts')
