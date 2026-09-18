'use strict';

async function blueprintsView() {
  const blueprints = (await api('/blueprints?limit=200')).items;
  const canDesignBlueprint = allowed('providers.read') && allowed('credentials.read') && allowed('terraform.read');
  const canQuickProxmox = canDesignBlueprint && allowed('hostnames.read') && allowed('ipam.read');
  const actions = [];
  if (allowed('blueprints.create') && canQuickProxmox) actions.push(button('Szybki Blueprint Proxmox', () => proxmoxBlueprintForm(), 'primary'));
  if (allowed('blueprints.create') && canDesignBlueprint) actions.push(button('Nowy Blueprint', () => blueprintForm()));
  dom.content.replaceChildren(heading('Wersjonowane definicje self-service. DAG, formularz zmiennych i provisioning są wykonywane przez wspólną warstwę API.', actions),
    table([
      { label: 'Blueprint', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: `${item.slug} · v${item.version}` })) },
      { label: 'Status', value: item => badge(statusLabel(item.is_active ? 'active' : 'inactive'), item.is_active ? 'ok' : 'danger') },
      { label: 'Widoczność', value: item => Object.entries(item.visibility).filter(([, value]) => value).map(([key]) => ({ backend: 'Backend', cloudportal: 'CloudPortal', api: 'API' }[key] || key)).join(', ') || '—' },
      { label: 'Kroki', value: item => item.workflow.length },
      { label: 'Zasady', value: item => node('div', { class: 'row-actions' }, item.requires_approval ? badge('Wymaga akceptacji', 'warning') : badge('Bez akceptacji', 'info'), item.recovery_policy === 'destroy_on_failure' ? badge('Usuń po błędzie', 'danger') : badge('Zachowaj po błędzie', 'info')) },
      { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
    ], blueprints, item => {
      const result = [];
      if (allowed('blueprints.execute') && (!item.requires_approval || allowed('blueprints.approve')) && item.is_active && item.visibility.backend) result.push(button('Uruchom', () => executeBlueprint(item), 'primary'));
      if (allowed('blueprints.update') && canQuickProxmox && item.deployment?.template === 'proxmox-vm') result.push(button('Szybka edycja', () => proxmoxBlueprintForm(item)));
      if (allowed('blueprints.update') && canDesignBlueprint) result.push(button('Edytuj', () => blueprintForm(item)));
      if (allowed('blueprints.delete')) result.push(button('Usuń', () => confirmAction('Usuń Blueprint', `Definicja ${item.name} zostanie usunięta. Istniejące wdrożenia zachowają snapshot.`, async () => { await api(`/blueprints/${item.id}`, { method: 'DELETE' }); toast('Blueprint usunięty.'); navigate('blueprints'); }), 'danger'));
      return result;
    }));
}

function jsonValue(value) { return JSON.stringify(value, null, 2); }
function parseObject(value, label) {
  try { const parsed = JSON.parse(value); if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(); return parsed; }
  catch { throw new Error(`${label} musi zawierać poprawny obiekt JSON.`); }
}
function parseArray(value, label) {
  try { const parsed = JSON.parse(value); if (!Array.isArray(parsed)) throw new Error(); return parsed; }
  catch { throw new Error(`${label} musi zawierać poprawną tablicę JSON.`); }
}

function hostnamePatternTokens(pattern) {
  const automatic = new Set(['number', 'random', 'year']);
  return [...new Set(Array.from(String(pattern || '').matchAll(/{([a-z]+)}/g), match => match[1]))]
    .filter(token => !automatic.has(token));
}

function blueprintTags(value) {
  return [...new Set(String(value || '').split(/[,\n]+/).map(item => item.trim().toLowerCase()).filter(Boolean))];
}

function setSelectChoices(select, choices, selected = '', placeholder = '') {
  select.replaceChildren();
  if (placeholder) select.append(node('option', { value: '', text: placeholder }));
  choices.forEach(choice => select.append(node('option', {
    value: choice.value,
    text: choice.label,
    selected: String(choice.value) === String(selected),
  })));
}

function blueprintWorkflow(options) {
  const steps = [];
  let previous = [];
  const add = (id, type) => {
    steps.push({ id, type, depends_on: [...previous] });
    previous = [id];
  };
  if (options.hostname) add('hostname', 'generate_hostname');
  if (options.ipam) add('ip', 'allocate_ip');
  add('clone', 'clone_vm');
  add('cloud_init', 'cloud_init');
  if (options.tags) add('tags', 'set_tags');
  add('apply', 'terraform_apply');
  if (options.waitAgent || options.ansible) add('agent', 'wait_for_agent');
  if (options.ansible) add('ansible', 'run_ansible_playbook');
  return steps;
}

async function proxmoxBlueprintForm(item = null) {
  try {
    const [providerResult, schemeResult, poolResult, playbookResult, credentialResult] = await Promise.all([
      api('/providers?limit=200'),
      api('/hostname-schemes?limit=200'),
      api('/ipam/pools?limit=200'),
      allowed('ansible.read') ? api('/ansible/playbooks') : Promise.resolve({ items: [] }),
      api('/credentials?limit=200'),
    ]);
    const providers = providerResult.items.filter(value => value.type === 'proxmox');
    if (!providers.length) throw new Error('Najpierw dodaj platformę Proxmox.');

    const schemes = schemeResult.items.filter(value => value.is_active);
    const pools = poolResult.items.filter(value => value.is_active);
    const playbooks = playbookResult.items;
    const credentials = credentialResult.items;
    const deployment = item?.deployment || {};
    const variables = deployment.variables || {};
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
      ...schemes.map(value => ({ value: value.id, label: value.name + ' — ' + value.pattern })),
      { value: '__new__', label: '+ Utwórz nowy pattern hostname' },
    ];
    const schemeField = selectField(
      'Wzorzec nazwy hosta', 'hostname_scheme_id', schemeChoices,
      deployment.hostname_scheme_id || (schemes[0]?.id || '__new__'),
      { required: true, wide: true }
    );
    const newScheme = node('div', { class: 'form-grid designer-subsection wide' },
      field('Nazwa wzorca', 'hostname_scheme_name', { value: item ? item.name + ' hostnames' : '', placeholder: 'Np. WRO PROD WEB' }),
      field('Wzorzec', 'hostname_pattern', {
        value: '{env}-{role}-{number}',
        placeholder: '{location}-{env}-{role}-{number}',
        help: 'Dostępne m.in. {location}, {env}, {environment}, {application}, {service}, {role}, {os}, {cluster}, {site}, {year}, {number}, {random}.',
        wide: true,
      }),
      field('Dopełnienie numeru', 'hostname_padding', { type: 'number', min: 1, max: 9, value: 3 })
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
    const ansibleCredentialField = selectField(
      'Dane dostępowe Ansible', 'ansible_credentials_id',
      credentials.filter(value => ['ssh', 'winrm'].includes(value.type)).map(value => ({
        value: value.id, label: value.name + ' [' + value.type + '] (#' + value.id + ')',
      })),
      deployment.ansible?.credentials_id || '', { placeholder: 'Wybierz dane dostępowe' }
    );

    const workflowPreview = node('ol', { class: 'workflow-preview' });
    const preservedVariablesSchema = item?.variables_schema || {};

    const fields = node('div', { class: 'form-grid blueprint-designer' },
      node('div', { class: 'designer-heading wide' }, node('strong', { text: '1. Blueprint' }), node('span', { text: 'Zapisujesz kompletny preset VM.' })),
      field('Slug', 'slug', { required: true, value: item?.slug || '' }),
      field('Nazwa', 'name', { required: true, value: item?.name || '' }),
      field('Opis', 'description', { tag: 'textarea', value: item?.description || '', wide: true }),
      providerField,

      node('div', { class: 'designer-heading wide' }, node('strong', { text: '2. Proxmox i obraz' }), node('span', { text: 'Te wartości zostaną użyte przy każdym uruchomieniu.' })),
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

      node('div', { class: 'designer-heading wide' }, node('strong', { text: '3. Hostname' }), node('span', { text: 'Pattern i wartości są zapisywane w Blueprintcie.' })),
      schemeField, newScheme, hostnameDefaults,

      node('div', { class: 'designer-heading wide' }, node('strong', { text: '4. Cloud-init' }), node('span', { text: 'Konfiguracja sieci, DNS i konta trafia do template Proxmox.' })),
      ipMode, ipamField, staticIp, staticGateway,
      field('Użytkownik SSH', 'ssh_username', { value: variables.ssh_username || 'clouduser', required: true }),
      field('Klucz publiczny SSH (opcjonalnie)', 'ssh_public_key', { tag: 'textarea', value: variables.ssh_public_key || '', wide: true }),
      field('Serwery DNS', 'dns_servers', { value: (variables.dns_servers || []).join(', '), placeholder: '1.1.1.1, 8.8.8.8' }),
      field('Domena wyszukiwania DNS', 'dns_domain', { value: variables.dns_domain || '', placeholder: 'lab.example.com' }),

      node('div', { class: 'designer-heading wide' }, node('strong', { text: '5. Workflow' }), node('span', { text: 'Bez ponownego wybierania obrazu, hostname, tagów ani cloud-init.' })),
      checkboxField('Czekaj na QEMU Agent po Terraform apply', 'wait_agent', true),
      playbookField, ansibleCredentialField,
      node('div', { class: 'workflow-box wide' }, node('strong', { text: 'Podgląd workflow' }), workflowPreview),

      node('div', { class: 'designer-heading wide' }, node('strong', { text: '6. Dostęp i recovery' })),
      checkboxField('Aktywny', 'is_active', item?.is_active ?? true),
      checkboxField('Panel backendu', 'visibility_backend', item?.visibility?.backend ?? true),
      checkboxField('CloudPortal', 'visibility_cloudportal', item?.visibility?.cloudportal ?? false),
      checkboxField('API', 'visibility_api', item?.visibility?.api ?? true),
      checkboxField('Wymaga zatwierdzenia przed uruchomieniem', 'requires_approval', item?.requires_approval ?? false),
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

    const updateHostnameFields = () => {
      const selected = schemeSelect.value;
      const scheme = schemes.find(value => String(value.id) === String(selected));
      const custom = selected === '__new__';
      newScheme.hidden = !custom;
      const pattern = custom
        ? newScheme.querySelector('[name="hostname_pattern"]').value
        : (scheme?.pattern || '');
      const defaults = deployment.hostname_values || {};
      hostnameDefaults.replaceChildren();
      const tokens = hostnamePatternTokens(pattern);
      if (!tokens.length) {
        hostnameDefaults.append(node('div', { class: 'field-help wide', text: 'Pattern używa wyłącznie automatycznych tokenów {number}/{random}/{year}.' }));
      } else {
        tokens.forEach(token => hostnameDefaults.append(field(
          'Domyślne {' + token + '}', 'hostname_token_' + token,
          { required: true, value: defaults[token] || '', placeholder: token === 'env' ? 'prod' : token, help: 'Możesz wpisać stałą wartość albo {{ nazwa_zmiennej }}.' }
        )));
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
        setSelectChoices(
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
        tags: blueprintTags(fields.querySelector('[name="tags"]')?.value).length > 0,
        waitAgent: Boolean(fields.querySelector('[name="wait_agent"]')?.checked),
        ansible: Boolean(playbookSelect.value),
      };
      const steps = blueprintWorkflow(options);
      workflowPreview.replaceChildren(...steps.map(step =>
        node('li', {}, node('strong', { text: step.type }), node('span', { class: 'muted', text: step.depends_on.length ? ' ← ' + step.depends_on.join(', ') : '' }))
      ));
    }

    const loadNodeResources = async () => {
      const providerId = providerSelect.value;
      const targetNode = nodeSelect.value;
      if (!providerId || !targetNode) return;
      const [storageResult, networkResult] = await Promise.all([
        api('/providers/' + providerId + '/storages?node=' + encodeURIComponent(targetNode)),
        api('/providers/' + providerId + '/networks?node=' + encodeURIComponent(targetNode)),
      ]);
      const storages = storageResult.items.filter(value => !value.disable && String(value.content || '').includes('images'));
      setSelectChoices(
        storageSelect,
        storages.map(value => ({ value: value.storage, label: value.storage + (value.type ? ' [' + value.type + ']' : '') })),
        variables.storage || storageSelect.value,
        'Wybierz storage'
      );
      const networks = networkResult.items.filter(value => value.iface);
      setSelectChoices(
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
      setSelectChoices(
        nodeSelect,
        nodeResult.items.map(value => ({ value: value.node, label: value.node })),
        variables.node || nodeSelect.value,
        'Wybierz węzeł'
      );
      const currentImage = variables.template_id
        ? String(variables.template_node || '') + '|' + String(variables.template_id)
        : '';
      setSelectChoices(
        imageSelect,
        templateRows.map(value => ({
          value: String(value.node || '') + '|' + String(value.vmid),
          label: (value.name || 'VM template') + ' — VMID ' + value.vmid + ' @ ' + value.node,
        })),
        currentImage,
        'Wybierz szablon'
      );
      await loadNodeResources();
    };

    providerSelect.addEventListener('change', loadProvider);
    nodeSelect.addEventListener('change', loadNodeResources);
    schemeSelect.addEventListener('change', updateHostnameFields);
    newScheme.querySelector('[name="hostname_pattern"]').addEventListener('input', updateHostnameFields);
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
      title: item ? 'Edytuj ' + item.name : 'Nowy Blueprint Proxmox',
      eyebrow: 'Blueprint Designer',
      body: fields,
      submitLabel: item ? 'Zapisz nową wersję' : 'Utwórz Blueprint',
      wide: true,
      onSubmit: async (data, form) => {
        const provider = providers.find(value => String(value.id) === String(data.get('provider_id')));
        if (!provider) throw new Error('Wybierz platformę Proxmox.');

        let schemeId = data.get('hostname_scheme_id');
        let selectedPattern = '';
        if (schemeId === '__new__') {
          const pattern = data.get('hostname_pattern');
          const created = await api('/hostname-schemes', {
            method: 'POST',
            body: {
              name: data.get('hostname_scheme_name') || data.get('name') + ' hostnames',
              pattern,
              next_number: 1,
              padding: Number(data.get('hostname_padding') || 3),
              is_active: true,
            },
          });
          schemeId = created.id;
          selectedPattern = created.pattern;
        } else {
          const selectedScheme = schemes.find(value => String(value.id) === String(schemeId));
          selectedPattern = selectedScheme?.pattern || '';
        }

        const hostnameValues = {};
        hostnamePatternTokens(selectedPattern).forEach(token => {
          const value = form.elements['hostname_token_' + token]?.value.trim();
          if (value) hostnameValues[token] = value;
        });

        const image = String(data.get('image') || '').split('|');
        const templateNode = image[0];
        const templateId = Number(image[1]);
        if (!templateId) throw new Error('Wybierz obraz/template Proxmox.');

        const tags = blueprintTags(data.get('tags'));
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

        const workflow = blueprintWorkflow({
          hostname: Boolean(schemeId),
          ipam: mode === 'ipam',
          tags: tags.length > 0,
          waitAgent: data.has('wait_agent'),
          ansible: Boolean(ansible),
        });

        const payload = {
          slug: data.get('slug'),
          name: data.get('name'),
          description: data.get('description'),
          is_active: data.has('is_active'),
          visibility: {
            backend: data.has('visibility_backend'),
            cloudportal: data.has('visibility_cloudportal'),
            api: data.has('visibility_api'),
          },
          allowed_role_ids: item?.allowed_role_ids || [],
          allowed_user_ids: item?.allowed_user_ids || [],
          variables_schema: preservedVariablesSchema,
          deployment: {
            name: '{{ hostname }}',
            provider_id: provider.id,
            credentials_id: provider.credentials_id,
            hostname_scheme_id: Number(schemeId),
            hostname_values: hostnameValues,
            ipam_pool_id: ipamPoolId,
            template: 'proxmox-vm',
            executor: 'terraform',
            variables: vmVariables,
            ansible,
          },
          workflow,
          requires_approval: data.has('requires_approval'),
          recovery_policy: data.get('recovery_policy'),
        };

        await api(item ? '/blueprints/' + item.id : '/blueprints', {
          method: item ? 'PUT' : 'POST',
          body: payload,
        });
        toast(item ? 'Utworzono nową wersję Blueprintu.' : 'Blueprint gotowy do szybkiego tworzenia VM.');
        navigate('blueprints');
      },
    });
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function blueprintForm(item = null) {
  try {
    const [providerResult, credentialResult, templateResult, schemeResult, poolResult, roleResult, userResult, playbookResult] = await Promise.all([
      api('/providers?limit=200'),
      api('/credentials?limit=200'),
      api('/templates'),
      allowed('hostnames.read') ? api('/hostname-schemes?limit=200') : Promise.resolve({ items: [] }),
      allowed('ipam.read') ? api('/ipam/pools?limit=200') : Promise.resolve({ items: [] }),
      allowed('roles.read') ? api('/roles?limit=200') : Promise.resolve({ items: [] }),
      allowed('users.read') ? api('/users?limit=200') : Promise.resolve({ items: [] }),
      allowed('ansible.execute') && allowed('ansible.read') ? api('/ansible/playbooks') : Promise.resolve({ items: [] }),
    ]);
    const providers = providerResult.items;
    const credentials = credentialResult.items;
    const templates = templateResult.items;
    const schemes = schemeResult.items;
    const pools = poolResult.items;
    const playbooks = playbookResult.items;
    if (!templates.length) throw new Error('Katalog nie zawiera szablonów Terraform/OpenTofu.');

    const variableList = node('div', { class: 'editor-list wide' });
    const workflowList = node('div', { class: 'editor-list wide' });
    let variableCounter = 0;
    let workflowCounter = 0;

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
      ['generate_hostname', 'Wygeneruj hostname'], ['allocate_ip', 'Przydziel IP'], ['release_ip', 'Zwolnij IP'],
      ['create_vm', 'Utwórz VM'], ['clone_vm', 'Sklonuj VM'], ['configure_vm', 'Skonfiguruj VM'],
      ['cloud_init', 'Cloud-init'], ['start_vm', 'Uruchom VM'], ['wait_for_vm', 'Czekaj na VM'],
      ['wait_for_agent', 'Czekaj na guest agent'], ['wait_for_ip', 'Czekaj na IP'], ['wait_for_ssh', 'Czekaj na SSH'],
      ['set_hostname', 'Ustaw hostname'], ['run_ansible_playbook', 'Uruchom Ansible'], ['terraform_plan', 'Terraform plan'],
      ['terraform_apply', 'Terraform apply'], ['create_snapshot', 'Utwórz snapshot'], ['set_tags', 'Ustaw tagi'], ['health_check', 'Health check'],
      ['condition', 'Warunek'], ['approval', 'Akceptacja'], ['delay', 'Opóźnienie'], ['notification', 'Powiadomienie'],
    ];
    const addWorkflowStep = (step = {}) => {
      workflowCounter += 1;
      const advanced = node('details', { class: 'advanced-options wide' },
        node('summary', { text: 'Opcje zaawansowane' }),
        node('div', { class: 'form-grid advanced-options-body' },
          field('Liczba ponowień', 'workflow_retry', { type: 'number', min: 0, max: 10, value: step.retry ?? 0 }),
          field('Limit czasu (s)', 'workflow_timeout', { type: 'number', min: 1, max: 86400, value: step.timeout ?? 600 }),
          field('Krok cofania — ID (opcjonalnie)', 'workflow_rollback', { value: step.rollback || '' }),
          field('Warunki — JSON (opcjonalnie)', 'workflow_conditions', {
            tag: 'textarea', wide: true, value: Object.keys(step.conditions || {}).length ? jsonValue(step.conditions) : '',
            help: 'Zaawansowane warunki wykonania kroku.',
          })));
      const row = node('div', { class: 'editor-card', 'data-workflow-row': String(workflowCounter) },
        node('div', { class: 'editor-card-header' },
          node('strong', { text: 'Krok workflow' }),
          button('Usuń', () => row.remove(), 'danger')),
        node('div', { class: 'form-grid' },
          field('ID kroku', 'workflow_id', { required: true, value: step.id || '', placeholder: 'np. apply' }),
          selectField('Akcja', 'workflow_type', workflowTypes.map(([value, label]) => ({ value, label })), step.type || 'terraform_apply', { required: true }),
          field('Zależy od (ID kroków)', 'workflow_depends', { value: (step.depends_on || []).join(', '), wide: true, help: 'Kilka ID oddziel przecinkami.' }),
          advanced));
      workflowList.append(row);
    };

    const defaultVariables = item?.variables_schema || {
      environment: { type: 'select', label: 'Środowisko', required: true, options: ['dev', 'test', 'prod'], default: 'dev' },
      cpu: { type: 'integer', label: 'CPU', required: true, default: 2, min: 1, max: 8 },
    };
    Object.entries(defaultVariables).forEach(([key, definition]) => addVariable(key, definition));
    const defaultWorkflow = item?.workflow || [
      { id: 'hostname', type: 'generate_hostname' },
      { id: 'clone', type: 'clone_vm', depends_on: ['hostname'] },
      { id: 'apply', type: 'terraform_apply', depends_on: ['clone'] },
    ];
    defaultWorkflow.forEach(addWorkflowStep);

    const deployment = item?.deployment || {};
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

    const existingAnsible = deployment.ansible || null;
    const ansibleToggle = checkboxField('Po wdrożeniu uruchom zatwierdzony playbook Ansible', 'deployment_ansible_enabled', Boolean(existingAnsible));
    const ansibleFields = node('div', { class: 'form-grid wide ansible-fields' });
    const ansibleSection = formSection(
      'Konfiguracja systemu po wdrożeniu',
      'Opcjonalny krok Ansible uruchamiany po utworzeniu VM i wykryciu jej adresu.',
      ansibleToggle,
      ansibleFields,
    );

    const renderBlueprintAnsible = () => {
      const template = currentTemplate();
      const supported = template.provider === 'proxmox';
      ansibleSection.hidden = !supported;
      if (!supported) {
        ansibleFields.replaceChildren();
        return;
      }
      const enabled = ansibleToggle.querySelector('input').checked;
      if (!allowed('ansible.execute')) {
        ansibleToggle.querySelector('input').disabled = true;
        ansibleFields.replaceChildren(node('p', {
          class: 'muted wide',
          text: existingAnsible
            ? 'Istniejąca konfiguracja Ansible zostanie zachowana. Brak uprawnienia ansible.execute do jej edycji.'
            : 'Brak uprawnienia ansible.execute — konfiguracja Ansible jest niedostępna.',
        }));
        return;
      }
      ansibleToggle.querySelector('input').disabled = false;
      if (!enabled) {
        ansibleFields.replaceChildren(node('p', { class: 'muted wide', text: 'Ansible nie zostanie uruchomiony po wdrożeniu.' }));
        return;
      }
      const choices = [...playbooks];
      if (existingAnsible?.playbook && !choices.some(value => value.id === existingAnsible.playbook)) {
        choices.unshift({
          id: existingAnsible.playbook,
          name: existingAnsible.playbook + ' (zapisany)',
          version: '?',
          transport: credentials.find(value => Number(value.id) === Number(existingAnsible.credentials_id))?.type || 'ssh',
          variables: Object.keys(existingAnsible.variables || {}),
        });
      }
      if (!choices.length) {
        ansibleFields.replaceChildren(node('p', { class: 'form-error wide', text: 'Katalog nie zawiera dostępnych playbooków Ansible.' }));
        return;
      }
      const playbookField = selectField('Playbook', 'deployment_ansible_playbook', choices.map(value => ({
        value: value.id, label: `${value.name} · v${value.version}`,
      })), existingAnsible?.playbook || choices[0].id, { required: true });
      const systemCredentialField = selectField('Systemowe dane dostępowe', 'deployment_ansible_credentials_id', [], existingAnsible?.credentials_id || '', { required: true });
      const variableFields = node('div', { class: 'form-grid wide' });
      ansibleFields.replaceChildren(playbookField, systemCredentialField, variableFields);

      const refreshPlaybook = () => {
        const playbook = choices.find(value => value.id === playbookField.querySelector('select').value) || choices[0];
        const matching = credentials.filter(value => value.type === playbook.transport).map(value => ({ id: value.id, label: value.name }));
        refill(
          systemCredentialField.querySelector('select'),
          matching,
          matching.length ? 'Wybierz systemowe dane dostępowe' : `Brak danych dostępowych typu ${playbook.transport}`,
          existingAnsible?.credentials_id,
        );
        variableFields.replaceChildren();
        (playbook.variables || []).forEach(name => variableFields.append(field(
          FIELD_LABELS[name] || name.replaceAll('_', ' '),
          `deployment_ansible_var_${name}`,
          { value: existingAnsible?.variables?.[name] || '', help: 'Opcjonalna zmienna zatwierdzonego playbooka.' },
        )));
      };
      playbookField.querySelector('select').addEventListener('change', refreshPlaybook);
      refreshPlaybook();
    };

    const refreshDeploymentTemplate = () => {
      saveDeploymentVariables();
      const template = currentTemplate();
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
      renderBlueprintAnsible();
    };
    templateField.querySelector('select').addEventListener('change', refreshDeploymentTemplate);
    providerField.querySelector('select').addEventListener('change', refreshCredentialChoices);
    ansibleToggle.querySelector('input').addEventListener('change', renderBlueprintAnsible);

    const roleChoices = roleResult.items.map(role => ({ value: role.id, label: role.name }));
    (item?.allowed_role_ids || []).forEach(id => {
      if (!roleChoices.some(choice => Number(choice.value) === Number(id))) roleChoices.push({ value: id, label: `Rola #${id}` });
    });
    const userChoices = userResult.items.map(user => ({ value: user.id, label: user.username + (user.email ? ' · ' + user.email : '') }));
    (item?.allowed_user_ids || []).forEach(id => {
      if (!userChoices.some(choice => Number(choice.value) === Number(id))) userChoices.push({ value: id, label: `Użytkownik #${id}` });
    });

    const schemeChoices = [{ value: '', label: 'Bez automatycznego hostname' }].concat(schemes.filter(value => value.is_active || Number(value.id) === Number(deployment.hostname_scheme_id)).map(value => ({
      value: value.id, label: `${value.name} · ${value.pattern}`,
    })));
    const poolChoices = [{ value: '', label: 'Bez automatycznego IPAM' }].concat(pools.filter(value => value.is_active || Number(value.id) === Number(deployment.ipam_pool_id)).map(value => ({
      value: value.id, label: `${value.name} · ${value.cidr}`,
    })));

    const fields = node('div', { class: 'form-grid blueprint-designer' },
      formSection('Blueprint', 'Nazwa, identyfikator i krótki opis widoczny dla użytkownika.',
        node('div', { class: 'form-grid' },
          field('Slug', 'slug', { required: true, value: item?.slug || '', placeholder: 'np. ubuntu-web' }),
          field('Nazwa', 'name', { required: true, value: item?.name || '', placeholder: 'np. Ubuntu Web Server' }),
          field('Opis', 'description', { tag: 'textarea', value: item?.description || '', wide: true }),
          checkboxField('Aktywny', 'is_active', item?.is_active ?? true))),
      formSection('Widoczność i bezpieczeństwo', 'Określ gdzie Blueprint jest dostępny i co ma się stać po nieudanym wdrożeniu.',
        node('div', { class: 'form-grid' },
          checkboxField('Panel backendu', 'visibility_backend', item?.visibility?.backend ?? true),
          checkboxField('CloudPortal', 'visibility_cloudportal', item?.visibility?.cloudportal ?? false),
          checkboxField('API', 'visibility_api', item?.visibility?.api ?? true),
          checkboxField('Wymaga akceptacji przy uruchomieniu', 'requires_approval', item?.requires_approval ?? false),
          selectField('Po błędzie wdrożenia', 'recovery_policy', [
            { value: 'preserve', label: 'Zachowaj zasoby do analizy' },
            { value: 'destroy_on_failure', label: 'Automatycznie usuń nieudane wdrożenie' },
          ], item?.recovery_policy || 'preserve'))),
      formSection('Dostęp', 'Puste listy oznaczają brak dodatkowego ograniczenia.',
        node('div', { class: 'form-grid' },
          multiCheckboxField('Dozwolone role', 'allowed_role_ids', roleChoices, item?.allowed_role_ids || [], {
            help: 'Brak zaznaczeń oznacza brak dodatkowego ograniczenia roli.',
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
          field('Nazwa wdrożenia', 'deployment_name', { required: true, value: deployment.name || '{{ hostname }}', wide: true, help: 'Możesz użyć {{ hostname }}.' }),
          templateField,
          providerField,
          credentialField,
          selectField('Silnik IaC', 'deployment_executor', [{ value: 'terraform', label: 'Terraform' }, { value: 'opentofu', label: 'OpenTofu' }], deployment.executor || 'terraform'),
          selectField('Schemat hostname', 'deployment_hostname_scheme_id', schemeChoices, deployment.hostname_scheme_id || ''),
          selectField('Pula IPAM', 'deployment_ipam_pool_id', poolChoices, deployment.ipam_pool_id || ''),
          formSection('Zmienne szablonu', 'Możesz używać placeholderów z pól self-service, np. {{ cpu }} lub {{ hostname }}.', templateVariables),
          ansibleSection)),
      formSection('Workflow', 'Kroki są wykonywane zgodnie z zależnościami. ID kroku musi być unikalne.',
        workflowList,
        node('div', { class: 'editor-add-row' }, button('Dodaj krok', () => addWorkflowStep({ type: 'terraform_apply' }), 'primary'))));

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
            timeout: Number(row.querySelector('[name="workflow_timeout"]').value || 600),
            conditions: row.querySelector('[name="workflow_conditions"]').value.trim()
              ? parseObject(row.querySelector('[name="workflow_conditions"]').value, 'Warunki kroku')
              : {},
          };
          const rollback = row.querySelector('[name="workflow_rollback"]').value.trim();
          if (rollback) step.rollback = rollback;
          return step;
        });
        if (!workflow.length) throw new Error('Blueprint musi zawierać co najmniej jeden krok workflow.');
        if (workflow.some(step => !step.id)) throw new Error('Każdy krok workflow musi mieć ID.');

        const template = currentTemplate();
        const deploymentPayload = {
          name: form.elements.deployment_name.value,
          provider_id: Number(form.elements.deployment_provider_id.value),
          credentials_id: Number(form.elements.deployment_credentials_id.value),
          template: template.id,
          executor: form.elements.deployment_executor.value,
          variables: variableState.get(template.id) || readBlueprintTemplateVariables(form, template),
          hostname_values: deployment.hostname_values || {},
        };
        if (!deploymentPayload.provider_id) throw new Error('Wybierz provider dla Blueprintu.');
        if (!deploymentPayload.credentials_id) throw new Error('Wybierz dane dostępowe dla Blueprintu.');
        if (form.elements.deployment_hostname_scheme_id.value) deploymentPayload.hostname_scheme_id = Number(form.elements.deployment_hostname_scheme_id.value);
        if (form.elements.deployment_ipam_pool_id.value) deploymentPayload.ipam_pool_id = Number(form.elements.deployment_ipam_pool_id.value);

        if (!allowed('ansible.execute') && existingAnsible) {
          deploymentPayload.ansible = existingAnsible;
        } else if (currentTemplate().provider === 'proxmox' && form.elements.deployment_ansible_enabled?.checked) {
          const playbook = playbooks.find(value => value.id === form.elements.deployment_ansible_playbook?.value)
            || (existingAnsible?.playbook === form.elements.deployment_ansible_playbook?.value
              ? { id: existingAnsible.playbook, variables: Object.keys(existingAnsible.variables || {}) }
              : null);
          if (!playbook) throw new Error('Wybierz playbook Ansible dla Blueprintu.');
          if (!form.elements.deployment_ansible_credentials_id?.value) throw new Error('Wybierz systemowe dane dostępowe dla Ansible.');
          const variables = {};
          (playbook.variables || []).forEach(name => {
            const value = form.elements[`deployment_ansible_var_${name}`]?.value?.trim();
            if (value) variables[name] = value;
          });
          deploymentPayload.ansible = {
            playbook: playbook.id,
            credentials_id: Number(form.elements.deployment_ansible_credentials_id.value),
            variables,
          };
        }

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
          variables_schema: variablesSchema,
          deployment: deploymentPayload,
          workflow,
          requires_approval: form.elements.requires_approval.checked,
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
      const missingTokens = hostnameTokens(scheme.pattern).filter(token => !defaults[token]);
      if (missingTokens.length) {
        const missingPattern = missingTokens.map(token => `{${token}}`).join('-');
        fields.append(formSection(
          'Nazwa hosta',
          `Wzorzec: ${scheme.pattern}. Pozostałe składniki są zapisane w Blueprintcie.`,
          hostnameValueFields(missingPattern),
        ));
      } else {
        fields.append(node('div', { class: 'field-help wide', text: `Nazwa hosta zostanie wygenerowana automatycznie według wzorca ${scheme.pattern}.` }));
      }
    } else if (item.deployment?.hostname_scheme_id) {
      fields.append(node('div', { class: 'field-help wide', text: 'Blueprint ma zapisany schemat nazwy hosta. Brak uprawnienia do odczytu schematu — zostaną użyte zapisane wartości domyślne.' }));
    }

    openModal({
      title: `Uruchom ${item.name}`,
      eyebrow: `Blueprint v${item.version}`,
      body: fields,
      submitLabel: 'Utwórz serwer',
      wide: true,
      onSubmit: async (_data, form) => {
        const variables = {};
        for (const [name, definition] of Object.entries(item.variables_schema || {})) {
          const control = form.elements[name];
          if (definition.type === 'boolean') variables[name] = control.checked;
          else if (control.value !== '') variables[name] = definition.type === 'integer' ? Number(control.value) : control.value;
        }
        const result = await api(`/blueprints/${item.id}/execute`, {
          method: 'POST',
          idempotent: true,
          body: { variables, hostname_values: readHostnameValues(form) },
        });
        toast(`Utworzono „${result.name}”. Zadanie ${short(result.job.id)} zostało dodane do kolejki.`);
        navigate('jobs');
      },
    });
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function hostnamesView() {
  const [schemes, reservations] = await Promise.all([api('/hostname-schemes?limit=200'), api('/hostnames?limit=200')]);
  const actions = [];
  if (allowed('hostnames.create')) actions.push(button('Nowy schemat', hostnameSchemeForm, 'primary'));
  if (allowed('hostnames.reserve')) actions.push(button('Generuj nazwę', () => generateHostname(schemes.items)));
  dom.content.replaceChildren(heading('Centralne generowanie nazw z blokadą sekwencji, wykrywaniem kolizji i historią rezerwacji.', actions),
    node('section', { class: 'panel' }, node('div', { class: 'panel-header' }, node('h2', { text: 'Schematy' })), table([
      { label: 'Nazwa', value: item => item.name }, { label: 'Wzorzec', value: item => node('span', { class: 'mono', text: item.pattern }) },
      { label: 'Następny numer', value: item => item.next_number }, { label: 'Status', value: item => badge(statusLabel(item.is_active ? 'active' : 'inactive'), item.is_active ? 'ok' : 'danger') },
    ], schemes.items, item => {
      const result = [];
      if (allowed('hostnames.update')) result.push(button('Edytuj', () => hostnameSchemeForm(item)));
      if (allowed('hostnames.delete')) result.push(button('Usuń', () => confirmAction(
        'Usuń schemat hostname',
        `Schemat „${item.name}” zostanie usunięty, jeśli nie ma historii rezerwacji.`,
        async () => {
          await api(`/hostname-schemes/${item.id}`, { method: 'DELETE' });
          toast('Schemat hostname usunięty.');
          navigate('hostnames');
        },
      ), 'danger'));
      return result;
    })),
    node('section', { class: 'panel' }, node('div', { class: 'panel-header' }, node('h2', { text: 'Rezerwacje' })), table([
      { label: 'Nazwa hosta', value: item => node('strong', { class: 'mono', text: item.hostname }) }, { label: 'Status', value: item => badge(statusLabel(item.status), statusKind(item.status)) },
      { label: 'Zasób', value: item => short(item.resource_id, 18) }, { label: 'Utworzono', value: item => formatDate(item.created_at) },
    ], reservations.items, item => {
      const result = [];
      if (allowed('hostnames.reserve') && item.status === 'reserved') result.push(button('Przypisz', () => assignHostname(item), 'primary'));
      if (allowed('hostnames.release') && item.status !== 'released') result.push(button('Zwolnij', () => confirmAction('Zwolnij nazwę hosta', `${item.hostname} będzie ponownie dostępny po wygaśnięciu historii kolizji.`, async () => {
        await api(`/hostnames/${item.id}/release`, { method: 'POST' });
        toast('Nazwa hosta zwolniona.');
        navigate('hostnames');
      }), 'danger'));
      return result;
    })));
}

async function assignHostname(item) {
  try {
    const [deploymentResult, resourceResult] = await Promise.all([
      allowed('deployments.read') ? api('/deployments?limit=200') : Promise.resolve({ items: [] }),
      allowed('inventory.read') ? api('/inventory/resources?limit=200') : Promise.resolve({ items: [] }),
    ]);
    const choices = [
      ...deploymentResult.items.map(row => ({ value: row.id, label: `Wdrożenie: ${row.name}` })),
      ...resourceResult.items.map(row => ({ value: row.id, label: `Zasób: ${row.name || row.external_id}` })),
    ];
    const fields = node('div', { class: 'form-grid' });
    if (choices.length) {
      fields.append(selectField('Zasób', 'known_resource_id', [{ value: '', label: 'Inny identyfikator' }, ...choices], ''));
    }
    fields.append(field('Identyfikator zasobu', 'resource_id', {
      required: !choices.length,
      wide: true,
      help: choices.length ? 'Wybierz zasób z listy albo wpisz własny identyfikator.' : 'Podaj ID wdrożenia lub innego zasobu.',
    }));
    openModal({
      title: `Przypisz ${item.hostname}`,
      eyebrow: 'Hostname Manager',
      body: fields,
      submitLabel: 'Przypisz',
      onSubmit: async data => {
        const resourceId = data.get('known_resource_id') || data.get('resource_id')?.trim();
        if (!resourceId) throw new Error('Wybierz lub podaj identyfikator zasobu.');
        await api(`/hostnames/${item.id}/assign?resource_id=${encodeURIComponent(resourceId)}`, { method: 'POST' });
        toast('Nazwa hosta przypisana.');
        navigate('hostnames');
      },
    });
  } catch (error) { toast(error.message, 'error'); }
}

function hostnameSchemeForm(item = null) {
  const fields = node('div', { class: 'form-grid' }, field('Nazwa', 'name', { required: true, value: item?.name || '' }), field('Wzorzec', 'pattern', { required: true, value: item?.pattern || '{location}-{env}-{role}-{number}', wide: true }), field('Następny numer', 'next_number', { type: 'number', min: 1, value: item?.next_number || 1 }), field('Dopełnienie', 'padding', { type: 'number', min: 1, max: 9, value: item?.padding || 3 }), checkboxField('Aktywny', 'is_active', item?.is_active ?? true));
  openModal({ title: item ? 'Edytuj schemat hostname' : 'Nowy schemat hostname', eyebrow: 'Hostname Manager', body: fields, onSubmit: async data => {
    await api(item ? `/hostname-schemes/${item.id}` : '/hostname-schemes', { method: item ? 'PUT' : 'POST', body: { name: data.get('name'), pattern: data.get('pattern'), next_number: Number(data.get('next_number')), padding: Number(data.get('padding')), is_active: data.has('is_active') } });
    toast('Schemat hostname zapisany.'); navigate('hostnames');
  }});
}

function generateHostname(schemes) {
  const active = schemes.filter(item => item.is_active);
  if (!active.length) {
    toast('Brak aktywnego schematu hostname.', 'error');
    return;
  }
  const schemeField = selectField('Schemat', 'scheme_id', active.map(item => ({
    value: item.id, label: `${item.name} — ${item.pattern}`,
  })), active[0].id, { required: true });
  const valuesContainer = node('div', { class: 'wide' });
  const fields = node('div', { class: 'form-grid' },
    schemeField,
    checkboxField('Zarezerwuj nazwę', 'reserve', true),
    formSection('Składniki nazwy', 'Pola wynikają automatycznie z wybranego wzorca.', valuesContainer));
  const render = () => {
    const scheme = active.find(item => String(item.id) === String(schemeField.querySelector('select').value));
    valuesContainer.replaceChildren(hostnameValueFields(scheme?.pattern || ''));
  };
  schemeField.querySelector('select').addEventListener('change', render);
  render();

  openModal({
    title: 'Generuj hostname',
    eyebrow: 'Hostname Manager',
    body: fields,
    submitLabel: 'Generuj',
    onSubmit: async (_data, form) => {
      const result = await api('/hostnames/generate', {
        method: 'POST',
        body: {
          scheme_id: Number(form.elements.scheme_id.value),
          values: readHostnameValues(form),
          reserve: form.elements.reserve.checked,
        },
      });
      toast(`Wygenerowano ${result.hostname}.`);
      navigate('hostnames');
    },
  });
}

registerView({ id: 'blueprints', label: 'Blueprinty', icon: 'B', permission: 'blueprints.read', order: 70 }, blueprintsView);
registerView({ id: 'hostnames', label: 'Nazwy hostów', icon: 'H', permission: 'hostnames.read', order: 80 }, hostnamesView);
