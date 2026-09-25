"""Scope Blueprint slug uniqueness to tenant/project.

Revision ID: 2d7e4a1b6c90
Revises: 1b7d4a9c2e61
"""
from alembic import op
import sqlalchemy as sa

revision = '2d7e4a1b6c90'
down_revision = '1b7d4a9c2e61'
branch_labels = None
depends_on = None

NAMING_CONVENTION = {'uq': 'uq_%(table_name)s_%(column_0_name)s'}


def _legacy_slug_constraint():
    constraints = sa.inspect(op.get_bind()).get_unique_constraints('blueprints')
    return next(
        (row for row in constraints if row.get('column_names') == ['slug']),
        None,
    )


def upgrade():
    legacy = _legacy_slug_constraint()
    dialect = op.get_bind().dialect.name

    if legacy is not None:
        if dialect == 'sqlite':
            # SQLite may reflect the historical unnamed UNIQUE(slug). A naming
            # convention gives Alembic a deterministic name during table rebuild.
            with op.batch_alter_table('blueprints', naming_convention=NAMING_CONVENTION) as batch:
                batch.drop_constraint(legacy.get('name') or 'uq_blueprints_slug', type_='unique')
                batch.create_unique_constraint(
                    'uq_blueprints_scope_slug',
                    ['tenant_id', 'project_id', 'slug'],
                )
            return
        op.drop_constraint(legacy['name'], 'blueprints', type_='unique')

    op.create_unique_constraint(
        'uq_blueprints_scope_slug',
        'blueprints',
        ['tenant_id', 'project_id', 'slug'],
    )


def downgrade():
    connection = op.get_bind()
    duplicates = connection.execute(sa.text(
        'SELECT slug FROM blueprints GROUP BY slug HAVING COUNT(*) > 1 LIMIT 1'
    )).first()
    if duplicates is not None:
        raise RuntimeError(
            'Cannot restore global Blueprint slug uniqueness while duplicate slugs exist across projects'
        )

    dialect = connection.dialect.name
    if dialect == 'sqlite':
        with op.batch_alter_table('blueprints', naming_convention=NAMING_CONVENTION) as batch:
            batch.drop_constraint('uq_blueprints_scope_slug', type_='unique')
            batch.create_unique_constraint('uq_blueprints_slug', ['slug'])
        return

    op.drop_constraint('uq_blueprints_scope_slug', 'blueprints', type_='unique')
    op.create_unique_constraint('uq_blueprints_slug', 'blueprints', ['slug'])
