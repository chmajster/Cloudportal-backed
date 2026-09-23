from schema_helpers import legacy_deployment, historical_deployment
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from app.config import settings
from app.database import engine, session
from app.models import Credential, Deployment, Provider, User, UserRole
from app.tenancy.models import TenantMembership
from app.tenancy.permissions import DEFAULT_TENANT_ID
from app.projects.models import ProjectMembership, ProjectRoleAssignment
from app.projects.permissions import DEFAULT_PROJECT_ID


def test_projects_upgrade_preserves_legacy_identity_and_assigns_default_membership(tmp_path, monkeypatch):
    monkeypatch.setenv('CP_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'migration.db'))
    settings.cache_clear(); engine.cache_clear()
    try:
        command.upgrade(Config('alembic.ini'), '7b31e28f49ac')
        uid, did, cid, _ = legacy_deployment(engine())
        command.upgrade(Config('alembic.ini'), '9a42d10e63bc')
        command.upgrade(Config('alembic.ini'), '9a42d10e63bc')
        with session() as db:
            default = db.execute(
                text('SELECT tenant_id, is_system, slug FROM projects WHERE id = :id'),
                {'id': DEFAULT_PROJECT_ID},
            ).mappings().one()
            assert default['tenant_id'] == DEFAULT_TENANT_ID and bool(default['is_system']) and default['slug'] == 'default'
            assert db.execute(text('SELECT COUNT(*) FROM projects')).scalar_one() == 1
            assert db.get(TenantMembership, (DEFAULT_TENANT_ID, uid)).status == 'active'
            assert db.get(ProjectMembership, (DEFAULT_PROJECT_ID, uid)).tenant_id == DEFAULT_TENANT_ID
            assert db.scalar(select(func.count()).select_from(ProjectRoleAssignment)) == 0
            assert db.scalar(select(func.count()).select_from(UserRole)) == 0
            assert db.get(User, uid).password_hash == 'unchanged'
            assert historical_deployment(db.connection(), did)['created_by'] == uid
            assert db.get(Credential, cid).encrypted_secret == b'unchanged'
        command.downgrade(Config('alembic.ini'), '7b31e28f49ac')
        with session() as db:
            assert historical_deployment(db.connection(), did)['name'] == 'legacy'
            assert db.get(TenantMembership, (DEFAULT_TENANT_ID, uid))
    finally:
        engine().dispose(); engine.cache_clear(); settings.cache_clear()
