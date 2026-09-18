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
    assert 'id="refresh-view"' in page.text
    assert 'src="./theme-init.js"' in page.text
    assert 'src="./app.js"' in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert page.headers['x-frame-options'] == 'DENY'
    assert page.headers['content-security-policy'] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )

    theme_script = client.get('/ui/theme-init.js')
    script = client.get('/ui/app.js')
    stylesheet = client.get('/ui/styles.css')

    assert theme_script.status_code == 200
    assert theme_script.headers['content-type'].startswith(('text/javascript', 'application/javascript'))
    assert "cloudportal.console.theme" in theme_script.text
    assert "document.documentElement.dataset.theme" in theme_script.text

    assert script.status_code == 200
    assert script.headers['content-type'].startswith(('text/javascript', 'application/javascript'))
    assert "const API = '/api/v1';" in script.text
    assert 'CREDENTIAL_TYPE_CONFIG' in script.text
    assert 'Secrets JSON' not in script.text
    assert 'Zastąp zapisane sekrety' in script.text
    assert "['suspend', 'Resume']" not in script.text
    assert "['suspend', 'Wstrzymaj']" in script.text
    assert "['resume', 'Wznów']" in script.text
    assert "['reset', 'Twardy reset']" in script.text
    assert 'destroy_unreferenced_disks' in script.text
    assert 'credential-secret-panel' in script.text
    assert 'Akceptuj certyfikat self-signed / niezaufany' in script.text
    assert 'template-variable-grid' in script.text
    assert 'data-workflow-row' in script.text
    assert 'deployment_ansible_enabled' in script.text
    assert 'function permissionPicker(' in script.text
    assert 'function permissionSummary(' in script.text
    assert 'function discoverVmOptions(' in script.text
    assert 'function storageLabel(' in script.text
    assert 'function hostnameValueFields(' in script.text
    assert 'function multiCheckboxField(' in script.text
    assert 'function toDateTimeLocal(' in script.text
    assert 'function showDeploymentDetails(' in script.text
    assert 'function assignHostname(' in script.text
    assert 'function assignIpAllocation(' in script.text
    assert 'function showProxmoxTask(' in script.text
    assert 'function stopTaskPolling(' in script.text
    assert 'Zmienne template JSON' not in script.text
    assert 'Schemat zmiennych JSON' not in script.text
    assert 'Definicja deploymentu JSON' not in script.text
    assert 'Workflow DAG JSON' not in script.text
    assert 'Wartości hostname JSON' not in script.text
    assert 'Desired variables JSON' not in script.text
    assert 'modal-form-error' in script.text
    assert "const THEME_KEY = 'cloudportal.console.theme';" in script.text
    assert 'function setTheme(' in script.text
    assert 'function setMobileMenu(' in script.text
    assert "dom.refreshView.addEventListener('click'" in script.text
    assert 'function copyText(' in script.text
    assert 'function setLoginMessage(' in script.text
    assert 'function friendlyApiText(' in script.text
    assert 'VALIDATION_FIELD_LABELS' in script.text
    assert "replace(/^Value error" in script.text
    assert "dom.modal.addEventListener('cancel'" in script.text
    assert "setMobileMenu(false)" in script.text
    assert '192.168.1.10' in script.text
    assert 'restoreVmFromBackup' in script.text
    assert "backups.restore" in script.text
    assert "Uruchom przywracanie" in script.text
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
    assert '.form-section' in stylesheet.text
    assert '.choice-fieldset' in stylesheet.text
    assert '.choice-grid' in stylesheet.text
    assert '.detail-section' in stylesheet.text
    assert '.task-progress' in stylesheet.text
    assert '.task-id-details' in stylesheet.text
    assert '.editor-card' in stylesheet.text
    assert '.table-toolbar' in stylesheet.text
    assert '.permission-group' in stylesheet.text
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
