from schema_helpers import legacy_deployment, historical_deployment
"""An upgrade must add tenancy without moving/deleting any legacy resource."""
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.config import settings
from app.database import engine, session
from app.models import Credential, Deployment, Provider, User
from app.tenancy.models import Tenant
from app.tenancy.permissions import DEFAULT_TENANT_ID


def test_additive_upgrade_preserves_legacy_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv('CP_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'migration.db'))
    settings.cache_clear()
    engine.cache_clear()
    try:
        command.upgrade(Config('alembic.ini'), 'c4f17b8d62a1')
        user_id, deployment_id, _, _ = legacy_deployment(engine(), name='Legacy deployment', variables={'cpu': 4})
        command.upgrade(Config('alembic.ini'), '7b31e28f49ac')
        command.upgrade(Config('alembic.ini'), '7b31e28f49ac')
        with session() as db:
            default = db.get(Tenant, DEFAULT_TENANT_ID)
            assert default.name == 'Default' and default.is_system and default.status == 'active'
            assert len(db.scalars(select(Tenant)).all()) == 1
            old = historical_deployment(db.connection(), deployment_id)
            assert old['name'] == 'Legacy deployment' and old['variables'] == {'cpu': 4}
            assert old['created_by'] == user_id
            assert db.get(User, user_id).password_hash == 'unchanged'
        command.downgrade(Config('alembic.ini'), 'c4f17b8d62a1')
        with session() as db:
            assert historical_deployment(db.connection(), deployment_id)['name'] == 'Legacy deployment'
    finally:
        engine().dispose()
        engine.cache_clear()
        settings.cache_clear()
