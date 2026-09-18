"""add job retry lineage"""
from alembic import op
import sqlalchemy as sa

revision = 'f6c93a81be57'
down_revision = 'e4b6c81a2d09'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('retry_of', sa.String(length=36), nullable=True))
        batch.add_column(sa.Column('attempt', sa.Integer(), server_default='1', nullable=False))
        batch.create_foreign_key('fk_jobs_retry_of_jobs', 'jobs', ['retry_of'], ['id'])
        batch.create_index(op.f('ix_jobs_retry_of'), ['retry_of'])


def downgrade():
    with op.batch_alter_table('jobs') as batch:
        batch.drop_index(op.f('ix_jobs_retry_of'))
        batch.drop_constraint('fk_jobs_retry_of_jobs', type_='foreignkey')
        batch.drop_column('attempt')
        batch.drop_column('retry_of')
