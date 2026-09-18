"""add job retry lineage"""
from alembic import op
import sqlalchemy as sa

revision = 'f6c93a81be57'
down_revision = 'e4b6c81a2d09'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('jobs', sa.Column('retry_of', sa.String(length=36), nullable=True))
    op.add_column('jobs', sa.Column('attempt', sa.Integer(), server_default='1', nullable=False))
    op.create_foreign_key('fk_jobs_retry_of_jobs', 'jobs', 'jobs', ['retry_of'], ['id'])
    op.create_index(op.f('ix_jobs_retry_of'), 'jobs', ['retry_of'])


def downgrade():
    op.drop_index(op.f('ix_jobs_retry_of'), table_name='jobs')
    op.drop_constraint('fk_jobs_retry_of_jobs', 'jobs', type_='foreignkey')
    op.drop_column('jobs', 'attempt')
    op.drop_column('jobs', 'retry_of')
