from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from app.config import settings
from app.database import engine, session
from app.models import Credential, Deployment, Provider, User, UserRole
from app.tenancy.models import TenantMembership
from app.tenancy.permissions import DEFAULT_TENANT_ID
from app.projects.models import Project, ProjectMembership, ProjectRoleAssignment
from app.projects.permissions import DEFAULT_PROJECT_ID


def test_projects_upgrade_preserves_legacy_identity_and_assigns_default_membership(tmp_path, monkeypatch):
    monkeypatch.setenv('CP_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'migration.db'))
    settings.cache_clear(); engine.cache_clear()
    try:
        command.upgrade(Config('alembic.ini'), '7b31e28f49ac')
        with session() as db:
            u = User(username='legacy', email='legacy@example.com', password_hash='unchanged')
            db.add(u); db.flush()
            c = Credential(name='legacy', type='proxmox', encrypted_secret=b'unchanged')
            db.add(c); db.flush()
            p = Provider(name='legacy', type='proxmox', credentials_id=c.id)
            db.add(p); db.flush()
            d = Deployment(name='legacy', provider_id=p.id, credentials_id=c.id, template='vm', variables={}, created_by=u.id)
            db.add(d); db.flush(); uid, did, cid = u.id, d.id, c.id
            db.commit()
        command.upgrade(Config('alembic.ini'), '9a42d10e63bc')
        command.upgrade(Config('alembic.ini'), '9a42d10e63bc')
        with session() as db:
            default = db.get(Project, DEFAULT_PROJECT_ID)
            assert default.tenant_id == DEFAULT_TENANT_ID and default.is_system and default.slug == 'default'
            assert db.scalar(select(func.count()).select_from(Project)) == 1
            assert db.get(TenantMembership, (DEFAULT_TENANT_ID, uid)).status == 'active'
            assert db.get(ProjectMembership, (DEFAULT_PROJECT_ID, uid)).tenant_id == DEFAULT_TENANT_ID
            assert db.scalar(select(func.count()).select_from(ProjectRoleAssignment)) == 0
            assert db.scalar(select(func.count()).select_from(UserRole)) == 0
            assert db.get(User, uid).password_hash == 'unchanged'
            assert db.get(Deployment, did).created_by == uid
            assert db.get(Credential, cid).encrypted_secret == b'unchanged'
        command.downgrade(Config('alembic.ini'), '7b31e28f49ac')
        with session() as db:
            assert db.get(Deployment, did).name == 'legacy'
            assert db.get(TenantMembership, (DEFAULT_TENANT_ID, uid))
    finally:
        engine().dispose(); engine.cache_clear(); settings.cache_clear()
