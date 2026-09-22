"""Regression coverage for quota accounting migrations."""
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, select

from app.config import settings
from app.database import engine
from schema_helpers import legacy_deployment


def test_quota_backfill_serializes_json_dimensions_for_confirmed_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv('CP_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'quota-migration.db'))
    settings.cache_clear()
    engine.cache_clear()
    config = Config('alembic.ini')
    try:
        command.upgrade(config, 'c864db917f20')
        _, deployment_id, _, _ = legacy_deployment(
            engine(),
            variables={'cpu': 2, 'memory': 4096, 'disk': 40},
        )
        with engine().begin() as connection:
            deployments = Table('deployments', MetaData(), autoload_with=connection)
            connection.execute(
                deployments.update()
                .where(deployments.c.id == deployment_id)
                .values(status='successful')
            )

        command.upgrade(config, 'head')

        with engine().connect() as connection:
            allocations = Table('quota_allocations', MetaData(), autoload_with=connection)
            row = connection.execute(
                select(allocations).where(allocations.c.subject_id == deployment_id)
            ).mappings().one()
            assert row['dimensions'] == {
                'vm_count': 1,
                'vcpu': 2,
                'memory_mb': 4096,
                'disk_gib': 40,
            }
    finally:
        engine().dispose()
        engine.cache_clear()
        settings.cache_clear()
