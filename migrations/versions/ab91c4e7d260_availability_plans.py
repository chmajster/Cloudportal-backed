"""Add scoped Availability Plans and VM assignments.

Revision ID: ab91c4e7d260
Revises: f9d6c2a81e44
"""
from alembic import op
import sqlalchemy as sa


revision = 'ab91c4e7d260'
down_revision = 'f9d6c2a81e44'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'availability_plans',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('normalized_name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('state', sa.String(length=16), nullable=False, server_default='started'),
        sa.Column('group', sa.String(length=63), nullable=True),
        sa.Column('max_restart', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('max_relocate', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_availability_plans_project_scope', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'id', name='uq_availability_plans_scoped_id'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'normalized_name', name='uq_availability_plans_scope_name'),
    )
    op.create_index('ix_availability_plans_project_scope', 'availability_plans', ['tenant_id', 'project_id'])
    op.create_index('ix_availability_plans_is_active', 'availability_plans', ['is_active'])

    op.create_table(
        'availability_assignments',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('plan_id', sa.String(length=36), nullable=False),
        sa.Column('deployment_id', sa.String(length=36), nullable=True),
        sa.Column('resource_id', sa.String(length=36), nullable=True),
        sa.Column('plan_snapshot', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False, server_default='pending'),
        sa.Column('job_id', sa.String(length=36), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('last_applied_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
            name='fk_availability_assignments_project_scope', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id', 'plan_id'],
            ['availability_plans.tenant_id', 'availability_plans.project_id', 'availability_plans.id'],
            name='fk_availability_assignments_plan_scope', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id', 'deployment_id'],
            ['deployments.tenant_id', 'deployments.project_id', 'deployments.id'],
            name='fk_availability_assignments_deployment_scope', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'project_id', 'resource_id'],
            ['managed_vms.tenant_id', 'managed_vms.project_id', 'managed_vms.id'],
            name='fk_availability_assignments_resource_scope', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'project_id', 'id', name='uq_availability_assignments_scoped_id'),
        sa.UniqueConstraint('deployment_id', name='uq_availability_assignments_deployment'),
        sa.UniqueConstraint('resource_id', name='uq_availability_assignments_resource'),
    )
    op.create_index('ix_availability_assignments_project_scope', 'availability_assignments', ['tenant_id', 'project_id'])
    op.create_index('ix_availability_assignments_plan', 'availability_assignments', ['plan_id'])
    op.create_index('ix_availability_assignments_status', 'availability_assignments', ['status'])
    op.create_index('ix_availability_assignments_job_id', 'availability_assignments', ['job_id'])


def downgrade():
    op.drop_index('ix_availability_assignments_job_id', table_name='availability_assignments')
    op.drop_index('ix_availability_assignments_status', table_name='availability_assignments')
    op.drop_index('ix_availability_assignments_plan', table_name='availability_assignments')
    op.drop_index('ix_availability_assignments_project_scope', table_name='availability_assignments')
    op.drop_table('availability_assignments')
    op.drop_index('ix_availability_plans_is_active', table_name='availability_plans')
    op.drop_index('ix_availability_plans_project_scope', table_name='availability_plans')
    op.drop_table('availability_plans')
