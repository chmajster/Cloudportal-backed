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
    assert 'id="global-search-close"' in page.text
    assert 'id="global-search-title"' in page.text
    assert 'id="sidebar-profile"' in page.text
    assert 'class="page-context"' in page.text
    assert 'href="./favicon.ico"' in page.text
    assert 'type="image/x-icon"' in page.text
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
    assert manifest['shared'] == ['shared/icons.js', 'shared/platforms.js']
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
        'features/search.js',
        'features/tools.js',
        'features/updates.js',
    } == set(manifest['scripts'])
    assert {
        'styles/features/identity.css',
        'styles/features/credentials.css',
        'styles/features/blueprints.css',
        'styles/features/inventory.css',
        'styles/features/dashboard.css',
        'styles/features/tools.css',
        'styles/features/updates.css',
    } <= set(manifest['styles'])

    script_paths = ['core.js', 'loader.js', *manifest['shared'], *manifest['scripts'], 'app.js']
    style_paths = ['styles.css', *manifest['styles']]

    favicon = client.get('/ui/favicon.ico')
    assert favicon.status_code == 200
    assert favicon.headers['content-type'].startswith('image/')
    assert len(favicon.content) > 1000

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
    assert 'route.navigation !== false' in core
    assert 'currentRoute?.navigationParent === route.id' in core
    assert 'appRouteIcon(route)' in core
    assert "text: route.icon" not in core
    assert 'function renderSidebarProfile()' in core
    assert "registerCommand('users.create'" in script
    assert "registerCommand('tokens.create'" in script
    assert "registerCommand('providers.create'" in script
    assert "registerCommand('blueprints.create'" in script
    assert "registerCommand('deployments.create'" in script
    assert "registerCommand('deployments.open'" in script
    assert 'function navigationGroup(' in core
    assert "'aria-current': exact ? 'page' : null" in core
    assert "appIcon('search')" in core
    assert "appIcon('refresh')" in core
    assert len(bootstrap.splitlines()) < 120
    assert 'async function usersView(' not in bootstrap
    assert 'async function blueprintsView(' not in bootstrap
    assert 'const views = {' not in bootstrap

    for path in script_paths:
        if path.startswith('features/'):
            assert 'registerView({' in scripts[path].text or 'registerExtension(' in scripts[path].text

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
    assert 'function splitProxmoxEndpoint(' in script
    assert 'function buildProxmoxEndpoint(' in script
    assert 'function splitProxmoxUsername(' in script
    assert 'function buildProxmoxUsername(' in script
    assert "label: 'Port'" in script
    assert 'default: 8006' in script
    assert "label: 'Realm'" in script
    assert "default: 'pam'" in script
    assert "placeholder: 'root'" in script
    assert 'if (proxmoxIdentityRow) secretPanel.append(proxmoxIdentityRow);' in script
    assert 'Sprawdź połączenie' in script
    assert '/credentials/proxmox/test' in script
    assert 'renderProxmoxConnectionResult' in script
    assert 'Czas odpowiedzi' in script
    assert 'Wersja Proxmox VE' in script
    assert 'function proxmoxDuplicateTokenDetail(' in script
    assert 'function applyProxmoxDuplicateTokenSuggestion(' in script
    assert 'function setProxmoxTokenVerificationState(' in script
    assert 'function proxmoxTokenRejectedDetail(' in script
    assert 'Sprawdzanie, czy token' in script or 'sprawdzi, czy nazwa tokenu jest duplikatem' in script
    assert 'Proponowana nowa nazwa:' in script
    assert 'Duplikat został potwierdzony' in script
    assert 'Została wpisana do formularza' in script
    assert "name: 'port'" not in script
    assert 'template-variable-grid' in script
    assert 'createProxmoxTemplatePicker' in script
    assert 'Podgląd Terraform' in script
    assert "'/source'" in script or "+ '/source'" in script
    assert '.terraform-template-code' in stylesheet
    assert 'Obraz / szablon Proxmox' in script
    assert '/providers/' in script and '/templates' in script
    assert '.proxmox-template-card' in stylesheet
    assert 'data-workflow-row' in script
    assert 'deployment_ansible_enabled' in script
    assert 'function permissionPicker(' in script
    assert 'function permissionSummary(' in script
    assert 'account-overview-grid' in script
    assert 'account-permissions-panel' in script
    assert 'Skuteczne uprawnienia' in script
    assert 'Ochrona dostępu' in script
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
    assert 'Nowy szablon Terraform / OpenTofu' in script
    assert "registerCommand('blueprints.proxmoxTemplateWizard'" in script
    assert "registerCommand('blueprints.execute'" in script
    assert 'SRLXXX' in script
    assert 'hostname_next_number' in script
    assert 'existing_hostname_pattern' in script
    assert 'Role zarządzające szablonem' in script
    assert 'Jedna rola może zarządzać tylko jednym szablonem.' in script
    assert 'canManageBlueprintByRole' in script
    assert 'Licznik jest tylko informacyjny i nie jest cofany podczas edycji szablonu.' in script
    assert "executor: data.get('executor')" in script
    assert 'Tagi Proxmox' in script
    assert 'Serwery DNS' in script
    assert "'set_tags'" in script
    assert 'function multiCheckboxField(' in script
    assert 'function toDateTimeLocal(' in script
    assert 'table-search-empty' in script
    assert 'function tablePreferenceKey(' in script
    assert 'function readTablePreferences(' in script
    assert 'table-sort-button' in script
    assert 'table-column-picker' in script
    assert 'table-page-size' in script
    assert "density === 'compact'" in script
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
    assert "id: 'updates'" in script
    assert "navigationParent: 'tools'" in script
    assert 'navigation: false' in script
    assert "registerView({ id: 'tools'" in script
    assert "iconName: 'wrench'" in script
    assert 'Centrum narzędzi' in script
    assert 'Otwórz Auto-update' in script
    assert '← Narzędzia' in script
    assert 'Aktualizuj teraz' in script
    assert 'Zainstaluj nowszy commit' in script
    assert 'Commit zainstalowany' in script
    assert 'Commit kanału' in script
    assert 'Model wersji' in script
    assert 'Git commit' in script
    assert 'Updater nie wykona downgrade’u' in script
    assert 'UPDATE_PHASES' in script
    assert 'updatePipeline' in script
    assert 'Polityka auto-update' in script
    assert 'Backup przed wdrożeniem' in script
    assert 'Następne sprawdzenie' in script
    assert 'update-interval-presets' in script
    assert "'X-Update-Status-Token': token" in script
    assert 'Witaj, ${greetingName}!' in script
    assert 'dashboard-metrics' in script
    assert 'dashboard-metric-action' in script
    assert "runCommand('deployments.open'" in script
    assert "entity: { type: 'deployment', item }" in script
    assert 'toolStatusDot' in script
    assert 'dashboard-quick-actions' in script
    assert 'Ostatnie wdrożenia' in script
    assert "api('/providers?limit=200')" in script
    assert "api('/deployments?limit=200')" in script
    assert "api('/jobs?limit=200')" in script
    assert "api('/users?limit=200')" in script
    assert "registerExtension('global-search'" in script
    assert 'function loadIndex(' in script
    assert 'function renderSearch(' in script
    assert "kind: 'Narzędzie'" in script
    assert 'route.navigation === false && route.navigationParent' in script
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
    assert '--radius-sm: 13px;' in stylesheet
    assert r'--radius: 20px;\\n  --radius-sm' not in stylesheet
    assert '--success-border:' in stylesheet
    assert '.nav-group-label' in stylesheet
    assert '.status-dot.warn' in stylesheet
    assert '.sidebar-backdrop' in stylesheet
    assert '.credential-secret-panel' in stylesheet
    assert '.credential-tls-control' in stylesheet
    assert '.credential-endpoint-row' in stylesheet
    assert '.credential-user-row' in stylesheet
    assert '.credential-connection-check' in stylesheet
    assert '.credential-connection-result' in stylesheet
    assert '.credential-connection-result.pending' in stylesheet
    assert '.proxmox-token-verification' in stylesheet
    assert '@keyframes proxmox-token-check-spin' in stylesheet
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
    assert '.table-sort-button' in stylesheet
    assert '.table-column-picker' in stylesheet
    assert '.table-pagination' in stylesheet
    assert '.advanced-table.compact' in stylesheet
    assert '.advanced-table {' in stylesheet
    assert '.permission-group' in stylesheet
    assert '.account-overview-grid' in stylesheet
    assert '.account-profile-head' in stylesheet
    assert '.account-meta-grid' in stylesheet
    assert '.account-permissions-grid' in stylesheet
    assert 'column-count: 3' in stylesheet
    assert '.modal-form-error' in stylesheet
    assert '.form-error.success' in stylesheet
    assert '.clipboard-fallback' in stylesheet
    assert '.toast.error' in stylesheet
    assert '.toast-close' in stylesheet
    assert '.modal.modal-wide' in stylesheet
    assert '.global-search-trigger' in stylesheet
    assert '.global-search-close' in stylesheet
    assert '.sr-only' in stylesheet
    assert '.sidebar-profile-card' in stylesheet
    assert '.nav-link.active::before' in stylesheet
    assert '.dashboard-metrics' in stylesheet
    assert '.dashboard-quick-action' in stylesheet
    assert '.dashboard-metric-action' in stylesheet
    assert '.global-search-dialog' in stylesheet
    assert '.global-search-result' in stylesheet
    assert '.vm-detail-header' in stylesheet
    assert '.vm-tabs' in stylesheet
    assert '.vm-overview-grid' in stylesheet
    assert '.tools-hero' in stylesheet
    assert '.tools-grid' in stylesheet
    assert '.tool-card' in stylesheet
    assert '.update-hero' in stylesheet
    assert '.update-version-grid' in stylesheet
    assert '.update-pipeline' in stylesheet
    assert '.update-master-toggle' in stylesheet
    assert '.update-safety-grid' in stylesheet


def test_web_console_is_not_added_to_openapi_contract(client):
    paths = client.get('/openapi.json').json()['paths']

    assert '/' not in paths
    assert '/ui/' not in paths
