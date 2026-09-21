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
    assert manifest['shared'] == [
        'shared/http.js',
        'shared/icons.js',
        'shared/navigation.js',
        'shared/page-layout.js',
        'shared/page-surfaces.js',
        'shared/platforms.js',
        'shared/polling.js',
        'shared/status.js',
    ]
    assert {
        'features/dashboard.js',
        'features/identity.js',
        'features/credentials.js',
        'features/providers.js',
        'features/catalog.js',
        'features/blueprint-provisioning-guards.js',
        'features/blueprint-runtime-apmid.js',
        'features/blueprint-wizard-core.js',
        'features/blueprint-wizard-hostname.js',
        'features/blueprint-wizard-network.js',
        'features/blueprint-wizard.js',
        'features/blueprints.js',
        'features/hostnames.js',
        'features/ipam.js',
        'features/inventory.js',
        'features/deployments.js',
        'features/operations.js',
        'features/search.js',
        'features/settings.js',
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
        'styles/features/settings.css',
        'styles/features/page-surfaces.css',
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
    assert "split('/page/')[0]" in core
    assert 'dismissCloudportalSurfaceForNavigation' in core
    assert 'closeCloudportalSurface' in core
    assert 'function modalSurfaceOpen()' in script
    assert 'dom.modal.showModal = renderSurface' in script
    assert 'history.pushState(' in script
    assert 'cloudportalPageSurface' in script
    assert "window.addEventListener('popstate'" in script
    assert '.page-surface-navigation' in stylesheet
    assert '.page-surface .modal-shell' in stylesheet
    assert '.page-surface .modal-actions' in stylesheet
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
    assert "api('/blueprints?available=true&limit=200')" in script
    assert "label: 'Produkty'" in script
    assert "label: 'Moje zasoby'" in script
    assert "id: 'my-resources'" in script
    assert "function productResourceTabs(" in script
    assert "function myResourcesView(repairInventory = true)" in script
    assert "api('/inventory/reconcile', { method: 'POST', body: {} })" in script
    assert "Odbudowano inventory dla " in script
    assert "function resourceSummaryCard(" in script
    assert "function resourceEmptyState(" in script
    assert "function resourceSection(" in script
    assert "my-resources-summary-card" in script
    assert "my-resources-body" in script
    assert "my-resources-empty-icon" in script
    assert "my-resources-section-icon" in script
    assert "document.getElementById(sectionId)?.scrollIntoView" in script
    assert "button('Utwórz VM'" in script
    assert "button('Zarządzaj VM'" in script
    assert "button('Anuluj'" in script
    assert "button('Anuluj zadanie'" in script
    assert "Anulowanie rozpoczęte." in script
    assert "item.status === 'cancelling'" in script
    assert "runCommand('inventory.openVm', item, 'overview', 'my-resources')" in script
    assert "runCommand('inventory.openVm', vm, 'overview', 'my-resources')" in script
    assert "Wdrożenia i operacje" in script
    assert 'Brak gotowych Blueprintów.' in script
    assert '.product-grid' in stylesheet
    assert '.product-card' in stylesheet
    assert '.job-log-status' in stylesheet
    assert '.job-live-log' in stylesheet
    assert '.product-resource-tabs' in stylesheet
    assert '.my-resources-page-head' in stylesheet
    assert '.my-resources-body' in stylesheet
    assert '.my-resources-summary-card' in stylesheet
    assert '.my-resources-summary-icon' in stylesheet
    assert '.my-resources-section-icon' in stylesheet
    assert '.my-resources-empty-icon' in stylesheet
    assert '.my-resource-grid' in stylesheet
    assert 'function navigationGroup(' in core
    assert 'function navigationGroupRank(' in core
    assert 'function navigationRouteRank(' in core
    assert "group === 'Operacje' ? 1" in core
    assert "window.uiResolveView" in core
    assert "window.uiRoutePath" in core
    assert "window.uiNavigationOrder" in script
    assert "window.cloudportalHttp" in script
    assert "window.pollingService" in script
    assert "window.uiStatusMeta" in script
    assert "window.uiPageHeading" in script
    assert "'my-resources': '/resources'" in script
    assert "'blueprints': '/blueprints'" in script or "blueprints: '/blueprints'" in script
    assert "'Administracja'" in script
    assert '.page-heading' in stylesheet
    assert '.filter-bar' in stylesheet
    assert '.ui-tabs' in stylesheet
    assert "'aria-current': exact ? 'page' : null" in core
    assert "appIcon('search')" in core
    assert "appIcon('refresh')" in core
    assert "cancelling: 'Anulowanie…'" in core
    assert "'cancelling'" in core
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
    assert "label: 'Hasło → wygeneruj i wgraj klucz (zalecane)'" in script
    assert 'Automatyczne wygenerowanie i wgranie klucza SSH' in script
    assert '/credentials/ssh/host-key' in script
    assert '/credentials/ssh/bootstrap' in script
    assert 'ssh_host_key_confirmed' in script
    assert 'Hasło służy tylko do instalacji klucza' in script
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
    assert "registerCommand('ansible.run'" in script
    assert "'/catalog/' + kind + '/'" in script
    assert "item.enabled === false ? 'Włącz' : 'Wyłącz'" in script
    assert "item.enabled !== false" in script
    assert 'ansible-playbook-info' in script
    assert 'Wszystkie kategorie' in script
    assert '.ansible-playbook-info' in stylesheet
    assert 'createProxmoxTemplatePicker' in script
    assert "function createProxmoxTemplatePicker(container, rows, selectedId = '', selectedNode = '', onSelect = null)" in script
    assert "if (!nodeSelect.value) nodeSelect.value = String(row.node);" in script
    assert "await loadNodeResources();" in script
    assert 'Podgląd Terraform' in script
    assert 'Podgląd playbooka' in script
    assert "'/ansible/playbooks/'" in script
    assert '.ansible-playbook-code' in stylesheet
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
    assert 'Obraz / szablon Proxmox' in script
    assert 'Sposób nadawania hostname' in script
    assert 'Nowy szablon Terraform / OpenTofu' in script
    assert "registerCommand('blueprints.proxmoxTemplateWizard'" in script
    assert "registerCommand('blueprints.execute'" in script
    assert "window.BlueprintWizard.open()" in script
    assert "registerExtension('blueprint-wizard-core'" in script
    assert "registerExtension('blueprint-wizard-hostname'" in script
    assert "Pattern hostname z Generatora" in script
    assert "'hostname_scheme_picker'" in script
    assert "button('Odśwież patterny'" in script
    assert "Lista patternów hostname odświeżona." in script
    assert "registerExtension('blueprint-wizard-network'" in script
    assert "registerExtension('blueprint-wizard'" in script
    assert "'Podstawy', 'Podstawowe informacje'" in script
    assert "'Platforma', 'Platforma i źródło VM'" in script
    assert "'VM', 'Parametry VM'" in script
    assert "'Hostname', 'Nazwa hosta'" in script
    assert "'Sieć', 'Sieć'" in script
    assert "'Konfiguracja', 'Konfiguracja systemu'" in script
    assert "'Workflow', 'Workflow'" in script
    assert "'Dostęp', 'Dostęp i bezpieczeństwo'" in script
    assert 'function dualListGroup(' in script
    assert "'data-dual-list-name': name" in script
    assert "dualListGroup('Dozwolone role'" in script
    assert "dualListGroup('Role zarządzające Blueprintem'" in script
    assert "new Set(['Administrator', 'Infrastructure Administrator'])" in script
    assert "state.managerRoleIds = data.managerRoles" in script
    assert "const defaultManagerRoleIds = item" in script
    assert "dualListGroup('Dozwoleni użytkownicy'" in script
    assert "'Dodaj zaznaczone'" in script
    assert "'Dodaj wszystkie'" in script
    assert "'Usuń zaznaczone'" in script
    assert "'Usuń wszystkie'" in script
    assert "'Podsumowanie', 'Podsumowanie'" in script
    assert "Blueprint definiuje sposób automatycznego tworzenia maszyny wirtualnej i jej konfiguracji." in script
    assert "state.slug = parts.core.slugify" in script
    assert "api('/providers/' + provider.id + '/nodes')" in script
    assert "api('/providers/' + provider.id + '/templates')" in script
    assert "VMID " in script
    assert "presetButton('small', 'Mała', 1, 2048, 20)" in script
    assert "presetButton('standard', 'Standardowa', 2, 4096, 40)" in script
    assert "presetButton('large', 'Duża', 4, 8192, 80)" in script
    assert "hostname_scheme_id" in script
    assert "hostname_values" in script
    assert "ipam_pool_id" in script
    assert "guest_credential_id" in script
    assert "Credential dostępu do VM" in script
    assert "supports_cloud_init_ssh_key === true" in script
    assert "requiredExecutionPermissions" in script
    assert "'jobs.execute'" in script
    assert "'terraform.execute'" in script
    assert "'deployments.create'" in script
    assert "'deployments.destroy'" in script
    assert "'snapshots.create'" in script
    assert "'ipam.release'" in script
    assert "Brak uprawnień do uruchomienia" in script
    assert script.count("workflowNeedsTags(") >= 2
    assert "Brak credentiali SSH zawierających klucz prywatny" in script
    assert "QEMU Guest Agent niedostępny" in script
    assert "storage obsługującego snippets" in script
    assert "Credential ustawiany na VM" in script
    assert "blueprint-wizard-ssh-credential-users" in script
    assert "wybrać użytkownika z zapisanych Credentiali" in script
    assert "zainstaluj qemu-guest-agent przez cloud-init" in script
    assert "install_qemu_guest_agent" in script
    assert "cloud_init_snippet_storage" in script
    assert "snippetStorages" in script
    assert "ansibleVariables.hostname = '{{ hostname }}'" in script
    assert "add('clone', 'clone_vm')" not in script
    assert "add('cloud_init', 'cloud_init')" not in script
    assert "add('apply', 'terraform_apply')" in script
    assert "add('guest_ip', 'wait_for_ip')" in script
    assert "Legacy / niedostępne dla" in script
    assert "Tryb zaawansowany" in script
    assert "Conditions (JSON)" in script
    assert "Opcjonalne ustawienia dostępu" in script
    assert "Blueprint został utworzony i jest gotowy do użycia." in script
    assert 'SRLXXX' in script
    assert 'Sposób nadawania hostname' in script
    assert 'Pattern hostname' in script
    assert "button('Nowy pattern', () => hostnameSchemeForm(), 'primary')" in script
    assert 'const schemeId = item == null ? null : Number(item.id);' in script
    assert 'const editing = Number.isInteger(schemeId) && schemeId > 0;' in script
    assert "editing ? `/hostname-schemes/${schemeId}` : '/hostname-schemes'" in script
    assert 'Podgląd hostname' in script
    assert 'data-hostname-preview' in script
    assert 'data-existing-hostname-preview' in script
    assert 'hostname_scheme_name' not in script
    assert 'existing_hostname_scheme_name' not in script
    assert 'Slug / identyfikator szablonu' in script
    assert "name: blueprintName" in script
    assert 'hostname_next_number' in script
    assert 'Nazwa deploymentu i nazwa VM będą generowane automatycznie z wybranego wzorca hostname.' in script
    assert "name: hostnameSchemeId ? '{{ hostname }}' : form.elements.deployment_name.value" in script
    assert "deploymentVariables.name = '{{ hostname }}'" in script
    assert "field('Nazwa wdrożenia', 'deployment_name'" in script
    assert 'existing_hostname_pattern' in script
    assert 'Role zarządzające szablonem' in script
    assert 'Jedna rola może zarządzać tylko jednym szablonem.' in script
    assert 'canManageBlueprintByRole' in script
    assert 'Generator hostname' in script
    assert "id: 'hostnames'" in script
    assert "navigationParent: 'tools'" in script
    assert "label: 'Generator hostname'" in script
    assert 'function hostnameGeneratorTool(' in script
    assert 'Użyj w Blueprint' in script
    assert 'options.hostnameSchemeId || deployment.hostname_scheme_id' in script
    assert 'function hostnameSchemePreview(' in script
    assert 'Pattern hostname zapisany i jest dostępny w Blueprintach.' in script
    assert 'hostname staje się nazwą deploymentu i VM' in script
    assert 'Licznik jest tylko informacyjny i nie jest cofany podczas edycji szablonu.' in script
    assert "registerView({ id: 'settings'" in script
    assert "'Wygląd'" in script
    assert "'Konto i sesja'" in script
    assert "'System'" in script
    assert "'Aktualizacje'" in script
    assert "'Bezpieczeństwo'" in script
    assert "api('/updates/settings')" in script
    assert "api('/health'" in script
    assert ".settings-grid" in stylesheet
    assert ".settings-choice" in stylesheet
    assert ".settings-ldap-panel" in stylesheet
    assert ".settings-ldap-hero" in stylesheet
    assert ".settings-ldap-overview" in stylesheet
    assert ".settings-ldap-section" in stylesheet
    assert ".settings-ldap-detail-grid" in stylesheet
    assert ".settings-ldap-jit" in stylesheet
    assert ".settings-ldap-filter-grid" in stylesheet
    assert ".settings-ldap-filter-card" in stylesheet
    assert 'Przykładowe filtry użytkownika' in script
    assert 'Testuj połączenie' in script
    assert 'Konfiguracja LDAP' in script
    assert '/settings/ldap/test' in script
    assert 'JIT provisioning i RBAC' in script
    assert "function environmentTool(config)" in script
    assert "function environmentsView()" in script
    assert "button('Zarządzaj środowiskami'" in script
    assert "id: 'environments'" in script
    assert "label: 'Środowiska'" in script
    assert "navigationParent: 'tools'" in script
    assert "Zapisz środowiska" in script
    assert "function apmidView()" in script
    assert "function apmidTool(config)" in script
    assert "function apmidInputForm(" in script
    assert "const IMMUTABLE_APMIDS = new Set(['LEO'])" in script
    assert "Domyślny APMID systemowy — nie można edytować ani usunąć" in script
    assert "Domyślny · zablokowany" in script
    assert "apmid: 'LEO'" in script
    assert "apmids.includes('LEO') ? 'LEO' : apmids[0]" in script
    assert "button('Otwórz listę APMID'" in script
    assert "button('Dodaj APMID'" in script
    assert "button('Edytuj'" in script
    assert "button('Usuń'" in script
    assert "id: 'apmid'" in script
    assert "function hostnameDefaultsTool(config)" in script
    assert "function hostnameDefaultsView()" in script
    assert "button('Konfiguruj Location i Role'" in script
    assert "id: 'hostname-defaults'" in script
    assert "Location i Role są ustawiane globalnie." in script
    assert "!['location', 'role'].includes(token)" in script
    assert "navigationParent: 'tools'" in script
    assert '/settings/vm-classification' in script
    assert "/settings/blueprints" in script
    assert "Automatyczne zatwierdzanie wykonania" in script
    assert "Uruchamia bez pytania o approval" in script
    assert "Włącz auto-approval" in script
    assert "Wyłącz auto-approval" in script
    assert "Timeout ręcznego approval" in script
    assert "Zmień timeout" in script
    assert "Approval wg polityki globalnej" in script
    assert "workflowChoicesForProvider" in script
    assert "Legacy / niedostępne dla" in script
    assert '.environment-manager-panel' in stylesheet
    assert '.environment-manager-grid' in stylesheet
    assert '.environment-manager-card' in stylesheet
    assert '.apmid-list' in stylesheet
    assert '.apmid-inline-form' in stylesheet
    assert '.hostname-defaults-form' in stylesheet
    assert "environment: 'environment'" in script
    assert "apmid: 'apmid'" in script
    assert "select_environment_on_execute: 'selectEnvironmentOnExecute'" in script
    assert "select_apmid_on_execute: 'selectApmidOnExecute'" in script
    assert 'Wybieraj Environment podczas tworzenia VM' in script
    assert 'Wybieraj APMID podczas tworzenia VM' in script
    assert 'Parametry wybierane przy użyciu Blueprintu' in script
    assert 'function fixedApmid(item)' in script
    assert 'function fixedEnvironment(item)' in script
    assert 'function allowsRuntimeEnvironment(item)' in script
    assert "registerExtension('blueprint-runtime-apmid'" in script
    assert "'/vm-classification/options'" in script
    assert "'runtime_apmid'" in script
    assert "'runtime_environment'" in script
    assert 'Ten Blueprint pozwala wybrać APMID podczas tworzenia VM.' in script
    assert 'Ten Blueprint pozwala wybrać środowisko podczas tworzenia VM.' in script
    assert "deployment.apmid = String(state.apmid).trim().toUpperCase()" in script
    assert "deployment.environment = String(state.environment).trim().toLowerCase()" in script
    assert "deployment.select_apmid_on_execute = Boolean(state.selectApmidOnExecute)" in script
    assert "deployment.select_environment_on_execute = Boolean(state.selectEnvironmentOnExecute)" in script
    assert "apmid + '.' + environment" in script
    assert "'apmid-' + apmid" in script
    assert "'env-' + environment" in script
    assert 'JIT provisioning i RBAC' in script
    assert 'Hasło pozostaje wyłącznie w LDAP' in script
    assert "executor: data.get('executor')" in script
    assert 'Tagi Proxmox' in script
    assert 'Serwery DNS' in script
    assert "'set_tags'" not in script
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
    assert "Zatwierdź i uruchom" in script
    assert "/approve" in script
    assert "waiting_approval" in script
    assert 'function assignHostname(' in script
    assert 'function assignIpAllocation(' in script
    assert 'function showProxmoxTask(' in script
    assert 'function showVmDetailsPage(' in script
    assert 'let jobLogPollNonce = 0' in script
    assert 'let myResourcesPollTimer = null' in script
    assert "window.setTimeout(poll, 1500)" in script
    assert "!logOutput.isConnected" in script
    assert "dom.content.querySelector('.my-resources-page-head')" in script
    assert "VM zsynchronizowana z inventory" in script
    assert "Po zakończeniu Terraform backend automatycznie doda VM" in script
    assert "parentView = null" in script
    assert "returnView === 'my-resources' ? 'Moje zasoby' : 'Zasoby'" in script
    assert "'start', 'Uruchom'" in script
    assert "'shutdown', 'Wyłącz'" in script
    assert "'reboot', 'Restart'" in script
    assert "'reset', 'Twardy reset'" in script
    assert "button('Konsola'" in script
    assert "button('Snapshot'" in script
    assert "button('Backup'" in script
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
    assert 'function normalizedUpdateStatus(' in script
    assert 'function captureTechnicalLogView(' in script
    assert 'function restoreTechnicalLogView(' in script
    assert 'distanceFromBottom <= 24' in script
    assert 'log.scrollTop = log.scrollHeight' in script
    assert 'log.scrollTop = Math.min(previous.scrollTop, maxScrollTop)' in script
    assert 'let updateStartPending = false' in script
    assert 'status.operation_active' in script
    assert "result.already_running" in script
    assert "includes('already in progress')" in script
    assert "status.phase === 'complete'" in script
    assert "status: 'success'" in script
    assert 'Polityka auto-update' in script
    assert 'Backup przed wdrożeniem' in script
    assert 'Następne sprawdzenie' in script
    assert 'update-interval-presets' in script
    assert "'X-Update-Status-Token': token" in script
    assert 'Witaj, ${greetingName}!' in script
    assert 'dashboard-metrics' in script
    assert 'function observabilityMetric(' in script
    assert 'Stan platformy' in script
    assert 'System działa poprawnie' in script
    assert 'Brak aktywnych alertów' in script
    assert 'Kopiuj metryki' in script
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
    assert 'function setDesktopSidebarCollapsed(' in script
    assert 'function toggleSidebar(' in script
    assert "SIDEBAR_COLLAPSED_KEY = 'cloudportal.console.sidebar.collapsed'" in script
    assert '.app-layout.sidebar-collapsed' in stylesheet
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
    assert '.ssh-key-bootstrap-panel' in stylesheet
    assert '.ssh-host-fingerprint' in stylesheet
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
    assert '.hostname-pattern-preview' in stylesheet
    assert '.hostname-generator-info' in stylesheet
    assert '.blueprint-run-summary' in stylesheet
    assert '.blueprint-wizard-shell' in stylesheet
    assert '.blueprint-wizard-steps' in stylesheet
    assert '.blueprint-wizard-select-card' in stylesheet
    assert '.blueprint-wizard-dual-fieldset' in stylesheet
    assert '.blueprint-wizard-dual-list' in stylesheet
    assert '.blueprint-wizard-dual-select' in stylesheet
    assert '.blueprint-wizard-dual-controls' in stylesheet
    assert '.blueprint-wizard-workflow-visual' in stylesheet
    assert '.blueprint-wizard-review' in stylesheet
    assert '@media (prefers-reduced-motion: reduce)' in stylesheet
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
    assert '.observability-hero' in stylesheet
    assert '.observability-metrics' in stylesheet
    assert '.observability-metric' in stylesheet
    assert '.observability-empty-state' in stylesheet
    assert '.observability-metrics-output' in stylesheet
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
    assert '.update-pipeline-step-copy' in stylesheet
    assert '.update-progress-status' in stylesheet
    assert "Aktualizacja gotowa — etapy rozpoczną się po uruchomieniu instalacji" in script
    assert "Etapy pozostają w kolejce do czasu rozpoczęcia aktualizacji" in script
    assert '.update-master-toggle' in stylesheet
    assert '.update-safety-grid' in stylesheet


def test_web_console_is_not_added_to_openapi_contract(client):
    paths = client.get('/openapi.json').json()['paths']

    assert '/' not in paths
    assert '/ui/' not in paths
