'use strict';
(() => {
function blueprintTemplateVariableField(name, spec, value) {
  const type = schemaType(spec);
  const label = FIELD_LABELS[name] || spec.title || name;
  const current = value ?? spec.default ?? '';
  const help = `Typ: ${type}. Możesz użyć wartości lub placeholdera, np. {{ cpu }}.`;
  const wrapper = field(label, `deployment_var_${name}`, {
    tag: type === 'array' ? 'textarea' : 'input',
    value: Array.isArray(current) ? current.join('\n') : String(current),
    wide: type === 'array' || ['ssh_public_key', 'subnet_id'].includes(name),
    help,
  });
  wrapper.dataset.blueprintTemplateVariable = name;
  return wrapper;
}
function readBlueprintTemplateVariables(root, template) {
  const result = {};
  const properties = template?.variables_schema?.properties || {};
  for (const [name, spec] of Object.entries(properties)) {
    const control = root.elements?.[`deployment_var_${name}`] || root.querySelector?.(`[name="deployment_var_${name}"]`);
    if (!control) continue;
    const raw = String(control.value ?? '').trim();
    if (!raw) continue;
    if (/{{\s*[^}]+\s*}}/.test(raw)) {
      result[name] = raw;
      continue;
    }
    const type = schemaType(spec);
    if (type === 'integer') result[name] = Number.parseInt(raw, 10);
    else if (type === 'number') result[name] = Number(raw);
    else if (type === 'boolean') result[name] = ['true', '1', 'tak', 'yes'].includes(raw.toLowerCase());
    else if (type === 'array') result[name] = splitValues(raw);
    else result[name] = raw;
  }
  return result;
}
function canManageBlueprintByRole(item) {
  const required = new Set((item?.manager_role_ids || []).map(Number));
  if (!required.size) return true;
  const owned = new Set((state.identity?.roles || []).map(role => Number(role.id)));
  return [...required].some(id => owned.has(id));
}

async function blueprintsView() {
  const [blueprintResult, roleResult] = await Promise.all([
    api('/blueprints?limit=200'),
    allowed('roles.read') ? api('/roles?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const blueprints = blueprintResult.items;
  const roleNames = new Map(roleResult.items.map(role => [Number(role.id), role.name]));
  const canDesignBlueprint = allowed('providers.read') && allowed('credentials.read') && allowed('terraform.read');
  const canQuickProxmox = canDesignBlueprint && allowed('hostnames.read') && allowed('ipam.read');
  const actions = [];
  if (allowed('blueprints.create') && canDesignBlueprint) {
    actions.push(button('Nowy Blueprint — kreator', () => window.BlueprintWizard.open(), 'primary'));
    if (window.ApplianceBlueprintUI && allowed('terraform.execute')) {
      actions.push(button('Importuj appliance OVA', () => window.ApplianceBlueprintUI.open().catch(error => toast(error.message, 'error'))));
    }
    if (window.BlueprintVRADesigner) actions.push(button('Designer vRA / YAML', () => window.BlueprintVRADesigner.open()));
  }
  dom.content.replaceChildren(heading('Wersjonowane definicje self-service. DAG, formularz zmiennych i provisioning są wykonywane przez wspólną warstwę API.', actions),
    table([
      { label: 'Blueprint', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: `${item.slug} · v${item.version}` })) },
      { label: 'Status', value: item => badge(statusLabel(item.is_active ? 'active' : 'inactive'), item.is_active ? 'ok' : 'danger') },
      { label: 'Widoczność', value: item => Object.entries(item.visibility).filter(([, value]) => value).map(([key]) => ({ backend: 'Backend', cloudportal: 'CloudPortal', api: 'API' }[key] || key)).join(', ') || '—' },
      { label: 'Kroki', value: item => item.workflow.length },
      { label: 'Zarządzanie', value: item => (item.manager_role_ids || []).length
        ? (item.manager_role_ids || []).map(id => roleNames.get(Number(id)) || ('Rola #' + id)).join(', ')
        : badge('Bez roli dedykowanej', 'warning') },
      { label: 'Zasady', value: item => node('div', { class: 'row-actions' }, window.BlueprintApprovalPolicyUI.badgeFor(item), item.recovery_policy === 'destroy_on_failure' ? badge('Usuń po błędzie', 'danger') : badge('Zachowaj po błędzie', 'info')) },
      { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
    ], blueprints, item => {
      const result = [];
      const executionControl = window.BlueprintProvisioningGuards.executionControl(item, () => executeBlueprint(item));
      if (executionControl) result.push(executionControl);
      const canManage = canManageBlueprintByRole(item);
      if (allowed('blueprints.update') && canManage && canQuickProxmox && item.deployment?.template === 'proxmox-vm') result.push(button('Szybka edycja', () => proxmoxBlueprintForm(item)));
      if (allowed('blueprints.update') && canManage && canDesignBlueprint) { if (window.BlueprintVRADesigner) result.push(button('Designer vRA / YAML', () => window.BlueprintVRADesigner.open(item))); result.push(button('Edytuj', () => window.BlueprintWizard.open({ item }))); }
      if (allowed('blueprints.delete') && canManage) result.push(button('Usuń', () => confirmAction('Usuń Blueprint', `Definicja ${item.name} zostanie usunięta. Istniejące wdrożenia zachowają snapshot.`, async () => { await api(`/blueprints/${item.id}`, { method: 'DELETE' }); toast('Blueprint usunięty.'); navigate('blueprints'); }), 'danger'));
      return result;
    }));
}
async function proxmoxBlueprintForm(item = null, options = {}) {
  const currentWorkflow = item?.workflow || [];
  const hasCloudInitStep = currentWorkflow.some(step => step.type === 'cloud_init');
  const hasAwxStep = currentWorkflow.some(step => step.type === 'register_awx');
  if (hasCloudInitStep && !hasAwxStep) return blueprintForm(item);
  try {
    const [providerResult, schemeResult, poolResult, playbookResult, credentialResult, roleResult, blueprintResult, vmClassification] = await Promise.all([
      api('/providers?limit=200'),
      api('/hostname-schemes?limit=200'),
      api('/ipam/pools?limit=200'),
      allowed('ansible.read') ? api('/ansible/playbooks') : Promise.resolve({ items: [] }),
      api('/credentials?limit=200'),
      allowed('roles.read') ? api('/roles?limit=200') : Promise.resolve({ items: [] }),
      allowed('blueprints.read') ? api('/blueprints?limit=200') : Promise.resolve({ items: [] }),
      allowed('settings.read')
        ? api('/settings/vm-classification')
        : Promise.resolve({ hostname_defaults: { location: 'wro', role: 'server' } }),
    ]);
    const providers = providerResult.items.filter(value => value.type === 'proxmox');
    if (!providers.length) throw new Error('Najpierw dodaj platformę Proxmox.');
    const schemes = schemeResult.items.filter(value => value.is_active);
    const pools = poolResult.items.filter(value => value.is_active);
    const playbooks = playbookResult.items.filter(value =>
      value.enabled !== false || value.id === item?.deployment?.ansible?.playbook);
    const credentials = credentialResult.items;
    const globalHostnameDefaults = vmClassification.hostname_defaults || { location: 'wro', role: 'server' };
    const managerPermissions = new Set(['blueprints.read', 'blueprints.update', 'blueprints.delete']);
    const dedicatedElsewhere = new Set(
      blueprintResult.items
        .filter(blueprint => Number(blueprint.id) !== Number(item?.id))
        .flatMap(blueprint => blueprint.manager_role_ids || [])
        .map(Number)
    );
    const roleChoices = roleResult.items
      .filter(role => !dedicatedElsewhere.has(Number(role.id)))
      .filter(role => [...managerPermissions].every(permission => (role.permissions || []).includes(permission)))
      .map(role => ({ value: role.id, label: role.name }));
    (item?.manager_role_ids || []).forEach(id => {
      if (!roleChoices.some(choice => Number(choice.value) === Number(id))) roleChoices.push({ value: id, label: 'Rola #' + id });
    });
    const deployment = item?.deployment || {};
    const variables = deployment.variables || {};
    const awxEditor = await window.BlueprintAwxEditor.create(
      deployment,
      credentials,
      item?.workflow || [],
      () => updateWorkflowPreview()
    );
    const templateWizard = options.mode === 'template';
    const currentProvider = providers.find(value => value.id === deployment.provider_id) || providers[0];
    const providerField = selectField(
      'Platforma Proxmox', 'provider_id',
      providers.map(value => ({ value: value.id, label: value.name + ' (#' + value.id + ')' })),
      currentProvider.id, { required: true }
    );
    const nodeField = selectField('Docelowy węzeł', 'node', [], variables.node || '', { required: true, placeholder: 'Wybierz węzeł' });
    const imageField = selectField('Obraz / szablon Proxmox', 'image', [], '', { required: true, placeholder: 'Wybierz szablon' });
    const storageField = selectField('Storage VM', 'storage', [], variables.storage || '', { required: true, placeholder: 'Wybierz storage' });
    const networkField = selectField('Bridge / sieć', 'network', [], variables.network || 'vmbr0', { required: true, placeholder: 'Wybierz sieć' });
    const schemeChoices = [
      ...schemes.map(value => ({ value: value.id, label: 'Użyj istniejącego: ' + value.pattern })),
      { value: '__new__', label: 'Własny pattern hostname' },
    ];
    const schemeField = selectField(
      'Sposób nadawania hostname', 'hostname_scheme_id', schemeChoices,
      options.hostnameSchemeId || deployment.hostname_scheme_id || (schemes[0]?.id || '__new__'),
      {
        required: true,
        wide: true,
        help: 'Wybierz gotowy pattern albo ustaw własny. Pattern może używać numeracji, np. SRLXXX → srl001.',
      }
    );
    const hostnamePreview = node('div', { class: 'hostname-pattern-preview wide' },
      node('span', { class: 'field-label', text: 'Podgląd hostname' }),
      node('strong', { class: 'mono', 'data-hostname-preview': 'true', text: 'srl001' }),
      node('small', { class: 'field-help', text: 'Tak będzie wyglądać przykładowa nazwa hosta dla aktualnego patternu.' })
    );
    const newScheme = node('div', { class: 'form-grid designer-subsection wide' },
      field('Pattern hostname', 'hostname_pattern', {
        value: 'SRLXXX',
        placeholder: 'SRLXXX lub {env}-{role}-{number}',
        help: 'Przykłady: SRLXXX → srl001, WEB-{env}-XXX → web-prod-001. Możesz też użyć {number}, {random}, {year}, {env}, {role}, {site}.',
        wide: true,
      }),
      field('Pierwszy numer', 'hostname_next_number', { type: 'number', min: 1, max: 999999999, value: 1 }),
      field('Liczba cyfr numeru', 'hostname_padding', {
        type: 'number', min: 1, max: 9, value: 3,
        help: 'Dla SRLXXX wartość zostanie rozpoznana automatycznie jako 3.',
      }),
      hostnamePreview
    );
    const existingSchemeEditor = node('div', { class: 'form-grid designer-subsection wide' },
      field('Pattern hostname', 'existing_hostname_pattern', {
        value: '',
        placeholder: 'SRLXXX lub srl{number}',
        help: 'Możesz zmienić pattern bez resetowania licznika. Nazwa techniczna schematu jest zarządzana automatycznie.',
        wide: true,
      }),
      field('Liczba cyfr numeru', 'existing_hostname_padding', { type: 'number', min: 1, max: 9, value: 3 }),
      node('div', { class: 'field wide' },
        node('span', { class: 'field-label', text: 'Następny numer' }),
        node('strong', { class: 'mono', 'data-hostname-next-number': 'true', text: '—' }),
        node('small', { class: 'field-help', text: 'Licznik jest tylko informacyjny i nie jest cofany podczas edycji szablonu.' })),
      node('div', { class: 'hostname-pattern-preview wide' },
        node('span', { class: 'field-label', text: 'Podgląd hostname' }),
        node('strong', { class: 'mono', 'data-existing-hostname-preview': 'true', text: '—' }),
        node('small', { class: 'field-help', text: 'Przykład wyniku dla wybranego patternu.' }))
    );
    const hostnameDefaults = node('div', { class: 'form-grid designer-subsection wide' });
    const ipMode = selectField(
      'Adres IPv4', 'ip_mode',
      [
        { value: 'dhcp', label: 'DHCP' },
        { value: 'ipam', label: 'Automatycznie z IPAM' },
        { value: 'static', label: 'Statyczny adres' },
      ],
      deployment.ipam_pool_id ? 'ipam' : (variables.ipv4_address ? 'static' : 'dhcp'),
      { required: true }
    );
    const ipamField = selectField(
      'Pula IPAM', 'ipam_pool_id',
      pools.map(value => ({ value: value.id, label: value.name + ' — ' + value.cidr })),
      deployment.ipam_pool_id || '', { placeholder: 'Wybierz pulę IPAM' }
    );
    const staticIp = field('IPv4/CIDR', 'ipv4_address', { value: variables.ipv4_address || '', placeholder: '10.0.20.25/24' });
    const staticGateway = field('Gateway', 'ipv4_gateway', { value: variables.ipv4_gateway || '', placeholder: '10.0.20.1' });
    const playbookField = selectField(
      'Playbook po utworzeniu (opcjonalnie)', 'ansible_playbook',
      [{ value: '', label: 'Bez Ansible' }, ...playbooks.map(value => ({ value: value.id, label: value.name + ' [' + value.transport + ']' }))],
      deployment.ansible?.playbook || ''
    );
    const ansibleCredentialField = selectField('Dane dostępowe Ansible', 'ansible_credentials_id',
      credentials.filter(value => ['ssh', 'winrm'].includes(value.type)).map(value => ({
        value: value.id, label: value.name + ' [' + value.type + '] (#' + value.id + ')',
      })), deployment.ansible?.credentials_id || '', { placeholder: 'Wybierz dane dostępowe' });
    const guestCredentialChoices = window.BlueprintProvisioningGuards.guestCredentialChoices(credentials);
    const templateGuestCredentialField = selectField('Istniejące konto lokalne w template', 'template_guest_credential_id',
      [{ value: '', label: 'Nie używaj predefiniowanego konta z template' }, ...guestCredentialChoices],
      deployment.template_guest_credential_id || '', { wide: true,
        help: 'Credential SSH opisuje konto, które już istnieje w bazowej VM/template. Cloudportal go nie tworzy ani nie nadpisuje; konto może być używane przez późniejsze kroki SSH i automatyzację.' });
    const guestCredentialField = selectField('Konto zarządzane przez Cloud-init', 'guest_credential_id',
      [{ value: '', label: 'Nie zmieniaj konta przez Cloud-init' }, ...guestCredentialChoices],
      deployment.guest_credential_id || '', { wide: true,
        help: 'Cloud-init utworzy albo zaktualizuje użytkownika z Credentiala. Aby zaktualizować istniejące konto z template, wybierz ten sam Credential w obu polach.' });
    const guestPasswordField = field('Hasło SSH (opcjonalnie)', 'ssh_password', {
      type: 'password',
      value: '',
      autocomplete: 'new-password',
      help: allowed('credentials.create')
        ? 'Możesz wpisać hasło bez tworzenia credentiala ręcznie. Przy zapisie Cloudportal utworzy dedykowany, zaszyfrowany credential SSH. Hasło nie trafi do Blueprintu ani terraform.tfvars.'
        : 'Do bezpośredniego podania hasła wymagane jest uprawnienie credentials.create. Możesz nadal wybrać istniejący credential SSH.',
    });
    const guestPasswordInput = guestPasswordField.querySelector('input');
    if (!allowed('credentials.create')) guestPasswordInput.disabled = true;
    const executorField = selectField(
      'Silnik IaC', 'executor',
      [{ value: 'terraform', label: 'Terraform' }, { value: 'opentofu', label: 'OpenTofu' }],
      deployment.executor || 'terraform',
      { required: true }
    );
    const workflowPreview = node('div', { class: 'workflow-preview workflow-preview-visual' });
    const preservedVariablesSchema = item?.variables_schema || {};
    const fields = node('div', { class: 'form-grid blueprint-designer' },
      node('div', { class: 'designer-heading wide' }, node('strong', { text: '1. Blueprint' }), node('span', { text: 'Zapisujesz kompletny preset VM.' })),
      field('Slug / identyfikator szablonu', 'slug', {
        required: true,
        value: item?.slug || '',
        help: 'To pole identyfikuje szablon. Nazwa wyświetlana jest tworzona automatycznie ze slugu.',
      }),
      field('Opis', 'description', { tag: 'textarea', value: item?.description || '', wide: true }),
      providerField, executorField,
      node('div', { class: 'designer-heading wide' }, node('strong', { text: '2. Proxmox i obraz' }), node('span', { text: 'Wybierz bazową VM/template oraz parametry używane przy każdym uruchomieniu.' })),
      nodeField, imageField, storageField, networkField,
      field('Rdzenie CPU', 'cpu', { type: 'number', min: 1, max: 128, value: variables.cpu ?? 2 }),
      field('RAM (MiB)', 'memory', { type: 'number', min: 512, max: 1048576, value: variables.memory ?? 4096 }),
      field('Dysk (GiB)', 'disk', { type: 'number', min: 1, max: 65536, value: variables.disk ?? 40 }),
      field('VLAN ID (opcjonalnie)', 'vlan_id', { type: 'number', min: 1, max: 4094, value: variables.vlan_id ?? '' }),
      field('Tagi Proxmox', 'tags', {
        value: (variables.tags || []).join(', '), wide: true,
        placeholder: 'linux, production, web',
        help: 'Tagi zostaną zapisane w Blueprintcie, dodane do workflow i automatycznie ustawione na VM.',
      }),
      node('div', { class: 'designer-heading wide' }, node('strong', { text: '3. Hostname' }), node('span', { text: 'Wybierz gotowy pattern albo wpisz własny. Podgląd pokaże przykładową nazwę hosta.' })),
      schemeField, newScheme, existingSchemeEditor, hostnameDefaults,
      node('div', { class: 'designer-heading wide' }, node('strong', { text: '4. Cloud-init' }), node('span', { text: 'Konfiguracja sieci, DNS i konta trafia do template Proxmox.' })),
      ipMode, ipamField, staticIp, staticGateway,
      templateGuestCredentialField,
      guestCredentialField,
      field('Użytkownik SSH', 'ssh_username', { value: variables.ssh_username || 'clouduser', required: true }),
      guestPasswordField,
      field('Klucz publiczny SSH (opcjonalnie)', 'ssh_public_key', {
        tag: 'textarea',
        value: variables.ssh_public_key || '',
        wide: true,
        help: 'Jeżeli wybierzesz credential VM, backend nadpisze te pola użytkownikiem i publicznym kluczem z credentiala.',
      }),
      field('Serwery DNS', 'dns_servers', { value: (variables.dns_servers || []).join(', '), placeholder: '1.1.1.1, 8.8.8.8' }),
      field('Domena wyszukiwania DNS', 'dns_domain', { value: variables.dns_domain || '', placeholder: 'lab.example.com' }),
      node('div', { class: 'designer-heading wide' }, node('strong', { text: '5. Workflow' }), node('span', { text: 'Bez ponownego wybierania obrazu, hostname, tagów ani cloud-init.' })),
      checkboxField('Instaluj QEMU Guest Agent automatycznie', 'install_qemu_guest_agent', variables.install_qemu_guest_agent ?? true),
      checkboxField('Czekaj na QEMU Guest Agent po Terraform apply', 'wait_agent', item ? (item.workflow || []).some(step => step.type === 'wait_for_agent') : true),
      playbookField, ansibleCredentialField,
      awxEditor.section,
      node('div', { class: 'workflow-box wide' }, node('strong', { text: 'Podgląd workflow' }), workflowPreview),
      node('div', { class: 'designer-heading wide' }, node('strong', { text: '6. Dostęp, role i recovery' })),
      multiCheckboxField('Role zarządzające szablonem', 'manager_role_ids', roleChoices, item?.manager_role_ids || [], {
        help: 'Użytkownik musi mieć globalne uprawnienie do zarządzania Blueprintami oraz co najmniej jedną z tych ról. Jedna rola może zarządzać tylko jednym szablonem.',
        empty: 'Brak ról dostępnych do przypisania.',
      }),
      checkboxField('Aktywny', 'is_active', item?.is_active ?? true),
      checkboxField('Panel backendu', 'visibility_backend', item?.visibility?.backend ?? true),
      checkboxField('CloudPortal', 'visibility_cloudportal', item?.visibility?.cloudportal ?? false),
      checkboxField('API', 'visibility_api', item?.visibility?.api ?? true),
      checkboxField('Wymaga zatwierdzenia przed uruchomieniem', 'requires_approval', item?.requires_approval ?? false), ...window.BlueprintApprovalPolicyUI.fields(item),
      selectField('Po błędzie wdrożenia', 'recovery_policy', [
        { value: 'preserve', label: 'Zachowaj zasoby do analizy' },
        { value: 'destroy_on_failure', label: 'Automatycznie usuń nieudane wdrożenie' },
      ], item?.recovery_policy || 'preserve'),
    );
    const providerSelect = providerField.querySelector('select');
    const nodeSelect = nodeField.querySelector('select');
    const imageSelect = imageField.querySelector('select');
    const storageSelect = storageField.querySelector('select');
    const networkSelect = networkField.querySelector('select');
    const schemeSelect = schemeField.querySelector('select');
    const ipModeSelect = ipMode.querySelector('select');
    const ipamSelect = ipamField.querySelector('select');
    const playbookSelect = playbookField.querySelector('select');
    const ansibleCredentialSelect = ansibleCredentialField.querySelector('select');
    let templateRows = [];
    let cloudInitSnippetStorage = variables.cloud_init_snippet_storage || '';
    let hydratedHostnameSchemeId = null;
    const updateHostnameFields = () => {
      const selected = schemeSelect.value;
      const scheme = schemes.find(value => String(value.id) === String(selected));
      const custom = selected === '__new__';
      newScheme.hidden = !custom;
      existingSchemeEditor.hidden = custom || !scheme;
      if (custom) hydratedHostnameSchemeId = null;
      if (scheme && !custom && String(hydratedHostnameSchemeId) !== String(scheme.id)) {
        existingSchemeEditor.querySelector('[name="existing_hostname_pattern"]').value = scheme.pattern;
        existingSchemeEditor.querySelector('[name="existing_hostname_padding"]').value = scheme.padding;
        existingSchemeEditor.querySelector('[data-hostname-next-number]').textContent = String(scheme.next_number);
        hydratedHostnameSchemeId = scheme.id;
      }
      const pattern = custom
        ? window.BlueprintFormUtils.normalizeHostnamePattern(
            newScheme.querySelector('[name="hostname_pattern"]').value,
            newScheme.querySelector('[name="hostname_padding"]').value
          ).pattern
        : window.BlueprintFormUtils.normalizeHostnamePattern(
            existingSchemeEditor.querySelector('[name="existing_hostname_pattern"]')?.value || scheme?.pattern || '',
            existingSchemeEditor.querySelector('[name="existing_hostname_padding"]')?.value || scheme?.padding || 3
          ).pattern;
      const defaults = deployment.hostname_values || {};
      hostnameDefaults.replaceChildren();
      const tokens = window.BlueprintFormUtils.hostnamePatternTokens(pattern);
      if (!tokens.length) {
        hostnameDefaults.append(node('div', { class: 'field-help wide', text: 'Pattern używa wyłącznie automatycznych tokenów {number}/{random}/{year}.' }));
      } else {
        const globalTokens = tokens.filter(token => ['location', 'role'].includes(token));
        if (globalTokens.length) {
          hostnameDefaults.append(node('div', { class: 'blueprint-wizard-info wide' },
            node('strong', { text: 'Location i Role są ustawiane globalnie.' }),
            node('span', { text: globalTokens.map(token =>
              '{' + token + '}=' + (globalHostnameDefaults[token] || '—')).join(' · ') + '. Zmienisz je w Narzędzia → Location i Role.' })));
        }
        tokens.filter(token => !['location', 'role'].includes(token)).forEach(token => hostnameDefaults.append(field(
          'Domyślne {' + token + '}', 'hostname_token_' + token,
          { required: true, value: defaults[token] || '', placeholder: token === 'env' ? 'prod' : token, help: 'Możesz wpisać stałą wartość albo {{ nazwa_zmiennej }}.' }
        )));
      }
      const sampleValue = token => ({
        env: 'prod',
        environment: 'prod',
        location: globalHostnameDefaults.location || 'wro',
        role: globalHostnameDefaults.role || 'server',
        application: 'app',
        service: 'svc',
        os: 'linux',
        cluster: 'cluster1',
        site: 'dc1',
      }[token] || token);
      const previewHostname = (rawPattern, rawPadding, nextNumber = 1) => {
        const normalized = window.BlueprintFormUtils.normalizeHostnamePattern(rawPattern, rawPadding);
        let preview = String(normalized.pattern || '')
          .replaceAll('{year}', String(new Date().getFullYear()))
          .replaceAll('{random}', 'a1b2')
          .replaceAll('{number}', String(nextNumber).padStart(Number(normalized.padding || 3), '0'));
        window.BlueprintFormUtils.hostnamePatternTokens(normalized.pattern).forEach(token => {
          preview = preview.replaceAll('{' + token + '}', sampleValue(token));
        });
        return preview.toLowerCase();
      };
      const customPreview = newScheme.querySelector('[data-hostname-preview]');
      if (customPreview) {
        customPreview.textContent = previewHostname(
          newScheme.querySelector('[name="hostname_pattern"]')?.value || '',
          newScheme.querySelector('[name="hostname_padding"]')?.value || 3,
          Number(newScheme.querySelector('[name="hostname_next_number"]')?.value || 1)
        ) || '—';
      }
      const existingPreview = existingSchemeEditor.querySelector('[data-existing-hostname-preview]');
      if (existingPreview && scheme) {
        existingPreview.textContent = previewHostname(
          existingSchemeEditor.querySelector('[name="existing_hostname_pattern"]')?.value || scheme.pattern,
          existingSchemeEditor.querySelector('[name="existing_hostname_padding"]')?.value || scheme.padding,
          Number(scheme.next_number || 1)
        ) || '—';
      }
      updateWorkflowPreview();
    };
    const updateIpMode = () => {
      const mode = ipModeSelect.value;
      ipamField.hidden = mode !== 'ipam';
      staticIp.hidden = mode !== 'static';
      staticGateway.hidden = mode !== 'static';
      updateWorkflowPreview();
    };
    const updateAnsible = () => {
      const playbook = playbooks.find(value => value.id === playbookSelect.value);
      ansibleCredentialField.hidden = !playbook;
      if (playbook) {
        const allowedType = playbook.transport;
        const matching = credentials.filter(value => value.type === allowedType);
        window.BlueprintFormUtils.setSelectChoices(
          ansibleCredentialSelect,
          matching.map(value => ({ value: value.id, label: value.name + ' [' + value.type + '] (#' + value.id + ')' })),
          deployment.ansible?.credentials_id || '',
          'Wybierz credential ' + allowedType
        );
      }
      updateWorkflowPreview();
    };
    function updateWorkflowPreview() {
      const options = {
        hostname: Boolean(schemeSelect.value),
        ipam: ipModeSelect.value === 'ipam',
        tags: window.BlueprintProvisioningGuards.workflowNeedsTags(window.BlueprintFormUtils.blueprintTags(fields.querySelector('[name="tags"]')?.value), deployment),
        waitAgent: Boolean(fields.querySelector('[name="wait_agent"]')?.checked),
        ansible: Boolean(playbookSelect.value),
        cloudInit: awxEditor.enabled(),
        awx: awxEditor.enabled(),
        awxRetry: awxEditor.retry(),
        awxTimeout: awxEditor.timeout(),
      };
      const steps = window.BlueprintFormUtils.blueprintWorkflow(options);
      workflowPreview.replaceChildren(...steps.flatMap((step, index) => {
        const card = node('div', { class: 'workflow-preview-card' },
          node('span', { class: 'workflow-dag-index', text: String(index + 1) }),
          node('div', {},
            node('strong', { text: step.type }),
            node('small', { class: 'muted', text: step.depends_on.length ? 'po: ' + step.depends_on.join(', ') : 'start' })));
        return index < steps.length - 1
          ? [card, node('span', { class: 'workflow-preview-arrow', 'aria-hidden': 'true', text: '→' })]
          : [card];
      }));
    }
    const loadNodeResources = async () => {
      const providerId = providerSelect.value;
      const targetNode = nodeSelect.value;
      if (!providerId || !targetNode) return;
      const [storageResult, networkResult, qemuReadiness] = await Promise.all([
        api('/providers/' + providerId + '/storages?node=' + encodeURIComponent(targetNode)),
        api('/providers/' + providerId + '/networks?node=' + encodeURIComponent(targetNode)),
        api('/providers/' + providerId + '/qemu-agent-readiness').catch(error => ({ ok: false, reason: error.message || 'readiness_check_failed' })),
      ]);
      const availableStorages = storageResult.items.filter(value => !value.disable);
      const storages = availableStorages.filter(value => String(value.content || '').includes('images'));
      const snippetState = window.BlueprintProvisioningGuards.selectSnippetStorage(availableStorages, cloudInitSnippetStorage);
      cloudInitSnippetStorage = snippetState.storage;
      window.BlueprintProvisioningGuards.syncQemuGuestAgentInstallControl(fields.querySelector('[name="install_qemu_guest_agent"]'), snippetState.snippets, qemuReadiness);
      window.BlueprintFormUtils.setSelectChoices(
        storageSelect,
        storages.map(value => ({ value: value.storage, label: value.storage + (value.type ? ' [' + value.type + ']' : '') })),
        variables.storage || storageSelect.value,
        'Wybierz storage'
      );
      const networks = networkResult.items.filter(value => value.iface);
      window.BlueprintFormUtils.setSelectChoices(
        networkSelect,
        networks.map(value => ({ value: value.iface, label: value.iface + (value.type ? ' [' + value.type + ']' : '') })),
        variables.network || networkSelect.value || 'vmbr0',
        'Wybierz sieć'
      );
    };
    const loadProvider = async () => {
      const providerId = providerSelect.value;
      const [nodeResult, templateResult] = await Promise.all([
        api('/providers/' + providerId + '/nodes'),
        api('/providers/' + providerId + '/templates'),
      ]);
      templateRows = templateResult.items;
      window.BlueprintFormUtils.setSelectChoices(
        nodeSelect,
        nodeResult.items.map(value => ({ value: value.node, label: value.node })),
        variables.node || nodeSelect.value,
        'Wybierz węzeł'
      );
      const currentImage = variables.template_id
        ? String(variables.template_node || '') + '|' + String(variables.template_id)
        : '';
      window.BlueprintFormUtils.setSelectChoices(
        imageSelect,
        templateRows.map(value => ({
          value: String(value.node || '') + '|' + String(value.vmid),
          label: (value.name || 'VM template') + ' — VMID ' + value.vmid + ' @ ' + value.node,
        })),
        currentImage,
        'Wybierz szablon'
      );
      await loadNodeResources();
    }; providerSelect.addEventListener('change', loadProvider);
    nodeSelect.addEventListener('change', loadNodeResources);
    schemeSelect.addEventListener('change', updateHostnameFields);
    newScheme.querySelector('[name="hostname_pattern"]').addEventListener('input', updateHostnameFields);
    newScheme.querySelector('[name="hostname_padding"]').addEventListener('input', updateHostnameFields);
    newScheme.querySelector('[name="hostname_next_number"]').addEventListener('input', updateHostnameFields);
    existingSchemeEditor.querySelector('[name="existing_hostname_pattern"]').addEventListener('input', updateHostnameFields);
    existingSchemeEditor.querySelector('[name="existing_hostname_padding"]').addEventListener('input', updateHostnameFields);
    ipModeSelect.addEventListener('change', updateIpMode);
    playbookSelect.addEventListener('change', updateAnsible);
    fields.querySelector('[name="tags"]').addEventListener('input', updateWorkflowPreview);
    fields.querySelector('[name="wait_agent"]').addEventListener('change', updateWorkflowPreview);
    await loadProvider();
    updateHostnameFields();
    updateIpMode();
    updateAnsible();
    updateWorkflowPreview();
    openModal({
      title: item ? 'Edytuj ' + item.name : (templateWizard ? 'Nowy szablon Terraform / OpenTofu' : 'Nowy Blueprint Proxmox'),
      eyebrow: templateWizard ? 'Kreator szablonu IaC' : 'Blueprint Designer',
      body: fields,
      submitLabel: item ? 'Zapisz nową wersję' : (templateWizard ? 'Utwórz szablon' : 'Utwórz Blueprint'),
      wide: true,
      onSubmit: async (data, form) => {
        const provider = providers.find(value => String(value.id) === String(data.get('provider_id')));
        if (!provider) throw new Error('Wybierz platformę Proxmox.');

        if (data.has('install_qemu_guest_agent') && !cloudInitSnippetStorage) {
          throw new Error('Instalacja QEMU Guest Agent wymaga storage z obsługą snippets na wybranym node.');
        }
        let schemeId = data.get('hostname_scheme_id');
        let selectedPattern = '';
        if (schemeId === '__new__') {
          const normalized = window.BlueprintFormUtils.normalizeHostnamePattern(
            data.get('hostname_pattern'),
            data.get('hostname_padding')
          );
          const created = await api('/hostname-schemes', {
            method: 'POST',
            body: {
              name: (data.get('slug') || 'hostname') + ' hostnames',
              pattern: normalized.pattern,
              next_number: Number(data.get('hostname_next_number') || 1),
              padding: normalized.padding,
              is_active: true,
            },
          });
          schemeId = created.id;
          selectedPattern = created.pattern;
        } else {
          const selectedScheme = schemes.find(value => String(value.id) === String(schemeId));
          if (!selectedScheme) throw new Error('Wybrany schemat hostname nie istnieje.');
          const normalized = window.BlueprintFormUtils.normalizeHostnamePattern(
            data.get('existing_hostname_pattern') || selectedScheme.pattern,
            data.get('existing_hostname_padding') || selectedScheme.padding
          );
          const editedName = selectedScheme.name;
          const changed = normalized.pattern.toLowerCase() !== selectedScheme.pattern.toLowerCase()
            || Number(normalized.padding) !== Number(selectedScheme.padding);
          if (changed) {
            if (!allowed('hostnames.update')) throw new Error('Brak uprawnienia do edycji schematu hostname.');
            const updated = await api('/hostname-schemes/' + selectedScheme.id, {
              method: 'PUT',
              body: {
                name: editedName,
                pattern: normalized.pattern,
                next_number: selectedScheme.next_number,
                padding: normalized.padding,
                is_active: selectedScheme.is_active,
              },
            });
            selectedPattern = updated.pattern;
          } else {
            selectedPattern = selectedScheme.pattern;
          }
        }
        const hostnameValues = {};
        window.BlueprintFormUtils.hostnamePatternTokens(selectedPattern).forEach(token => {
          const value = form.elements['hostname_token_' + token]?.value.trim();
          if (value) hostnameValues[token] = value;
        });
        const image = String(data.get('image') || '').split('|');
        const templateNode = image[0];
        const templateId = Number(image[1]);
        if (!templateId) throw new Error('Wybierz obraz/template Proxmox.');
        const tags = window.BlueprintFormUtils.blueprintTags(data.get('tags'));
        const directGuestPassword = String(data.get('ssh_password') || '');
        const selectedGuestCredentialId = data.get('guest_credential_id')
          ? Number(data.get('guest_credential_id'))
          : null;
        const selectedTemplateGuestCredentialId = data.get('template_guest_credential_id')
          ? Number(data.get('template_guest_credential_id'))
          : null;
        if (directGuestPassword && !allowed('credentials.create')) {
          throw new Error('Brak uprawnienia credentials.create do bezpiecznego zapisania hasła SSH.');
        }
        const guestCredentialRequested = Boolean(selectedGuestCredentialId || directGuestPassword);
        const vmVariables = {
          name: '{{ hostname }}',
          node: data.get('node'),
          template_id: templateId,
          template_node: templateNode || null,
          cpu: Number(data.get('cpu')),
          memory: Number(data.get('memory')),
          disk: Number(data.get('disk')),
          network: data.get('network'),
          storage: data.get('storage'),
          vlan_id: data.get('vlan_id') ? Number(data.get('vlan_id')) : null,
          ssh_username: data.get('ssh_username'),
          ssh_public_key: data.get('ssh_public_key') || null,
          install_qemu_guest_agent: data.has('install_qemu_guest_agent'),
          cloud_init_snippet_storage: data.has('install_qemu_guest_agent') && !guestCredentialRequested && !awxEditor.enabled() ? cloudInitSnippetStorage : null,
          dns_servers: splitValues(data.get('dns_servers')),
          dns_domain: data.get('dns_domain') || null,
          tags,
        };
        const mode = data.get('ip_mode');
        let ipamPoolId = null;
        if (mode === 'ipam') {
          ipamPoolId = Number(data.get('ipam_pool_id'));
          if (!ipamPoolId) throw new Error('Wybierz pulę IPAM.');
        } else if (mode === 'static') {
          if (!data.get('ipv4_address') || !data.get('ipv4_gateway')) throw new Error('Statyczny IPv4 wymaga adresu/CIDR i gateway.');
          vmVariables.ipv4_address = data.get('ipv4_address');
          vmVariables.ipv4_gateway = data.get('ipv4_gateway');
        }
        let ansible = null;
        if (data.get('ansible_playbook')) {
          const credentialId = Number(data.get('ansible_credentials_id'));
          if (!credentialId) throw new Error('Wybierz dane dostępowe dla Ansible.');
          ansible = {
            playbook: data.get('ansible_playbook'),
            credentials_id: credentialId,
            variables: deployment.ansible?.playbook === data.get('ansible_playbook') ? (deployment.ansible?.variables || {}) : {},
          };
          if (schemeId && ansible.playbook === 'bootstrap-linux' && ansible.variables.hostname === undefined) {
            ansible.variables.hostname = '{{ hostname }}';
          }
        }
        const awx = awxEditor.value();
        const workflow = window.BlueprintAwxEditor.quickWorkflow(item, awx, {
          hostname: Boolean(schemeId),
          ipam: mode === 'ipam',
          tags: window.BlueprintProvisioningGuards.workflowNeedsTags(tags, deployment),
          waitAgent: data.has('wait_agent'),
          guestAccess: Boolean(selectedTemplateGuestCredentialId),
          ansible: Boolean(ansible),
        }, awxEditor.retry(), awxEditor.timeout());
        const managerRoleIds = [...form.querySelectorAll('[name="manager_role_ids"]:checked')].map(input => Number(input.value));
        if (templateWizard && !item && roleChoices.length && !managerRoleIds.length) {
          throw new Error('Wybierz co najmniej jedną rolę zarządzającą szablonem.');
        }
        const blueprintSlug = String(data.get('slug') || '').trim();
        if (!blueprintSlug) throw new Error('Podaj slug / identyfikator szablonu.');
        const blueprintName = item?.name || blueprintSlug
          .replace(/[-_]+/g, ' ')
          .replace(/\b\w/g, value => value.toUpperCase());
        const payload = {
          slug: blueprintSlug,
          name: blueprintName,
          description: data.get('description'),
          is_active: data.has('is_active'),
          visibility: {
            backend: data.has('visibility_backend'),
            cloudportal: data.has('visibility_cloudportal'),
            api: data.has('visibility_api'),
          },
          allowed_role_ids: item?.allowed_role_ids || [],
          allowed_user_ids: item?.allowed_user_ids || [],
          manager_role_ids: managerRoleIds,
          variables_schema: preservedVariablesSchema,
          deployment: {
            name: '{{ hostname }}',
            provider_id: provider.id,
            credentials_id: provider.credentials_id,
            hostname_scheme_id: Number(schemeId),
            hostname_values: hostnameValues,
            ipam_pool_id: ipamPoolId,
            guest_credential_id: selectedGuestCredentialId,
            template_guest_credential_id: selectedTemplateGuestCredentialId,
            guest_account_mode: deployment.guest_account_mode || 'cloud_init_managed',
            apmid: deployment.apmid || null,
            environment: deployment.environment || null,
            select_apmid_on_execute: Boolean(deployment.select_apmid_on_execute),
            select_environment_on_execute: Boolean(deployment.select_environment_on_execute),
            template: 'proxmox-vm',
            executor: data.get('executor'),
            variables: vmVariables,
            ansible,
            awx,
          },
          workflow,
          requires_approval: data.has('requires_approval'), auto_approve_for_executors: window.BlueprintApprovalPolicyUI.parseAuto(data.get('auto_approve_for_executors')), approval_timeout_hours: window.BlueprintApprovalPolicyUI.parseTimeout(data.get('approval_timeout_hours')),
          recovery_policy: data.get('recovery_policy'),
        };
        let createdInlineCredentialId = null;
        if (directGuestPassword) {
          const inlineCredential = await api('/credentials', {
            method: 'POST',
            body: {
              name: ('Blueprint ' + blueprintSlug + ' — VM SSH').slice(0, 100),
              type: 'ssh',
              endpoint: '',
              username: String(data.get('ssh_username') || '').trim(),
              verify_ssl: true,
              expires_at: null,
              rotation_due_at: null,
              secrets: { password: directGuestPassword },
            },
          });
          createdInlineCredentialId = Number(inlineCredential.id);
          payload.deployment.guest_credential_id = createdInlineCredentialId;
        }
        try {
          await api(item ? '/blueprints/' + item.id : '/blueprints', {
            method: item ? 'PUT' : 'POST',
            body: payload,
          });
        } catch (error) {
          if (createdInlineCredentialId && allowed('credentials.delete')) {
            try {
              await api('/credentials/' + createdInlineCredentialId, { method: 'DELETE' });
            } catch {
              // Best effort only. Never hide the Blueprint save error.
            }
          }
          throw error;
        }
        toast(item
          ? 'Utworzono nową wersję Blueprintu.'
          : (templateWizard ? 'Szablon Terraform / OpenTofu jest gotowy do tworzenia VM.' : 'Blueprint gotowy do szybkiego tworzenia VM.'));
        navigate(options.returnTo || 'blueprints');
      },
    });
  } catch (error) {
    toast(error.message, 'error');
  }
}
async function blueprintForm(item = null) {
  try {
    const [providerResult, credentialResult, templateResult, schemeResult, poolResult, roleResult, userResult, playbookResult, blueprintResult] = await Promise.all([
      api('/providers?limit=200'),
      api('/credentials?limit=200'),
      api('/templates'),
      allowed('hostnames.read') ? api('/hostname-schemes?limit=200') : Promise.resolve({ items: [] }),
      allowed('ipam.read') ? api('/ipam/pools?limit=200') : Promise.resolve({ items: [] }),
      allowed('roles.read') ? api('/roles?limit=200') : Promise.resolve({ items: [] }),
      allowed('users.read') ? api('/users?limit=200') : Promise.resolve({ items: [] }),
      allowed('ansible.execute') && allowed('ansible.read') ? api('/ansible/playbooks') : Promise.resolve({ items: [] }),
      allowed('blueprints.read') ? api('/blueprints?limit=200') : Promise.resolve({ items: [] }),
    ]);
    const providers = providerResult.items;
    const credentials = credentialResult.items;
    const templates = templateResult.items.filter(value =>
      value.enabled !== false || value.id === item?.deployment?.template);
    const schemes = schemeResult.items;
    const pools = poolResult.items;
    const playbooks = playbookResult.items.filter(value =>
      value.enabled !== false || value.id === item?.deployment?.ansible?.playbook);
    if (!templates.length) throw new Error('Katalog nie zawiera szablonów Terraform/OpenTofu.');
    const variableList = node('div', { class: 'editor-list wide' });
    const workflowList = node('div', { class: 'editor-list wide workflow-editor-list' });
    const workflowGraph = node('div', { class: 'workflow-dag wide', 'aria-live': 'polite' });
    let variableCounter = 0;
    let workflowCounter = 0;
    const workflowRowData = row => {
      const typeSelect = row.querySelector('[name="workflow_type"]');
      return {
        row,
        id: row.querySelector('[name="workflow_id"]')?.value.trim() || '',
        type: typeSelect?.value || '',
        label: typeSelect?.selectedOptions?.[0]?.textContent || typeSelect?.value || 'Krok',
        depends: splitValues(row.querySelector('[name="workflow_depends"]')?.value || ''),
      };
    };
    const workflowDepths = steps => {
      const byId = new Map(steps.filter(step => step.id).map(step => [step.id, step]));
      const memo = new Map();
      const visiting = new Set();
      const depth = step => {
        if (!step.id) return 0;
        if (memo.has(step.id)) return memo.get(step.id);
        if (visiting.has(step.id)) return 0;
        visiting.add(step.id);
        const parents = step.depends.map(id => byId.get(id)).filter(Boolean);
        const value = parents.length ? 1 + Math.max(...parents.map(depth)) : 0;
        visiting.delete(step.id);
        memo.set(step.id, value);
        return value;
      };
      return new Map(steps.map(step => [step, depth(step)]));
    };

    const syncWorkflowGraph = () => {
      const steps = [...workflowList.querySelectorAll('[data-workflow-row]')].map(workflowRowData);
      if (!steps.length) {
        workflowGraph.replaceChildren(node('div', { class: 'empty', text: 'Dodaj pierwszy krok workflow.' }));
        return;
      }
      const counts = new Map();
      steps.forEach(step => { if (step.id) counts.set(step.id, (counts.get(step.id) || 0) + 1); });
      const ids = new Set(steps.map(step => step.id).filter(Boolean));
      const depths = workflowDepths(steps);
      const maxDepth = Math.max(0, ...depths.values());
      const columns = [];
      for (let level = 0; level <= maxDepth; level += 1) {
        const levelSteps = steps.filter(step => depths.get(step) === level);
        if (!levelSteps.length) continue;
        const cards = levelSteps.map(step => {
          const missing = step.depends.filter(id => !ids.has(id));
          const duplicate = step.id && counts.get(step.id) > 1;
          const selfReference = step.id && step.depends.includes(step.id);
          const invalid = !step.id || duplicate || missing.length || selfReference;
          const dependencies = step.depends.length
            ? node('div', { class: 'workflow-dag-dependencies' },
              node('span', { class: 'muted', text: 'Zależy od:' }),
              ...step.depends.map(id => node('span', {
                class: `workflow-dag-chip ${ids.has(id) ? '' : 'danger'}`,
                text: id,
              })))
            : node('span', { class: 'muted', text: 'Krok startowy' });
          return node('article', {
            class: `workflow-dag-card ${invalid ? 'invalid' : ''}`,
            onClick: () => step.row.scrollIntoView({ behavior: 'smooth', block: 'center' }),
          },
          node('div', { class: 'workflow-dag-card-head' },
            node('span', { class: 'workflow-dag-index', text: String(steps.indexOf(step) + 1) }),
            node('div', {},
              node('strong', { text: step.id || 'Brak ID' }),
              node('small', { text: step.label })),
            invalid ? badge('Sprawdź', 'danger') : badge('OK', 'ok')),
          dependencies,
          duplicate ? node('small', { class: 'form-error', text: 'ID kroku występuje więcej niż raz.' }) : '',
          selfReference ? node('small', { class: 'form-error', text: 'Krok nie może zależeć od samego siebie.' }) : '',
          missing.length ? node('small', { class: 'form-error', text: 'Brak kroków: ' + missing.join(', ') }) : '');
        });
        columns.push(node('div', { class: 'workflow-dag-column' },
          node('div', { class: 'workflow-dag-level', text: `Etap ${level + 1}` }),
          ...cards));
      }
      workflowGraph.replaceChildren(
        node('div', { class: 'workflow-dag-header' },
          node('div', {},
            node('strong', { text: 'Mapa workflow' }),
            node('small', { text: `${steps.length} kroków · ${columns.length} poziomów zależności` })),
          node('span', { class: 'muted', text: 'Kliknij kartę, aby przejść do edycji kroku.' })),
        node('div', { class: 'workflow-dag-columns' }, ...columns));
    };

    const moveWorkflowRow = (row, direction) => {
      if (direction < 0 && row.previousElementSibling) {
        workflowList.insertBefore(row, row.previousElementSibling);
      } else if (direction > 0 && row.nextElementSibling) {
        workflowList.insertBefore(row.nextElementSibling, row);
      }
      syncWorkflowGraph();
    };

    const addVariable = (key = '', definition = {}) => {
      variableCounter += 1;
      const row = node('div', { class: 'editor-card', 'data-variable-row': String(variableCounter) });
      const typeChoices = [
        { value: 'string', label: 'Tekst' },
        { value: 'integer', label: 'Liczba całkowita' },
        { value: 'select', label: 'Lista wyboru' },
        { value: 'boolean', label: 'Tak / nie' },
      ];
      const typeField = selectField('Typ', 'variable_type', typeChoices, definition.type || 'string', { required: true });
      const optionsField = field('Opcje (przecinki lub nowe linie)', 'variable_options', {
        tag: 'textarea', value: (definition.options || []).join('\n'), wide: true,
      });
      const minField = field('Minimum', 'variable_min', { type: 'number', value: definition.min ?? '' });
      const maxField = field('Maksimum', 'variable_max', { type: 'number', value: definition.max ?? '' });
      const refresh = () => {
        const type = typeField.querySelector('select').value;
        optionsField.hidden = type !== 'select';
        minField.hidden = type !== 'integer';
        maxField.hidden = type !== 'integer';
      };
      typeField.querySelector('select').addEventListener('change', refresh);
      row.append(
        node('div', { class: 'editor-card-header' },
          node('strong', { text: 'Zmienna wejściowa' }),
          button('Usuń', () => row.remove(), 'danger')),
        node('div', { class: 'form-grid' },
          field('Klucz', 'variable_key', { required: true, value: key, placeholder: 'np. cpu' }),
          field('Etykieta dla użytkownika', 'variable_label', { value: definition.label || '', placeholder: 'np. Liczba CPU' }),
          typeField,
          field('Wartość domyślna', 'variable_default', {
            value: definition.default === undefined || definition.default === null ? '' : String(definition.default),
          }),
          minField,
          maxField,
          optionsField,
          checkboxField('Pole wymagane', 'variable_required', Boolean(definition.required))));
      variableList.append(row);
      refresh();
    };

    const workflowTypes = [
      ['cloud_init', 'Cloud-init: pierwszy start systemu'], ['terraform_plan', 'Terraform plan'], ['terraform_apply', 'Terraform apply'],
      ['wait_for_vm', 'Czekaj na VM'], ['wait_for_agent', 'Czekaj na guest agent'],
      ['wait_for_ip', 'Czekaj na IP'], ['wait_for_ssh', 'Czekaj na SSH'],
      ['run_ansible_playbook', 'Uruchom Ansible'], ['register_awx', 'Rejestracja w AWX'], ['create_snapshot', 'Utwórz snapshot'],
      ['health_check', 'Health check'], ['condition', 'Warunek'], ['approval', 'Akceptacja'],
      ['delay', 'Opóźnienie'], ['notification', 'Powiadomienie'], ['terraform_destroy', 'Terraform destroy (tylko rollback)'],
    ];
    let workflowProvider = templates.find(template => template.id === (item?.deployment?.template || 'proxmox-vm'))?.provider || 'proxmox'; const workflowChoices = currentType => window.BlueprintProvisioningGuards.workflowChoicesForProvider(workflowTypes, workflowProvider, currentType);
    const addWorkflowStep = (step = {}) => {
      workflowCounter += 1;
      const advanced = node('details', { class: 'advanced-options wide' },
        node('summary', { text: 'Opcje zaawansowane' }),
        node('div', { class: 'form-grid advanced-options-body' },
          field('Liczba ponowień', 'workflow_retry', { type: 'number', min: 0, max: 10, value: step.retry ?? 0 }),
          field('Limit czasu (s)', 'workflow_timeout', { type: 'number', min: 1, max: 86400, value: step.timeout ?? (step.type === 'wait_for_ip' ? 180 : 600) }),
          field('Krok cofania — ID (opcjonalnie)', 'workflow_rollback', { value: step.rollback || '' }),
          field('Warunki — JSON (opcjonalnie)', 'workflow_conditions', {
            tag: 'textarea', wide: true, value: Object.keys(step.conditions || {}).length ? window.BlueprintFormUtils.jsonValue(step.conditions) : '',
            help: 'Zaawansowane warunki wykonania kroku.',
          })));
      const row = node('div', { class: 'editor-card workflow-editor-card', 'data-workflow-row': String(workflowCounter) });
      const controls = node('div', { class: 'workflow-step-controls' },
        button('↑', () => moveWorkflowRow(row, -1), 'ghost'),
        button('↓', () => moveWorkflowRow(row, 1), 'ghost'),
        button('Usuń', () => {
          window.BlueprintClassicWorkflow.removeRow(workflowList, row);
          syncWorkflowGraph();
        }, 'danger'));
      row.append(
        node('div', { class: 'editor-card-header' },
          node('div', {},
            node('strong', { text: 'Krok workflow' }),
            node('small', { class: 'muted', text: 'Kolejność wizualna nie zastępuje depends_on — zależności pozostają źródłem prawdy.' })),
          controls),
        node('div', { class: 'form-grid' },
          field('ID kroku', 'workflow_id', { required: true, value: step.id || '', placeholder: 'np. apply' }),
          selectField('Akcja', 'workflow_type',
            workflowChoices(step.type).map(([value, label]) => ({ value, label })),
            step.type || 'terraform_apply', { required: true }),
          field('Zależy od (ID kroków)', 'workflow_depends', { value: (step.depends_on || []).join(', '), wide: true, help: 'Kilka ID oddziel przecinkami.' }),
          advanced));
      window.BlueprintClassicWorkflow.bindIdTracking(row, workflowList, syncWorkflowGraph);
      row.querySelectorAll('input,select,textarea').forEach(control => {
        control.addEventListener('input', syncWorkflowGraph);
        control.addEventListener('change', syncWorkflowGraph);
      });
      workflowList.append(row);
      syncWorkflowGraph();
    };

    const defaultVariables = item?.variables_schema || {
      environment: { type: 'select', label: 'Środowisko', required: true, options: ['dev', 'test', 'prod'], default: 'dev' },
      cpu: { type: 'integer', label: 'CPU', required: true, default: 2, min: 1, max: 8 },
    };
    Object.entries(defaultVariables).forEach(([key, definition]) => addVariable(key, definition));
    const defaultWorkflow = item?.workflow || (() => {
      const apply = { id: 'apply', type: 'terraform_apply', depends_on: [], retry: 0, timeout: 3600, conditions: {}, rollback: null };
      const provider = templates.find(template => template.id === (item?.deployment?.template || 'proxmox-vm'))?.provider;      return provider === 'proxmox' ? [apply, { id: 'vm_running', type: 'wait_for_vm', depends_on: ['apply'], retry: 0, timeout: 600, conditions: {}, rollback: null }] : [apply];
    })();
    defaultWorkflow.forEach(addWorkflowStep);

    const deployment = item?.deployment || {};
    const awxEditor = await window.BlueprintAwxEditor.create(deployment, credentials, item?.workflow || []);
    const initialTemplate = templates.find(template => template.id === (deployment.template || 'proxmox-vm')) || templates[0];
    const templateField = selectField('Szablon IaC', 'deployment_template', templates.map(template => ({
      value: template.id,
      label: `${template.name} · v${template.version} · ${CREDENTIAL_TYPE_CONFIG[template.provider]?.label || template.provider}`,
    })), initialTemplate.id, { required: true });
    const providerField = selectField('Platforma', 'deployment_provider_id', [], deployment.provider_id || '', { required: true });
    const credentialField = selectField('Dane dostępowe', 'deployment_credentials_id', [], deployment.credentials_id || '', { required: true });
    const templateVariables = node('div', { class: 'form-grid wide template-variable-grid' });
    const variableState = new Map([[initialTemplate.id, { ...(deployment.variables || {}) }]]);
    let currentTemplateId = initialTemplate.id;
    let deploymentVariablesReady = false;

    const currentTemplate = () => templates.find(template => template.id === templateField.querySelector('select').value) || templates[0];
    const templateGuestCredentialControl = window.BlueprintProvisioningGuards.templateGuestCredentialField(
      credentials, deployment.template_guest_credential_id, currentTemplate()?.id
    );
    const templateGuestCredentialField = templateGuestCredentialControl.field;
    const guestCredentialControl = window.BlueprintProvisioningGuards.guestCredentialField(
      credentials, deployment.guest_credential_id, currentTemplate()?.id
    );
    const guestCredentialField = guestCredentialControl.field;
    const refill = (select, values, placeholder, selectedValue) => {
      select.replaceChildren(node('option', { value: '', text: placeholder }));
      values.forEach(value => select.append(node('option', {
        value: value.id,
        text: value.label,
        selected: String(value.id) === String(selectedValue),
      })));
      if (!select.value && values.length === 1) select.value = String(values[0].id);
    };

    const saveDeploymentVariables = () => {
      if (!deploymentVariablesReady) return;
      const previousTemplate = templates.find(template => template.id === currentTemplateId);
      if (previousTemplate) variableState.set(currentTemplateId, readBlueprintTemplateVariables(templateVariables, previousTemplate));
    };

    const refreshCredentialChoices = () => {
      const providerId = providerField.querySelector('select').value;
      const provider = providers.find(value => String(value.id) === String(providerId));
      const matches = provider
        ? credentials.filter(value => Number(value.id) === Number(provider.credentials_id)).map(value => ({ id: value.id, label: value.name }))
        : [];
      refill(credentialField.querySelector('select'), matches,
        matches.length ? 'Dane dostępowe platformy' : 'Wybierz provider',
        deployment.credentials_id);
    };

    const ansibleEditor = window.BlueprintAnsibleRuns.createEditor({
      playbooks,
      credentials,
      initialRuns: window.BlueprintAnsibleRuns.initialRunsFromDeployment(deployment),
      enabled: Boolean(
        (deployment.ansible_runs || []).length
        || deployment.ansible
      ),
      editable: allowed('ansible.execute'),
      supported: currentTemplate()?.provider === 'proxmox',
      prefix: 'deployment_ansible',
      toggleLabel: 'Po wdrożeniu uruchom zatwierdzone runbooki Ansible',
      description: 'Możesz dodać wiele runbooków. Są wykonywane kolejno po utworzeniu VM i wykryciu jej adresu.',
    });
    const ansibleSection = ansibleEditor.section;

    const refreshDeploymentTemplate = () => {
      saveDeploymentVariables();
      const template = currentTemplate();
      workflowProvider = template.provider;
      currentTemplateId = template.id;
      const matches = providers.filter(value => value.type === template.provider).map(value => ({ id: value.id, label: value.name }));
      refill(providerField.querySelector('select'), matches,
        matches.length ? 'Wybierz provider' : 'Brak połączenia z tą platformą',
        deployment.provider_id);
      refreshCredentialChoices();
      templateVariables.replaceChildren();
      const values = variableState.get(template.id) || {};
      Object.entries(template.variables_schema?.properties || {}).forEach(([name, spec]) => {
        templateVariables.append(blueprintTemplateVariableField(name, spec, values[name]));
      });
      deploymentVariablesReady = true;
      templateGuestCredentialControl.sync(template.id);
      guestCredentialControl.sync(template.id);
      ansibleEditor.setSupported(template.provider === 'proxmox');
    };
    templateField.querySelector('select').addEventListener('change', refreshDeploymentTemplate);
    providerField.querySelector('select').addEventListener('change', refreshCredentialChoices);

    const managerPermissions = new Set(['blueprints.read', 'blueprints.update', 'blueprints.delete']);
    const dedicatedElsewhere = new Set(
      blueprintResult.items
        .filter(blueprint => Number(blueprint.id) !== Number(item?.id))
        .flatMap(blueprint => blueprint.manager_role_ids || [])
        .map(Number)
    );
    const roleChoices = roleResult.items
      .filter(role => !dedicatedElsewhere.has(Number(role.id)))
      .filter(role => [...managerPermissions].every(permission => (role.permissions || []).includes(permission)))
      .map(role => ({ value: role.id, label: role.name }));
    (item?.allowed_role_ids || []).forEach(id => {
      if (!roleChoices.some(choice => Number(choice.value) === Number(id))) roleChoices.push({ value: id, label: `Rola #${id}` });
    });
    (item?.manager_role_ids || []).forEach(id => {
      if (!roleChoices.some(choice => Number(choice.value) === Number(id))) roleChoices.push({ value: id, label: `Rola #${id}` });
    });
    const defaultManagerRoleNames = new Set(['Administrator', 'Infrastructure Administrator']);
    const defaultManagerRoleIds = item
      ? (item.manager_role_ids || [])
      : roleChoices.filter(choice => defaultManagerRoleNames.has(choice.label)).map(choice => Number(choice.value));
    const userChoices = userResult.items.map(user => ({ value: user.id, label: user.username + (user.email ? ' · ' + user.email : '') }));
    (item?.allowed_user_ids || []).forEach(id => {
      if (!userChoices.some(choice => Number(choice.value) === Number(id))) userChoices.push({ value: id, label: `Użytkownik #${id}` });
    });

    const schemeChoices = [{ value: '', label: 'Bez automatycznego hostname' }].concat(schemes.filter(value => value.is_active || Number(value.id) === Number(deployment.hostname_scheme_id)).map(value => ({
      value: value.id, label: `${value.name} · ${value.pattern}`,
    })));
    const deploymentNameField = field('Nazwa wdrożenia', 'deployment_name', {
      required: !deployment.hostname_scheme_id,
      value: deployment.hostname_scheme_id ? '{{ hostname }}' : (deployment.name || ''),
      wide: true,
      placeholder: 'np. web-prod-01',
      help: 'Pole jest używane tylko bez generatora hostname.',
    });
    const deploymentHostnameSchemeField = selectField(
      'Schemat hostname', 'deployment_hostname_scheme_id', schemeChoices, deployment.hostname_scheme_id || ''
    );
    const automaticHostnameNotice = node('div', {
      class: 'field-help wide',
      text: 'Nazwa deploymentu i nazwa VM będą generowane automatycznie z wybranego wzorca hostname.',
    });
    const syncDeploymentNameMode = () => {
      const automatic = Boolean(deploymentHostnameSchemeField.querySelector('select').value);
      deploymentNameField.hidden = automatic;
      automaticHostnameNotice.hidden = !automatic;
      const input = deploymentNameField.querySelector('input');
      input.required = !automatic;
      if (automatic) input.value = '{{ hostname }}';
      else if (input.value === '{{ hostname }}') input.value = '';
    };
    const poolChoices = [{ value: '', label: 'Bez automatycznego IPAM' }].concat(pools.filter(value => value.is_active || Number(value.id) === Number(deployment.ipam_pool_id)).map(value => ({
      value: value.id, label: `${value.name} · ${value.cidr}`,
    })));

    const blueprintSelector = selectField(
      'Blueprint', 'designer_blueprint_id',
      [
        { value: '', label: 'Nowy Blueprint' },
        ...blueprintResult.items
          .slice()
          .sort((left, right) => String(left.name || left.slug || '').localeCompare(String(right.name || right.slug || ''), 'pl', { sensitivity: 'base' }))
          .map(blueprint => ({
            value: blueprint.id,
            label: (blueprint.name || blueprint.slug) + ' · v' + blueprint.version,
          })),
      ],
      item?.id || '',
      {
        wide: true,
        help: 'Wybierz istniejący Blueprint, aby otworzyć go w Automation Designer, albo wybierz „Nowy Blueprint”, aby utworzyć nowy.',
      }
    );

    const fields = node('div', { class: 'form-grid blueprint-designer' },
      formSection('Blueprint', 'Wybierz Blueprint do edycji, a następnie ustaw jego nazwę, identyfikator i opis.',
        node('div', { class: 'form-grid' },
          blueprintSelector,
          field('Slug', 'slug', { required: true, value: item?.slug || '', placeholder: 'np. ubuntu-web' }),
          field('Nazwa', 'name', { required: true, value: item?.name || '', placeholder: 'np. Ubuntu Web Server' }),
          field('Opis', 'description', { tag: 'textarea', value: item?.description || '', wide: true }),
          checkboxField('Aktywny', 'is_active', item?.is_active ?? true))),
      formSection('Widoczność i bezpieczeństwo', 'Określ gdzie Blueprint jest dostępny i co ma się stać po nieudanym wdrożeniu.',
        node('div', { class: 'form-grid' },
          checkboxField('Panel backendu', 'visibility_backend', item?.visibility?.backend ?? true),
          checkboxField('CloudPortal', 'visibility_cloudportal', item?.visibility?.cloudportal ?? false),
          checkboxField('API', 'visibility_api', item?.visibility?.api ?? true),
          checkboxField('Wymaga akceptacji przy uruchomieniu', 'requires_approval', item?.requires_approval ?? false), ...window.BlueprintApprovalPolicyUI.fields(item),
          selectField('Po błędzie wdrożenia', 'recovery_policy', [
            { value: 'preserve', label: 'Zachowaj zasoby do analizy' },
            { value: 'destroy_on_failure', label: 'Automatycznie usuń nieudane wdrożenie' },
          ], item?.recovery_policy || 'preserve'))),
      formSection('Dostęp', 'Puste listy oznaczają brak dodatkowego ograniczenia.',
        node('div', { class: 'form-grid' },
          multiCheckboxField('Dozwolone role', 'allowed_role_ids', roleChoices, item?.allowed_role_ids || [], {
            help: 'Brak zaznaczeń oznacza brak dodatkowego ograniczenia roli przy uruchamianiu.',
            empty: 'Brak ról dostępnych do wyboru.',
          }),
          multiCheckboxField('Role zarządzające szablonem', 'manager_role_ids', roleChoices, defaultManagerRoleIds, {
            help: 'Wymagane dodatkowo do edycji i usuwania. Jedna rola może być przypisana jako zarządzająca tylko do jednego szablonu.',
            empty: 'Brak ról dostępnych do wyboru.',
          }),
          multiCheckboxField('Dozwoleni użytkownicy', 'allowed_user_ids', userChoices, item?.allowed_user_ids || [], {
            help: 'Brak zaznaczeń oznacza brak dodatkowego ograniczenia użytkownika.',
            empty: 'Brak użytkowników dostępnych do wyboru.',
          }))),
      formSection('Pola self-service', 'Z tych definicji portal buduje formularz uruchomienia Blueprintu.',
        variableList,
        node('div', { class: 'editor-add-row' }, button('Dodaj pole', () => addVariable('', { type: 'string' }), 'primary'))),
      formSection('Wdrożenie', 'Wybierz platformę i wartości przekazywane do zatwierdzonego szablonu IaC.',
        node('div', { class: 'form-grid' },
          deploymentNameField,
          automaticHostnameNotice,
          templateField,
          providerField,
          credentialField,
          selectField('Silnik IaC', 'deployment_executor', [{ value: 'terraform', label: 'Terraform' }, { value: 'opentofu', label: 'OpenTofu' }], deployment.executor || 'terraform'),
          deploymentHostnameSchemeField,
          templateGuestCredentialField,
          guestCredentialField,
          selectField('Pula IPAM', 'deployment_ipam_pool_id', poolChoices, deployment.ipam_pool_id || ''),
          formSection('Zmienne szablonu', 'Możesz używać placeholderów z pól self-service, np. {{ cpu }} lub {{ hostname }}.', templateVariables),
          ansibleSection)),
      awxEditor.section,
      formSection('Workflow', 'Kroki są wykonywane zgodnie z zależnościami. Mapa DAG aktualizuje się podczas edycji i wskazuje błędne zależności.',
        workflowGraph,
        node('details', { class: 'workflow-editor-details wide', open: true },
          node('summary', { text: 'Edytuj kroki workflow' }),
          workflowList,
          node('div', { class: 'editor-add-row' }, button('Dodaj krok', () => addWorkflowStep({ type: 'terraform_apply' }), 'primary')))));

    blueprintSelector.querySelector('select').addEventListener('change', event => {
      const selectedId = Number(event.currentTarget.value || 0);
      if (selectedId === Number(item?.id || 0)) return;
      const selectedBlueprint = selectedId
        ? blueprintResult.items.find(blueprint => Number(blueprint.id) === selectedId)
        : null;
      closeModal();
      blueprintForm(selectedBlueprint);
    });

    deploymentHostnameSchemeField.querySelector('select').addEventListener('change', syncDeploymentNameMode);
    syncDeploymentNameMode();
    refreshDeploymentTemplate();

    openModal({
      title: item ? `Edytuj ${item.name}` : 'Nowy Blueprint',
      eyebrow: 'Automation Designer',
      body: fields,
      submitLabel: item ? 'Zapisz nową wersję' : 'Utwórz Blueprint',
      wide: true,
      onSubmit: async (_data, form) => {
        saveDeploymentVariables();
        const variablesSchema = {};
        form.querySelectorAll('[data-variable-row]').forEach(row => {
          const key = row.querySelector('[name="variable_key"]').value.trim();
          if (!key) throw new Error('Każde pole self-service musi mieć klucz.');
          if (variablesSchema[key]) throw new Error(`Klucz „${key}” występuje więcej niż raz.`);
          const type = row.querySelector('[name="variable_type"]').value;
          const definition = {
            type,
            label: row.querySelector('[name="variable_label"]').value.trim() || null,
            required: row.querySelector('[name="variable_required"]').checked,
          };
          const defaultRaw = row.querySelector('[name="variable_default"]').value.trim();
          if (defaultRaw !== '') {
            if (type === 'integer') definition.default = Number.parseInt(defaultRaw, 10);
            else if (type === 'boolean') definition.default = ['true', '1', 'tak', 'yes'].includes(defaultRaw.toLowerCase());
            else definition.default = defaultRaw;
          }
          if (type === 'integer') {
            const min = row.querySelector('[name="variable_min"]').value;
            const max = row.querySelector('[name="variable_max"]').value;
            if (min !== '') definition.min = Number(min);
            if (max !== '') definition.max = Number(max);
          }
          if (type === 'select') {
            definition.options = splitValues(row.querySelector('[name="variable_options"]').value);
            if (!definition.options.length) throw new Error(`Pole „${key}” typu lista wymaga co najmniej jednej opcji.`);
          }
          variablesSchema[key] = definition;
        });

        const workflow = [...form.querySelectorAll('[data-workflow-row]')].map(row => {
          const step = {
            id: row.querySelector('[name="workflow_id"]').value.trim(),
            type: row.querySelector('[name="workflow_type"]').value,
            depends_on: splitValues(row.querySelector('[name="workflow_depends"]').value),
            retry: Number(row.querySelector('[name="workflow_retry"]').value || 0),
            timeout: Number(row.querySelector('[name="workflow_timeout"]').value || (row.querySelector('[name="workflow_type"]').value === 'wait_for_ip' ? 180 : 600)),
            conditions: row.querySelector('[name="workflow_conditions"]').value.trim()
              ? window.BlueprintFormUtils.parseObject(row.querySelector('[name="workflow_conditions"]').value, 'Warunki kroku')
              : {},
          };
          const rollback = row.querySelector('[name="workflow_rollback"]').value.trim();
          if (rollback) step.rollback = rollback;
          return step;
        });
        if (!workflow.length) throw new Error('Blueprint musi zawierać co najmniej jeden krok workflow.');
        window.BlueprintClassicWorkflow.assertValid(workflow);
        const awx = awxEditor.value();
        window.BlueprintAwxEditor.validateWorkflow(awx, workflow);

        const template = currentTemplate();
        const hostnameSchemeId = Number(form.elements.deployment_hostname_scheme_id.value || 0);
        const deploymentVariables = {
          ...(variableState.get(template.id) || readBlueprintTemplateVariables(form, template)),
        };
        if (hostnameSchemeId && Object.prototype.hasOwnProperty.call(template.variables_schema?.properties || {}, 'name')) {
          deploymentVariables.name = '{{ hostname }}';
        }
        const deploymentPayload = {
          name: hostnameSchemeId ? '{{ hostname }}' : form.elements.deployment_name.value,
          provider_id: Number(form.elements.deployment_provider_id.value),
          credentials_id: Number(form.elements.deployment_credentials_id.value),
          template: template.id,
          executor: form.elements.deployment_executor.value,
          variables: deploymentVariables,
          hostname_values: Object.fromEntries(
            Object.entries(deployment.hostname_values || {}).filter(([name]) => !['location', 'role'].includes(name))
          ),
          guest_credential_id: form.elements.deployment_guest_credential_id?.value
            ? Number(form.elements.deployment_guest_credential_id.value) : null,
          template_guest_credential_id: form.elements.deployment_template_guest_credential_id?.value
            ? Number(form.elements.deployment_template_guest_credential_id.value) : null,
          guest_account_mode: deployment.guest_account_mode || 'cloud_init_managed',
          apmid: deployment.apmid || null,
          environment: deployment.environment || null,
          select_apmid_on_execute: Boolean(deployment.select_apmid_on_execute),
          select_environment_on_execute: Boolean(deployment.select_environment_on_execute),
          awx,
        };
        if (!deploymentPayload.provider_id) throw new Error('Wybierz provider dla Blueprintu.');
        if (!deploymentPayload.credentials_id) throw new Error('Wybierz dane dostępowe dla Blueprintu.');
        if (hostnameSchemeId) deploymentPayload.hostname_scheme_id = hostnameSchemeId;
        if (form.elements.deployment_ipam_pool_id.value) deploymentPayload.ipam_pool_id = Number(form.elements.deployment_ipam_pool_id.value);

        const ansibleRuns = ansibleEditor.getRuns();
        deploymentPayload.ansible_runs = ansibleRuns;
        deploymentPayload.ansible = ansibleRuns[0] || null;

        const selectedIds = name => [...form.querySelectorAll(`[name="${name}"]:checked`)].map(input => Number(input.value));
        const payload = {
          slug: form.elements.slug.value,
          name: form.elements.name.value,
          description: form.elements.description.value,
          is_active: form.elements.is_active.checked,
          visibility: {
            backend: form.elements.visibility_backend.checked,
            cloudportal: form.elements.visibility_cloudportal.checked,
            api: form.elements.visibility_api.checked,
          },
          allowed_role_ids: selectedIds('allowed_role_ids'),
          allowed_user_ids: selectedIds('allowed_user_ids'),
          manager_role_ids: selectedIds('manager_role_ids'),
          variables_schema: variablesSchema,
          deployment: deploymentPayload,
          workflow,
          requires_approval: form.elements.requires_approval.checked, auto_approve_for_executors: window.BlueprintApprovalPolicyUI.parseAuto(form.elements.auto_approve_for_executors.value), approval_timeout_hours: window.BlueprintApprovalPolicyUI.parseTimeout(form.elements.approval_timeout_hours.value),
          recovery_policy: form.elements.recovery_policy.value,
        };
        await api(item ? `/blueprints/${item.id}` : '/blueprints', {
          method: item ? 'PUT' : 'POST',
          body: payload,
        });
        toast(item ? 'Utworzono nową wersję Blueprintu.' : 'Blueprint utworzony.');
        navigate('blueprints');
      },
    });
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function executeBlueprint(item) {
  try {
    const fields = node('div', { class: 'form-grid' });
    const apmidContext = await window.BlueprintRuntimeApmid.prepare(item, fields);

    for (const [name, definition] of Object.entries(item.variables_schema || {})) {
      if (definition.type === 'select') {
        fields.append(selectField(definition.label || name, name, (definition.options || []).map(value => ({ value, label: value })), definition.default, { required: definition.required }));
      } else if (definition.type === 'boolean') {
        fields.append(checkboxField(definition.label || name, name, Boolean(definition.default)));
      } else {
        fields.append(field(definition.label || name, name, {
          type: definition.type === 'integer' ? 'number' : 'text',
          value: definition.default ?? '',
          min: definition.min,
          max: definition.max,
          required: definition.required,
        }));
      }
    }

    let scheme = null;
    if (item.deployment?.hostname_scheme_id && allowed('hostnames.read')) {
      const result = await api('/hostname-schemes?limit=200');
      scheme = result.items.find(value => Number(value.id) === Number(item.deployment.hostname_scheme_id)) || null;
    }
    if (scheme) {
      const defaults = item.deployment?.hostname_values || {};
      const missingTokens = window.BlueprintRuntimeApmid.hostnameTokens(scheme.pattern)
        .filter(token => !['location', 'role'].includes(token))
        .filter(token => !defaults[token]);
      if (missingTokens.length) {
        const missingPattern = missingTokens.map(token => `{${token}}`).join('-');
        fields.append(formSection(
          'Nazwa hosta',
          `Wzorzec: ${scheme.pattern}. Pozostałe składniki są zapisane w Blueprintcie.`,
          window.BlueprintRuntimeApmid.hostnameValueFields(missingPattern),
        ));
      } else {
        fields.append(node('div', { class: 'field-help wide', text: `Nazwa hosta zostanie wygenerowana automatycznie według wzorca ${scheme.pattern}.` }));
      }
    } else if (item.deployment?.hostname_scheme_id) {
      fields.append(node('div', { class: 'field-help wide', text: 'Blueprint ma zapisany schemat nazwy hosta. Brak uprawnienia do odczytu schematu — zostaną użyte zapisane wartości domyślne.' }));
    }

    openModal({
      title: `Utwórz VM · ${item.name}`,
      eyebrow: `Produkt z Blueprintu v${item.version}`,
      body: fields,
      submitLabel: 'Utwórz VM',
      wide: true,
      onSubmit: async (_data, form) => {
        const variables = {};
        for (const [name, definition] of Object.entries(item.variables_schema || {})) {
          const control = form.elements[name];
          if (definition.type === 'boolean') variables[name] = control.checked;
          else if (control.value !== '') variables[name] = definition.type === 'integer' ? Number(control.value) : control.value;
        }
        const payload = {
          variables,
          hostname_values: window.BlueprintRuntimeApmid.readHostnameValues(form),
        };
        const runtimeClassification = window.BlueprintRuntimeApmid.read(form, apmidContext);
        if (runtimeClassification.apmid) payload.apmid = runtimeClassification.apmid;
        if (runtimeClassification.environment) payload.environment = runtimeClassification.environment;

        const result = await api(`/blueprints/${item.id}/execute`, {
          method: 'POST',
          idempotent: true,
          body: payload,
        });
        toast(`Utworzono „${result.name}”. VM jest już widoczna w „Moje zasoby”. Status i etap provisioningu będą aktualizowane automatycznie; jeśli Proxmox jest offline, zadanie wznowi się po odzyskaniu połączenia.`);
        navigate('my-resources');
      },
    });
  } catch (error) {
    toast(error.message, 'error');
  }
}

registerCommand('blueprints.proxmoxTemplateWizard', item => item ? window.BlueprintWizard.open({ item }) : window.BlueprintWizard.open());
registerCommand('blueprints.proxmoxWithHostnameScheme', schemeId => window.BlueprintWizard.open({ hostnameSchemeId: schemeId }));
registerCommand('blueprints.execute', executeBlueprint);
registerCommand('blueprints.create', () => window.BlueprintWizard.open());
registerView({ id: 'blueprints', label: 'Blueprinty', icon: 'B', permission: 'blueprints.read', order: 70 }, blueprintsView);
})();
