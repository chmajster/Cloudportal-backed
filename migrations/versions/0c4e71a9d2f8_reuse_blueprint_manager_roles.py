"""Allow Blueprint manager roles to be reused across Blueprints."""
from alembic import op
import sqlalchemy as sa

revision = '0c4e71a9d2f8'
down_revision = 'f9d6c2a81e44'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('blueprint_manager_roles') as batch:
        batch.drop_constraint('uq_blueprint_manager_roles_role_id', type_='unique')
        batch.create_index('ix_blueprint_manager_roles_role_id', ['role_id'], unique=False)


def downgrade():
    connection = op.get_bind()
    duplicate = connection.execute(sa.text(
        'SELECT role_id FROM blueprint_manager_roles '
        'GROUP BY role_id HAVING COUNT(*) > 1 LIMIT 1'
    )).first()
    if duplicate is not None:
        raise RuntimeError(
            'Cannot downgrade while one manager role is assigned to multiple Blueprints'
        )
    with op.batch_alter_table('blueprint_manager_roles') as batch:
        batch.drop_index('ix_blueprint_manager_roles_role_id')
        batch.create_unique_constraint('uq_blueprint_manager_roles_role_id', ['role_id'])
