"""Repair clone Blueprints accidentally saved as proxmox-appliance.

Revision ID: 7f3c1a9d4b20
Revises: 2d7e4a1b6c90
"""
from alembic import op
import sqlalchemy as sa

revision = '7f3c1a9d4b20'
down_revision = '2d7e4a1b6c90'
branch_labels = None
depends_on = None


blueprints = sa.table(
    'blueprints',
    sa.column('id', sa.Integer),
    sa.column('deployment', sa.JSON),
)


def _legacy_clone(deployment):
    if not isinstance(deployment, dict):
        return False
    variables = deployment.get('variables')
    return (
        deployment.get('template') == 'proxmox-appliance'
        and isinstance(variables, dict)
        and 'template_id' in variables
        and 'import_file_ids' not in variables
    )


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(sa.select(blueprints.c.id, blueprints.c.deployment)).mappings().all()
    for row in rows:
        deployment = row['deployment']
        if not _legacy_clone(deployment):
            continue
        fixed = dict(deployment)
        fixed['template'] = 'proxmox-vm'
        connection.execute(
            blueprints.update()
            .where(blueprints.c.id == row['id'])
            .values(deployment=fixed)
        )


def downgrade():
    # This repair is intentionally irreversible: after the template is corrected,
    # there is no reliable way to distinguish a migrated row from a Blueprint that
    # was created correctly as proxmox-vm.
    pass
