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
    assert 'id="global-search-open"' in page.text
    assert 'id="global-search-dialog"' in page.text
    assert 'id="global-search-input"' in page.text
    assert 'src="./theme-init.js"' in page.text
    assert 'src="./core.js"' in page.text
    assert 'src="./loader.js"' in page.text
    assert 'src="./app.js"' not in page.text
    assert 'features/' not in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert page.headers['x-frame-options'] == 'DENY'
    assert page.headers['content-security-policy'] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )

    manifest_response = client.get('/ui/manifest.json')
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest['version'] == 1
    assert manifest['shared'] == sorted(manifest['shared'])
    assert manifest['scripts'] == sorted(manifest['scripts'])
    assert manifest['styles'] == sorted(manifest['styles'])
    assert manifest['shared'] == ['shared/platforms.js']
    assert {
        'features/dashboard.js',
        'features/identity.js',
        'features/credentials.js',
        'features/providers.js',
        'features/catalog.js',
        'features/blueprints.js',
        'features/ipam.js',
        'features/inventory.js',
        'features/deployments.js',
        'features/operations.js',
    } == set(manifest['scripts'])
    assert {
        'styles/features/identity.css',
        'styles/features/credentials.css',
        'styles/features/blueprints.css',
        'styles/features/inventory.css',
    } <= set(manifest['styles'])

    script_paths = ['core.js', 'loader.js', *manifest['shared'], *manifest['scripts'], 'app.js']
    style_paths = ['styles.css', *manifest['styles']]

    theme_script = client.get('/ui/theme-init.js')
    assert theme_script.status_code == 200
    assert theme_script.headers['content-type'].startswith(('text/javascript', 'application/javascript'))
    assert "cloudportal.console.theme" in theme_script.text
    assert "document.documentElement.dataset.theme" in theme_script.text

    scripts = {path: client.get('/ui/' + path) for path in script_paths}
    styles = {path: client.get('/ui/' + path) for path in style_paths}
    for response in scripts.values():
        assert response.status_code == 200
        assert response.headers['content-type'].startswith(('text/javascript', 'application/javascript'))
    for response in styles.values():
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/css')

    script = '\n'.join(response.text for response in scripts.values())
    stylesheet = '\n'.join(response.text for response in styles.values())
    bootstrap = scripts['app.js'].text
    core = scripts['core.js'].text
    loader = scripts['loader.js'].text

    assert "fetch('./manifest.json'" in loader
    assert "loadFeatureScript('app.js')" in loader
    assert 'manifest.shared' in loader
    assert 'loadFeatureStyle' in loader

    assert "const API = '/api/v1';" in core
    assert 'function registerView(' in core
    assert 'function registerCommand(' in core
    assert 'function registerExtension(' in core
    assert 'function emitUiEvent(' in core
    assert 'const routes = [];' in core
    assert 'const views = Object.create(null);' in core
    assert len(bootstrap.splitlines()) < 120
    assert 'async function usersView(' not in bootstrap
    assert 'async function blueprintsView(' not in bootstrap
    assert 'const views = {' not in bootstrap

    for path in script_paths:
        if path.startswith('features/'):
            assert 'registerView({' in scripts[path].text

    assert 'CREDENTIAL_TYPE_CONFIG' in script
    assert 'Secrets JSON' not in script
    assert 'Zastąp zapisane sekrety' in script
    assert "['suspend', 'Resume']" not in script
    assert "'suspend', 'Wstrzymaj'" in script
    assert "'resume', 'Wznów'" in script
    assert "'reset', 'Twardy reset'" in script
    assert 'destroy_unreferenced_disks' in script
    assert 'credential-secret-panel' in script
    assert 'Akceptuj certyfikat self-signed / niezaufany' in script
    assert 'template-variable-grid' in script
    assert 'data-workflow-row' in script
    assert 'deployment_ansible_enabled' in script
    assert 'function permissionPicker(' in script
    assert 'function permissionSummary(' in script
    assert 'function discoverVmOptions(' in script
    assert 'function storageLabel(' in script
    assert 'function hostnameValueFields(' in script
    assert 'function proxmoxBlueprintForm(' in script
    assert 'const workflowGraph = node(' in script
    assert 'const syncWorkflowGraph = () =>' in script
    assert 'const moveWorkflowRow = (row, direction) =>' in script
    assert 'workflow-dag-card' in script
    assert 'workflow-preview-visual' in script
    assert 'Szybki Blueprint Proxmox' in script
    assert 'Obraz / szablon Proxmox' in script
    assert 'Wzorzec nazwy hosta' in script
    assert 'Tagi Proxmox' in script
    assert 'Serwery DNS' in script
    assert "'set_tags'" in script
    assert 'function multiCheckboxField(' in script
    assert 'function toDateTimeLocal(' in script
    assert 'table-search-empty' in script
    assert 'function showDeploymentDetails(' in script
    assert 'function assignHostname(' in script
    assert 'function assignIpAllocation(' in script
    assert 'function showProxmoxTask(' in script
    assert 'function showVmDetailsPage(' in script
    assert 'function vmDetailActions(' in script
    assert 'function vmSnapshotsContent(' in script
    assert 'function vmBackupsContent(' in script
    assert 'function vmAuditContent(' in script
    assert "['overview', 'Przegląd']" in script
    assert "selectField('Powtarzanie', 'interval_preset'" in script
    assert "class: 'advanced-options wide'" in script
    assert 'function stopTaskPolling(' in script
    assert "registerExtension('global-search'" in script
    assert 'function loadIndex(' in script
    assert 'function renderSearch(' in script
    assert "event.key.toLocaleLowerCase() === 'k'" in script
    assert 'Zmienne template JSON' not in script
    assert 'Schemat zmiennych JSON' not in script
    assert 'Definicja deploymentu JSON' not in script
    assert 'Workflow DAG JSON' not in script
    assert 'Wartości hostname JSON' not in script
    assert 'Desired variables JSON' not in script
    assert 'modal-form-error' in script
    assert "const THEME_KEY = 'cloudportal.console.theme';" in script
    assert 'function setTheme(' in script
    assert 'function setMobileMenu(' in script
    assert "dom.refreshView.addEventListener('click'" in script
    assert 'function copyText(' in script
    assert 'function setLoginMessage(' in script
    assert 'function formFieldLabel(' in script
    assert 'function friendlyApiText(' in script
    assert 'VALIDATION_FIELD_LABELS' in script
    assert "replace(/^Value error" in script
    assert "dom.modal.addEventListener('cancel'" in script
    assert "setMobileMenu(false)" in script
    assert '192.168.1.10' in script
    assert 'restoreVmFromBackup' in script
    assert "backups.restore" in script
    assert "Uruchom przywracanie" in script
    assert 'consoleRfb' in script
    assert 'rfb_module' in script
    assert 'ws_path' in script
    assert "window.open('about:blank'" not in script

    assert ':root {' in stylesheet
    assert 'html[data-theme="dark"]' in stylesheet
    assert '--sidebar-bg:' in stylesheet
    assert '.sidebar-backdrop' in stylesheet
    assert '.credential-secret-panel' in stylesheet
    assert '.credential-tls-control' in stylesheet
    assert '.form-section' in stylesheet
    assert '.required-mark' in stylesheet
    assert 'input:user-invalid' in stylesheet
    assert '.choice-fieldset' in stylesheet
    assert '.choice-grid' in stylesheet
    assert '.detail-section' in stylesheet
    assert '.task-progress' in stylesheet
    assert '.advanced-options' in stylesheet
    assert '.task-id-details' in stylesheet
    assert '.editor-card' in stylesheet
    assert '.designer-heading' in stylesheet
    assert '.designer-subsection' in stylesheet
    assert '.workflow-preview' in stylesheet
    assert '.workflow-dag' in stylesheet
    assert '.workflow-dag-columns' in stylesheet
    assert '.workflow-preview-visual' in stylesheet
    assert '.blueprint-run-summary' in stylesheet
    assert '.table-toolbar' in stylesheet
    assert '.permission-group' in stylesheet
    assert '.modal-form-error' in stylesheet
    assert '.form-error.success' in stylesheet
    assert '.clipboard-fallback' in stylesheet
    assert '.toast.error' in stylesheet
    assert '.toast-close' in stylesheet
    assert '.modal.modal-wide' in stylesheet
    assert '.global-search-trigger' in stylesheet
    assert '.global-search-dialog' in stylesheet
    assert '.global-search-result' in stylesheet
    assert '.vm-detail-header' in stylesheet
    assert '.vm-tabs' in stylesheet
    assert '.vm-overview-grid' in stylesheet


def test_web_console_is_not_added_to_openapi_contract(client):
    paths = client.get('/openapi.json').json()['paths']

    assert '/' not in paths
    assert '/ui/' not in paths
