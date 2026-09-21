from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from app.config import settings
from app.database import engine, session
from app.models import User, UserRole
from app.projects.models import Project, ProjectMembership
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.tenancy.models import TenantMembership
from app.tenancy.permissions import DEFAULT_TENANT_ID


def test_default_backfill_preserves_existing_membership_and_global_roles(tmp_path, monkeypatch):
    monkeypatch.setenv('CP_DATABASE_URL','sqlite:///'+str(tmp_path/'projects-upgrade.db'))
    settings.cache_clear();engine.cache_clear()
    try:
        command.upgrade(Config('alembic.ini'),'7b31e28f49ac')
        with session() as db:
            a=User(username='legacy',email='legacy@example.com',password_hash='unchanged')
            b=User(username='disabledmember',email='disabled@example.com',password_hash='unchanged')
            db.add_all([a,b]);db.flush()
            db.add(TenantMembership(tenant_id=DEFAULT_TENANT_ID,user_id=b.id,status='disabled'))
            a_id,b_id=a.id,b.id;db.commit()
        command.upgrade(Config('alembic.ini'),'8c42f39a50bd')
        command.upgrade(Config('alembic.ini'),'8c42f39a50bd')
        with session() as db:
            assert db.get(Project,DEFAULT_PROJECT_ID).is_system
            assert db.scalar(select(func.count()).select_from(Project))==1
            assert db.get(TenantMembership,(DEFAULT_TENANT_ID,b_id)).status=='disabled'
            assert db.get(ProjectMembership,(DEFAULT_PROJECT_ID,b_id)).status=='disabled'
            assert db.get(ProjectMembership,(DEFAULT_PROJECT_ID,a_id)).status=='active'
            assert db.scalar(select(func.count()).select_from(UserRole))==0
            assert db.get(User,a_id).password_hash=='unchanged'
        command.downgrade(Config('alembic.ini'),'7b31e28f49ac')
        with session() as db:
            assert db.get(User,a_id).password_hash=='unchanged'
            assert db.get(TenantMembership,(DEFAULT_TENANT_ID,b_id)).status=='disabled'
    finally:
        engine().dispose();engine.cache_clear();settings.cache_clear()
