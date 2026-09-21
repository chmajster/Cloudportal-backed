"""store encrypted Terraform approval plan"""
from alembic import op
import sqlalchemy as sa

revision = 'f0a4c2d9b671'
down_revision = 'b42d8e1f6a53'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'terraform_plans',
        sa.Column('deployment_id', sa.String(length=36), nullable=False),
        sa.Column('encrypted_plan', sa.LargeBinary(), nullable=False),
        sa.Column('plan_sha256', sa.String(length=64), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['deployment_id'], ['deployments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('deployment_id'),
    )


def downgrade():
    op.drop_table('terraform_plans')
