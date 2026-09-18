"""store encrypted Terraform state"""
from alembic import op
import sqlalchemy as sa

revision = 'a72df0169c31'
down_revision = 'f6c93a81be57'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'terraform_states',
        sa.Column('deployment_id', sa.String(length=36), nullable=False),
        sa.Column('encrypted_state', sa.LargeBinary(), nullable=False),
        sa.Column('state_sha256', sa.String(length=64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['deployment_id'], ['deployments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('deployment_id'),
    )


def downgrade():
    op.drop_table('terraform_states')
