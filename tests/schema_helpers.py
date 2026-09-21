"""Historical fixtures use reflected tables, never a newer ORM ownership schema."""
from uuid import uuid4
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session
from app.models import Credential, Provider, User, now


def legacy_deployment(engine, *, name='legacy', variables=None):
    with Session(engine) as db:
        user = User(username='legacy', email='legacy@example.com', password_hash='unchanged')
        db.add(user); db.flush()
        credential = Credential(name='legacy', type='proxmox', encrypted_secret=b'unchanged')
        db.add(credential); db.flush()
        provider = Provider(name='legacy', type='proxmox', credentials_id=credential.id)
        db.add(provider); db.flush()
        table = Table('deployments', MetaData(), autoload_with=db.connection())
        deployment_id = str(uuid4())
        db.execute(table.insert().values(
            id=deployment_id, name=name, provider_id=provider.id, provider='proxmox',
            credentials_id=credential.id, template='legacy-template', variables=variables or {},
            workspace=str(uuid4()), workflow={}, state_location='', status='queued',
            created_by=user.id, executor='terraform', created_at=now(), updated_at=now()))
        result = user.id, deployment_id, credential.id, provider.id
        db.commit()
        return result


def historical_deployment(connection, deployment_id):
    table = Table('deployments', MetaData(), autoload_with=connection)
    return connection.execute(select(table).where(table.c.id == deployment_id)).mappings().one()
