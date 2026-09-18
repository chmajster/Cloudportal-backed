def test_root_redirects_to_local_web_console(client):
    response = client.get('/', follow_redirects=False)

    assert response.status_code == 307
    assert response.headers['location'] == '/ui/'


def test_web_console_and_assets_are_served_with_security_headers(client):
    page = client.get('/ui/')

    assert page.status_code == 200
    assert page.headers['content-type'].startswith('text/html')
    assert 'id="login-form"' in page.text
    assert 'data-theme="light"' in page.text
    assert page.text.count('data-theme-toggle') == 2
    assert 'id="sidebar-backdrop"' in page.text
    assert 'id="modal-close"' in page.text
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
    assert "['suspend', 'Resume']" not in script.text
    assert "['suspend', 'Suspend']" in script.text
    assert "['resume', 'Resume']" in script.text
    assert "['reset', 'Reset']" in script.text
    assert 'destroy_unreferenced_disks' in script.text
    assert 'credential-secret-panel' in script.text
    assert 'Akceptuj certyfikat self-signed / niezaufany' in script.text
    assert 'modal-form-error' in script.text
    assert "const THEME_KEY = 'cloudportal.console.theme';" in script.text
    assert 'function setTheme(' in script.text
    assert 'function setMobileMenu(' in script.text
    assert 'function copyText(' in script.text
    assert 'function setLoginMessage(' in script.text
    assert "replace(/^Value error" in script.text
    assert "dom.modal.addEventListener('cancel'" in script.text
    assert "setMobileMenu(false)" in script.text
    assert '192.168.1.10' in script.text
    assert 'restoreVmFromBackup' in script.text
    assert "backups.restore" in script.text
    assert "Uruchom restore" in script.text
    assert 'consoleRfb' in script.text
    assert 'rfb_module' in script.text
    assert 'ws_path' in script.text
    assert "window.open('about:blank'" not in script.text
    assert stylesheet.status_code == 200
    assert ':root {' in stylesheet.text
    assert 'html[data-theme="dark"]' in stylesheet.text
    assert '--sidebar-bg:' in stylesheet.text
    assert '.sidebar-backdrop' in stylesheet.text
    assert '.credential-secret-panel' in stylesheet.text
    assert '.credential-tls-control' in stylesheet.text
    assert '.modal-form-error' in stylesheet.text
    assert '.form-error.success' in stylesheet.text
    assert '.clipboard-fallback' in stylesheet.text
    assert '.toast.error' in stylesheet.text
    assert '.modal.modal-wide' in stylesheet.text
    assert stylesheet.headers['content-type'].startswith('text/css')


def test_web_console_is_not_added_to_openapi_contract(client):
    paths = client.get('/openapi.json').json()['paths']

    assert '/' not in paths
    assert '/ui/' not in paths
