"""A populated pre-scope database upgrades without replacing users or secrets."""
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, select, inspect, update
from sqlalchemy.exc import IntegrityError
import pytest
from app.config import settings
from app.database import engine, session
from app.models import Deployment, Credential, User
from app.resource_scope.authorization import DEFAULT_SCOPE
from app.resource_scope.models import ProjectCredentialAccess, ProjectProviderAccess
from schema_helpers import legacy_deployment, historical_deployment


def test_legacy_backfill_constraints_repeat_upgrade_and_safe_downgrade(tmp_path,monkeypatch):
    monkeypatch.setenv('CP_DATABASE_URL','sqlite:///'+str(tmp_path/'upgrade.db'))
    settings.cache_clear();engine.cache_clear()
    try:
        command.upgrade(Config('alembic.ini'),'9a42d10e63bc')
        uid,did,cid,pid=legacy_deployment(engine(),variables={'cpu':4})
        command.upgrade(Config('alembic.ini'),'head')
        command.upgrade(Config('alembic.ini'),'head')
        with session() as db:
            d=db.get(Deployment,did)
            assert (d.tenant_id,d.project_id)==(DEFAULT_SCOPE.tenant_id,DEFAULT_SCOPE.project_id)
            assert d.variables=={'cpu':4} and d.created_by==uid
            assert db.get(User,uid).password_hash=='unchanged'
            assert db.get(Credential,cid).encrypted_secret==b'unchanged'
            assert db.get(ProjectCredentialAccess,(DEFAULT_SCOPE.project_id,cid)) is not None
            assert db.get(ProjectProviderAccess,(DEFAULT_SCOPE.project_id,pid)) is not None
        keys=inspect(engine()).get_foreign_keys('jobs')
        assert any(k['constrained_columns']==['tenant_id','project_id','deployment_id'] for k in keys)
        with engine().connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            with pytest.raises(IntegrityError):
                connection.execute(update(Deployment.__table__).where(Deployment.__table__.c.id==did).values(project_id='not-a-project'))
            connection.rollback()
            connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        command.downgrade(Config('alembic.ini'),'9a42d10e63bc')
        with engine().connect() as connection:
            assert historical_deployment(connection,did)['variables']=={'cpu':4}
        command.upgrade(Config('alembic.ini'),'head')
        with session() as db:
            assert db.get(Deployment,did).project_id==DEFAULT_SCOPE.project_id
    finally:
        engine().dispose();engine.cache_clear();settings.cache_clear()
