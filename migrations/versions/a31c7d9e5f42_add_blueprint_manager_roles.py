from alembic import op
import sqlalchemy as sa

revision = 'a31c7d9e5f42'
down_revision = 'e5b7a3149d20'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'blueprint_manager_roles',
        sa.Column('blueprint_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['blueprint_id'], ['blueprints.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('blueprint_id', 'role_id'),
        sa.UniqueConstraint('role_id', name='uq_blueprint_manager_roles_role_id'),
    )


def downgrade():
    op.drop_table('blueprint_manager_roles')
