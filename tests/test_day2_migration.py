"""Day-2 migrations extend the merged tenancy/project chain without data loss."""
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, update
from app.config import settings
from app.database import engine
from sqlalchemy.orm import Session
from app.models import Credential, User
from app.projects.models import Project
from app.projects.context_models import UserProjectContext
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.tenancy.permissions import DEFAULT_TENANT_ID


def test_day2_upgrade_and_downgrade_preserve_existing_context_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv('CP_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'day2-migration.db'))
    settings.cache_clear(); engine.cache_clear()
    config = Config('alembic.ini')
    try:
        command.upgrade(config, '8c42f39a50bd')
        with Session(engine(), expire_on_commit=False) as db:
            user = User(username='legacy-day2', email='legacy-day2@example.com', password_hash='unchanged')
            secret = Credential(name='preserve', type='ssh', encrypted_secret=b'unchanged-ciphertext')
            db.add_all([user, secret]); db.flush(); uid, cid = user.id, secret.id
            db.add(UserProjectContext(user_id=uid, tenant_id=DEFAULT_TENANT_ID,
                                      project_id=DEFAULT_PROJECT_ID)); db.flush()
            db.execute(update(UserProjectContext).where(UserProjectContext.user_id == uid).values(version=7)); db.commit()
        command.upgrade(config, 'head'); command.upgrade(config, 'head')
        assert len(ScriptDirectory.from_config(config).get_heads()) == 1
        assert {'day2_action_requests', 'day2_resource_states', 'day2_resource_locks',
                'bulk_day2_action_requests'} <= set(inspect(engine()).get_table_names())
        with Session(engine(), expire_on_commit=False) as db:
            assert db.get(User, uid).password_hash == 'unchanged'
            assert db.get(Credential, cid).encrypted_secret == b'unchanged-ciphertext'
            assert db.get(UserProjectContext, uid).version == 7
            assert len(db.scalars(select(Project)).all()) == 1
        command.downgrade(config, '8c42f39a50bd')
        assert 'day2_action_requests' not in inspect(engine()).get_table_names()
        with Session(engine(), expire_on_commit=False) as db:
            assert db.get(UserProjectContext, uid).version == 7
            assert db.get(Credential, cid).encrypted_secret == b'unchanged-ciphertext'
        command.upgrade(config, 'head')
    finally:
        engine().dispose(); engine.cache_clear(); settings.cache_clear()
