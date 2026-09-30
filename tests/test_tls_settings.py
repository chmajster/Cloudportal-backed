def test_tls_settings_api_proxies_privileged_sidecar(client, headers, monkeypatch):
    import app.tls_config.api as tls_api

    calls = []

    def fake_request(path, method='GET', payload=None, **kwargs):
        calls.append((path, method, payload))
        if path == '/tls/status':
            return {
                'hostname': '100.118.132.40',
                'port': 8443,
                'source': 'managed-self-signed',
                'certificate': {'present': True, 'valid': True},
            }
        if path == '/tls/letsencrypt':
            return {
                'live_dir': '/etc/letsencrypt/live',
                'hostname': '100.118.132.40',
                'items': [{
                    'lineage': 'kynlab.ddnsfree.com',
                    'path': '/etc/letsencrypt/live/kynlab.ddnsfree.com',
                    'usable': True,
                    'certificate': {'dns_names': ['kynlab.ddnsfree.com'], 'valid': True},
                }],
            }
        return {'hostname': 'kynlab.ddnsfree.com', 'port': 8443, 'source': 'letsencrypt'}

    monkeypatch.setattr(tls_api, 'updater_request', fake_request)

    status = client.get('/api/v1/settings/tls', headers=headers)
    assert status.status_code == 200
    assert status.json()['hostname'] == '100.118.132.40'

    detected = client.get('/api/v1/settings/tls/letsencrypt', headers=headers)
    assert detected.status_code == 200
    assert detected.json()['items'][0]['lineage'] == 'kynlab.ddnsfree.com'

    applied = client.post(
        '/api/v1/settings/tls/letsencrypt',
        headers=headers,
        json={'lineage': 'kynlab.ddnsfree.com', 'hostname': 'kynlab.ddnsfree.com'},
    )
    assert applied.status_code == 200
    assert applied.json()['source'] == 'letsencrypt'
    assert calls[-1] == (
        '/tls/letsencrypt',
        'POST',
        {'lineage': 'kynlab.ddnsfree.com', 'hostname': 'kynlab.ddnsfree.com'},
    )
