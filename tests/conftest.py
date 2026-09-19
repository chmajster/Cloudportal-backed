import os
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from app.config import settings
from app.database import Base, engine, session
from app.security.core import redis_client
from app.bootstrap import bootstrap, generate_key


@pytest.fixture(scope='session', autouse=True)
def local_redis():
    binary = os.environ.get('REDIS_SERVER_BINARY')
    if not binary:
        yield
        return
    import subprocess, time, socket
    process = subprocess.Popen([binary, '--port', '16389', '--bind', '127.0.0.1', '--save', '', '--appendonly', 'no'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', 16389), timeout=.1):
                    break
            except OSError:
                time.sleep(.05)
        yield
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv('CP_DATABASE_URL', os.environ.get('TEST_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'test.db')))
    monkeypatch.setenv('CP_REDIS_URL', os.environ.get('TEST_REDIS_URL', 'redis://127.0.0.1:16389/15'))
    monkeypatch.setenv('CP_MASTER_KEY_FILE', str(tmp_path / 'master.key'))
    monkeypatch.setenv('CP_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('CP_ALLOW_HTTP', 'true')
    monkeypatch.setenv('CP_PROVIDER_OFFLINE_QUEUE_ENABLED', 'false')
    settings.cache_clear()
    engine.cache_clear()
    redis_client.cache_clear()
    from app.jobs.queue import queue_connection
    queue_connection.cache_clear()
    redis_client().flushdb()
    settings().data_dir.mkdir()
    if os.environ.get('TEST_DATABASE_URL'):
        Base.metadata.drop_all(engine())
        from sqlalchemy import text
        with engine().begin() as connection:
            connection.execute(text('DROP TABLE IF EXISTS alembic_version'))
    command.upgrade(Config('alembic.ini'), 'head')
    generate_key()
    with session() as db:
        admin = bootstrap(db)
    from app.main import app
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, {'Authorization': 'Bearer ' + admin['token']}, admin
    engine().dispose()


@pytest.fixture
def client(system):
    return system[0]


@pytest.fixture
def headers(system):
    return system[1]


def new_user(client, headers, username='viewer', permissions=None):
    user = client.post('/api/v1/users', headers=headers, json={'username': username, 'email': username+'@example.com', 'password': 'strong-password-1234'})
    assert user.status_code == 201, user.text
    role = client.post('/api/v1/roles', headers=headers, json={'name': username+' role', 'permissions': permissions or []})
    assert role.status_code == 201, role.text
    response = client.put('/api/v1/users/'+str(user.json()['id'])+'/roles', headers=headers, json={'role_ids': [role.json()['id']]})
    assert response.status_code == 200, response.text
    login = client.post('/api/v1/auth/login', json={'username': username, 'password': 'strong-password-1234'})
    assert login.status_code == 200, login.text
    return user.json(), {'Authorization': 'Bearer '+login.json()['access_token']}
