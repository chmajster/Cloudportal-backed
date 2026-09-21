"""Isolated project domain fixtures runnable without optional provider packages."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Permission, Role, Setting, Token, User
from app.projects.models import Project
from app.projects.permissions import PROJECT_DELEGABLE_PERMISSIONS, DEFAULT_PROJECT_ID
from app.tenancy.models import Tenant, TenantMembership
from app.tenancy.permissions import DEFAULT_TENANT_ID, TENANCY_PERMISSIONS
from app.tenancy.authorization import Principal


@pytest.fixture
def domain():
    engine = create_engine('sqlite://')
    @event.listens_for(engine, 'connect')
    def enforce_fk(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        permissions = {name: Permission(name=name) for name in TENANCY_PERMISSIONS | {'users.read'}}
        db.add_all(permissions.values())
        db.add(Setting(key='governance', value={}))
        admin_role = Role(name='Explicit platform powers', permissions=list(permissions.values()))
        project_role = Role(name='Project manager', permissions=[permissions[p] for p in PROJECT_DELEGABLE_PERMISSIONS])
        viewer_role = Role(name='Project reader', permissions=[permissions['projects.read'], permissions['projects.select']])
        tenant_role = Role(name='Parent manager', permissions=[permissions[p] for p in TENANCY_PERMISSIONS
                            if p not in {'tenants.admin', 'tenants.create', 'tenants.delete'}])
        db.add_all([admin_role, project_role, viewer_role, tenant_role])
        users = [User(username=n, email=n+'@example.com', password_hash='unused', roles=[admin_role] if i == 0 else [])
                 for i, n in enumerate(['platform','alice','bob','eve'])]
        db.add_all(users)
        db.flush()
        tokens = [Token(name=u.username, user_id=u.id, token_hash=uuid4().hex, token_prefix='unit', kind='session', scopes=[]) for u in users]
        db.add_all(tokens)
        db.add(Tenant(id=DEFAULT_TENANT_ID, name='Default', slug='default', is_system=True))
        db.flush()
        db.add(Project(id=DEFAULT_PROJECT_ID, tenant_id=DEFAULT_TENANT_ID, name='Default', slug='default', is_system=True))
        db.flush(); db.commit()
        yield SimpleNamespace(db=db, perms=permissions, users=users, tokens=tokens, admin_role=admin_role,
            project_role=project_role, viewer_role=viewer_role, tenant_role=tenant_role,
            admin=Principal.from_token(tokens[0]), alice=Principal.from_token(tokens[1]),
            bob=Principal.from_token(tokens[2]), eve=Principal.from_token(tokens[3]))
    engine.dispose()
