"""Incremental upgrade never recreates #119 tables or changes existing records."""
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, inspect
from app.config import settings
from app.database import engine
from sqlalchemy.orm import Session
from app.models import User
from app.projects.models import Project, ProjectMembership
from app.projects.context_models import UserProjectContext
from app.tenancy.models import TenantMembership
from app.tenancy.permissions import DEFAULT_TENANT_ID
from app.projects.permissions import DEFAULT_PROJECT_ID


def test_context_additive_upgrade_downgrade_preserves_projects(tmp_path, monkeypatch):
    monkeypatch.setenv('CP_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'context.db'))
    settings.cache_clear(); engine.cache_clear()
    config = Config('alembic.ini')
    try:
        command.upgrade(config, '9a42d10e63bc')
        with Session(engine(), expire_on_commit=False) as db:
            user = User(username='context-legacy', email='context@example.com', password_hash='untouched')
            db.add(user); db.flush(); uid = user.id
            db.add(TenantMembership(tenant_id=DEFAULT_TENANT_ID, user_id=uid, status='active')); db.flush()
            db.add(ProjectMembership(tenant_id=DEFAULT_TENANT_ID, project_id=DEFAULT_PROJECT_ID,
                                     user_id=uid, status='disabled')); db.commit()
        command.upgrade(config, '8c42f39a50bd'); command.upgrade(config, '8c42f39a50bd')
        with Session(engine(), expire_on_commit=False) as db:
            assert len(db.scalars(select(Project)).all()) == 1
            assert db.get(User, uid).password_hash == 'untouched'
            assert db.get(ProjectMembership, (DEFAULT_PROJECT_ID, uid)).status == 'disabled'
            db.add(UserProjectContext(user_id=uid, tenant_id=DEFAULT_TENANT_ID, project_id=DEFAULT_PROJECT_ID)); db.commit()
        command.downgrade(config, '9a42d10e63bc')
        assert 'user_project_contexts' not in inspect(engine()).get_table_names()
        with Session(engine(), expire_on_commit=False) as db:
            assert db.get(Project, DEFAULT_PROJECT_ID).is_system
            assert db.get(ProjectMembership, (DEFAULT_PROJECT_ID, uid)).status == 'disabled'
            assert db.get(User, uid).password_hash == 'untouched'
        command.upgrade(config, 'head')
        assert len(ScriptDirectory.from_config(config).get_heads()) == 1
    finally:
        engine().dispose(); engine.cache_clear(); settings.cache_clear()
