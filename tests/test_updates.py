from conftest import new_user


def fake_status():
    return {
        'status': 'idle',
        'phase': 'idle',
        'progress': 0,
        'message': 'Updater gotowy.',
        'events': [],
        'output': [],
    }


def test_update_status_and_settings_are_proxied(client, headers, monkeypatch):
    import app.api.updates as updates

    def request(path, method='GET', payload=None):
        if path == '/status':
            return fake_status()
        if path == '/settings' and method == 'GET':
            return {'enabled': False, 'interval_hours': 24, 'ref': 'main'}
        if path == '/settings' and method == 'POST':
            return payload
        raise AssertionError((path, method, payload))

    monkeypatch.setattr(updates, 'updater_request', request)
    assert client.get('/api/v1/updates/status', headers=headers).json()['status'] == 'idle'
    assert client.get('/api/v1/updates/settings', headers=headers).json()['ref'] == 'main'
    changed = client.put('/api/v1/updates/settings', headers=headers, json={
        'enabled': True, 'interval_hours': 12, 'ref': 'main',
    })
    assert changed.status_code == 200
    assert changed.json()['enabled'] is True


def test_update_execution_requires_separate_permission(client, headers, monkeypatch):
    import app.api.updates as updates

    monkeypatch.setattr(updates, 'updater_request', lambda *args, **kwargs: {'accepted': True})
    _, reader = new_user(client, headers, username='update-reader', permissions=['updates.read'])
    assert client.get('/api/v1/updates/status', headers=reader).status_code == 200
    denied = client.post('/api/v1/updates/run', headers=reader, json={})
    assert denied.status_code == 403



def test_update_ref_override_requires_update_permission(client, headers, monkeypatch):
    import app.api.updates as updates

    monkeypatch.setattr(updates, 'updater_request', lambda *args, **kwargs: {'accepted': True, 'ref': 'main'})
    _, executor = new_user(
        client,
        headers,
        username='update-executor',
        permissions=['updates.read', 'updates.execute'],
    )
    denied_run = client.post('/api/v1/updates/run', headers=executor, json={'ref': 'feature/test'})
    denied_check = client.post('/api/v1/updates/check', headers=executor, json={'ref': 'feature/test'})
    assert denied_run.status_code == 403
    assert denied_check.status_code == 403

def test_update_start_is_idempotent_when_sidecar_reports_existing_run(client, headers, monkeypatch):
    import app.api.updates as updates
    from app.updates.service import UpdaterError

    def already_running(*args, **kwargs):
        raise UpdaterError(409, 'Update already in progress')

    monkeypatch.setattr(updates, 'updater_request', already_running)
    response = client.post('/api/v1/updates/run', headers=headers, json={})
    assert response.status_code == 202
    assert response.json() == {'accepted': False, 'already_running': True}


def test_update_ref_validation(client, headers, monkeypatch):
    import app.api.updates as updates

    monkeypatch.setattr(updates, 'updater_request', lambda *args, **kwargs: {'accepted': True})
    response = client.post('/api/v1/updates/run', headers=headers, json={'ref': '../unsafe'})
    assert response.status_code == 422


def test_update_sidecar_errors_are_preserved(client, headers, monkeypatch):
    import app.api.updates as updates
    from app.updates.service import UpdaterError

    def unavailable(*args, **kwargs):
        raise UpdaterError(503, 'Updater service is unavailable')

    monkeypatch.setattr(updates, 'updater_request', unavailable)
    response = client.get('/api/v1/updates/status', headers=headers)
    assert response.status_code == 503
    assert response.json()['detail'] == 'Updater service is unavailable'
