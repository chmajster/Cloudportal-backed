def test_root_redirects_to_local_web_console(client):
    response = client.get('/', follow_redirects=False)

    assert response.status_code == 307
    assert response.headers['location'] == '/ui/'


def test_web_console_and_assets_are_served_with_security_headers(client):
    page = client.get('/ui/')

    assert page.status_code == 200
    assert page.headers['content-type'].startswith('text/html')
    assert 'id="login-form"' in page.text
    assert 'src="./app.js"' in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert page.headers['x-frame-options'] == 'DENY'
    assert page.headers['content-security-policy'] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )

    script = client.get('/ui/app.js')
    stylesheet = client.get('/ui/styles.css')

    assert script.status_code == 200
    assert script.headers['content-type'].startswith(('text/javascript', 'application/javascript'))
    assert "const API = '/api/v1';" in script.text
    assert 'CREDENTIAL_TYPE_CONFIG' in script.text
    assert 'Secrets JSON' not in script.text
    assert 'Zastąp zapisane sekrety' in script.text
    assert 'credential-secret-panel' in script.text
    assert 'Blueprint Designer' in script.text
    assert 'Obraz / template Proxmox' in script.text
    assert 'Pattern hostname' in script.text
    assert 'Tagi Proxmox' in script.text
    assert 'Cloud-init' in script.text
    assert "'set_tags'" in script.text
    assert stylesheet.status_code == 200
    assert '.credential-secret-panel' in stylesheet.text
    assert '.modal.modal-wide' in stylesheet.text
    assert '.workflow-preview' in stylesheet.text
    assert '.blueprint-run-summary' in stylesheet.text
    assert stylesheet.headers['content-type'].startswith('text/css')


def test_web_console_is_not_added_to_openapi_contract(client):
    paths = client.get('/openapi.json').json()['paths']

    assert '/' not in paths
    assert '/ui/' not in paths
