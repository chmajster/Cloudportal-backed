'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};
  const STEPS = [
    ['Podstawy', 'Podstawowe informacje'],
    ['Platforma', 'Platforma i źródło VM'],
    ['VM', 'Parametry VM'],
    ['Hostname', 'Nazwa hosta'],
    ['Sieć', 'Sieć'],
    ['Konfiguracja', 'Konfiguracja systemu'],
    ['Workflow', 'Workflow'],
    ['Dostęp', 'Dostęp i bezpieczeństwo'],
    ['Podsumowanie', 'Podsumowanie'],
  ];

  function safeApi(path, fallback = [], options = {}) {
    return api(path, options).then(result => result.items || result).catch(() => fallback);
  }
  function dualListGroup(title, name, rows, selected, description = '') {
    const picked = new Set((selected || []).map(value => String(value)));
    const labelFor = row => {
      const primary = row.name || row.username || ('#' + row.id);
      return row.email ? primary + ' — ' + row.email : primary;
    };
    const sorted = rows.slice().sort((a, b) => labelFor(a).localeCompare(labelFor(b), 'pl'));
    const availableSelect = node('select', {
      class: 'blueprint-wizard-dual-select',
      multiple: true,
      size: 8,
      'aria-label': title + ' — dostępne',
    });
    const selectedSelect = node('select', {
      class: 'blueprint-wizard-dual-select',
      multiple: true,
      size: 8,
      'aria-label': title + ' — wybrane',
      'data-dual-list-name': name,
    });

    const optionFor = row => node('option', {
      value: String(row.id),
      text: labelFor(row),
      title: labelFor(row),
    });
    const refill = () => {
      const current = new Set([...selectedSelect.options].map(option => option.value));
      availableSelect.replaceChildren(...sorted.filter(row => !current.has(String(row.id))).map(optionFor));
      selectedSelect.replaceChildren(...sorted.filter(row => current.has(String(row.id))).map(optionFor));
    };
    sorted.forEach(row => (picked.has(String(row.id)) ? selectedSelect : availableSelect).append(optionFor(row)));

    const move = (source, target, all = false) => {
      const moving = [...source.options].filter(option => all || option.selected).map(option => option.value);
      if (!moving.length) return;
      const targetValues = new Set([...target.options].map(option => option.value));
      moving.forEach(value => targetValues.add(value));
      const selectedValues = target === selectedSelect
        ? targetValues
        : new Set([...selectedSelect.options].map(option => option.value).filter(value => !moving.includes(value)));
      selectedSelect.replaceChildren(...sorted.filter(row => selectedValues.has(String(row.id))).map(optionFor));
      refill();
      selectedSelect.dispatchEvent(new Event('change', { bubbles: true }));
    };

    availableSelect.addEventListener('dblclick', () => move(availableSelect, selectedSelect));
    selectedSelect.addEventListener('dblclick', () => move(selectedSelect, availableSelect));

    const controls = node('div', { class: 'blueprint-wizard-dual-controls', 'aria-label': 'Przenoszenie pozycji' },
      node('button', { type: 'button', class: 'button ghost', title: 'Dodaj zaznaczone', 'aria-label': 'Dodaj zaznaczone', onClick: () => move(availableSelect, selectedSelect) }, '›'),
      node('button', { type: 'button', class: 'button ghost', title: 'Dodaj wszystkie', 'aria-label': 'Dodaj wszystkie', onClick: () => move(availableSelect, selectedSelect, true) }, '»'),
      node('button', { type: 'button', class: 'button ghost', title: 'Usuń zaznaczone', 'aria-label': 'Usuń zaznaczone', onClick: () => move(selectedSelect, availableSelect) }, '‹'),
      node('button', { type: 'button', class: 'button ghost', title: 'Usuń wszystkie', 'aria-label': 'Usuń wszystkie', onClick: () => move(selectedSelect, availableSelect, true) }, '«'));

    const body = node('div', { class: 'blueprint-wizard-dual-list' },
      node('div', { class: 'blueprint-wizard-dual-column' },
        node('span', { class: 'blueprint-wizard-dual-title', text: 'Dostępne' }),
        availableSelect),
      controls,
      node('div', { class: 'blueprint-wizard-dual-column' },
        node('span', { class: 'blueprint-wizard-dual-title', text: 'Wybrane' }),
        selectedSelect));

    if (!rows.length) body.replaceChildren(node('span', { class: 'muted', text: 'Brak dostępnych pozycji.' }));
    return node('fieldset', { class: 'blueprint-wizard-dual-fieldset' },
      node('legend', { text: title }),
      description ? node('p', { class: 'muted', text: description }) : null,
      body);
  }

  async function openBlueprintWizard(options = {}) {
    if (!parts.core || !parts.hostname || !parts.network || !parts.scope || !parts.ui || !parts.cloudInit) {
      toast('Moduły wizarda Blueprintu nie zostały załadowane.', 'error');
      return;
    }

    try {
      const [tenants, projects, projectContext, playbooks, roles, users, vmClassification] = await Promise.all([
        safeApi('/tenants?limit=200'),
        safeApi('/projects?limit=200'),
        safeApi('/project-context', { selected: null, version: 0 }),
        allowed('ansible.read') ? safeApi('/ansible/playbooks') : Promise.resolve([]),
        allowed('roles.read') ? safeApi('/roles?limit=200') : Promise.resolve([]),
        allowed('users.read') ? safeApi('/users?limit=200') : Promise.resolve([]),
        safeApi('/settings/vm-classification', {
          environments: { test: true, dev: true, nonprod: true, prod: true },
          apmids: [],
          hostname_defaults: { location: 'wro', role: 'server' },
        }),
      ]);

      const state = parts.core.stateDefaults();
      const scopeData = parts.scope.prepare(tenants, projects, projectContext, state);
      const data = {
        tenants: scopeData.tenants,
        projects: scopeData.projects,
        providers: [],
        templates: [],
        schemes: [],
        pools: [],
        playbooks: playbooks.filter(value => value.enabled !== false),
        credentials: [],
        roles,
        users: users.filter(value => value.is_active !== false),
        blueprints: [],
        managerRoles: [],
        vmClassification,
      };

      let blueprintScope = null;
      let bodyRoot = null;
      let navRoot = null;
      let footerRoot = null;

      async function discoverProvider(providerId) {
        const provider = data.providers.find(value => String(value.id) === String(providerId));
        if (!provider) return;
        state.providerType = provider.type;
        state.providerCredentialId = String(provider.credentials_id || '');
        state.providerConnected = false;
        state.providerError = '';
        const matchingTemplates = data.templates.filter(value => value.provider === provider.type);
        if (!matchingTemplates.some(value => value.id === state.terraformTemplateId)) {
          state.terraformTemplateId = matchingTemplates[0]?.id || '';
          state.genericVariables = parts.core.defaultGenericVariables(matchingTemplates[0]);
        }
        if (provider.type !== 'proxmox') {
          state.nodes = [];
          state.templates = [];
          state.storages = [];
          state.snippetStorages = [];
          state.networks = [];
          state.providerConnected = Boolean(provider.credentials_id);
          return;
        }
        try {
          const requestOptions = { headers: parts.core.scopeHeaders(state) };
          const [nodeResult, templateResult] = await Promise.all([
            api('/providers/' + provider.id + '/nodes', requestOptions),
            api('/providers/' + provider.id + '/templates', requestOptions),
          ]);
          state.nodes = nodeResult.items || [];
          state.templates = templateResult.items || [];
          if (!state.nodes.some(value => String(value.node) === String(state.node))) {
            state.node = state.nodes[0]?.node || '';
          }
          if (!state.templates.some(value =>
            String(value.vmid) === String(state.selectedTemplateVmid)
            && String(value.node || '') === String(state.selectedTemplateNode || ''))) {
            const first = state.templates[0];
            state.selectedTemplateVmid = first ? String(first.vmid) : '';
            state.selectedTemplateNode = first?.node || '';
            state.selectedTemplateName = first?.name || '';
          }
          state.terraformTemplateId = data.templates.find(value => value.provider === 'proxmox')?.id || 'proxmox-vm';
          state.providerConnected = true;
          await loadNodeResources();
        } catch (error) {
          state.providerError = error.message;
          state.providerConnected = false;
        }
      }

      async function loadNodeResources() {
        const provider = data.providers.find(value => String(value.id) === String(state.providerId));
        if (!provider || provider.type !== 'proxmox' || !state.node) return;
        try {
          const requestOptions = { headers: parts.core.scopeHeaders(state) };
          const [storageResult, networkResult, qemuReadiness] = await Promise.all([
            api('/providers/' + provider.id + '/storages?node=' + encodeURIComponent(state.node), requestOptions),
            api('/providers/' + provider.id + '/networks?node=' + encodeURIComponent(state.node), requestOptions),
            api('/providers/' + provider.id + '/qemu-agent-readiness', requestOptions).catch(error => ({
              ok: false,
              reason: error.message || 'readiness_check_failed',
            })),
          ]);
          state.qemuAgentSshReady = qemuReadiness.ok === true;
          state.qemuAgentSshReason = qemuReadiness.reason || '';
          const allStorages = (storageResult.items || []).filter(value => !value.disable);
          const storageContent = value => Array.isArray(value.content)
            ? value.content.join(',')
            : String(value.content || '');
          state.storages = allStorages.filter(value => {
            const content = storageContent(value);
            return !content || content.includes('images');
          });
          state.snippetStorages = allStorages.filter(value => storageContent(value).includes('snippets'));
          if (!state.snippetStorages.some(value =>
            String(value.storage || value.id) === String(state.cloudInitSnippetStorage))) {
            const preferredSnippet = state.snippetStorages.find(value => String(value.storage || value.id) === 'local')
              || state.snippetStorages[0];
            state.cloudInitSnippetStorage = String(preferredSnippet?.storage || preferredSnippet?.id || '');
          }
          if (!state.snippetStorages.length) state.cloudInitSnippetStorage = '';
          state.networks = (networkResult.items || []).filter(value => value.iface);
          if (!state.storages.some(value => String(value.storage || value.id) === String(state.storage))) {
            state.storage = String(state.storages[0]?.storage || state.storages[0]?.id || '');
          }
          if (!state.networks.some(value => String(value.iface) === String(state.network))) {
            state.network = state.networks.some(value => value.iface === 'vmbr0') ? 'vmbr0' : String(state.networks[0]?.iface || '');
          }
        } catch (error) {
          state.providerError = error.message;
        }
      }

      function saveStateFromInput(control) {
        if (!control?.name) return;
        const value = control.type === 'checkbox' ? control.checked : control.value;
        const mapping = {
          name: 'name',
          slug: 'slug',
          description: 'description',
          is_active: 'active',
          executor: 'executor',
          cpu: 'cpu',
          memory: 'memory',
          disk: 'disk',
          storage: 'storage',
          network: 'network',
          vlan_id: 'vlanId',
          environment: 'environment',
          apmid: 'apmid',
          select_environment_on_execute: 'selectEnvironmentOnExecute',
          select_apmid_on_execute: 'selectApmidOnExecute',
          tags: 'tags',
          ssh_username: 'sshUsername',
          ssh_public_key: 'sshPublicKey',
          guest_credential_id: 'guestCredentialId',
          hostname_enabled: 'hostnameEnabled',
          manual_vm_name: 'manualVmName',
          ipv4_address: 'ipv4Address',
          ipv4_gateway: 'ipv4Gateway',
          dns_servers: 'dnsServers',
          dns_domain: 'dnsDomain',
          ansible_enabled: 'ansibleEnabled',
          ansible_credentials_id: 'ansibleCredentialId',
          cloud_init_enabled: 'cloudInitEnabled',
          install_qemu_guest_agent: 'installQemuGuestAgent',
          wait_agent: 'waitAgent',
          advanced_workflow: 'advancedWorkflow',
          visibility_backend: 'visibilityBackend',
          visibility_cloudportal: 'visibilityCloudportal',
          visibility_api: 'visibilityApi',
          requires_approval: 'requiresApproval',
          auto_approve_for_executors: 'autoApproveForExecutors', approval_timeout_hours: 'approvalTimeoutHours',
          recovery_policy: 'recoveryPolicy',
          new_scheme_name: 'newSchemeName',
          new_scheme_pattern: 'newSchemePattern',
          new_scheme_next: 'newSchemeNext',
          new_scheme_padding: 'newSchemePadding',
        };
        const target = mapping[control.name];
        if (target) state[target] = value;
      }

      function captureCurrentStep() {
        if (!bodyRoot) return;
        const root = bodyRoot;
        if (state.step === 0) {
          state.name = root.querySelector('[name="name"]')?.value.trim() || state.name;
          state.slug = root.querySelector('[name="slug"]')?.value.trim() || state.slug;
          state.description = root.querySelector('[name="description"]')?.value.trim() || '';
          state.active = root.querySelector('[name="is_active"]')?.checked ?? state.active;
          state.executor = root.querySelector('[name="executor"]')?.value || state.executor;
        } else if (state.step === 2) {
          if (state.providerType === 'proxmox') {
            ['cpu', 'memory', 'disk'].forEach(name => {
              const input = root.querySelector('[name="' + name + '"]');
              if (input) state[name] = Number(input.value);
            });
            state.storage = root.querySelector('[name="storage"]')?.value || state.storage;
            state.network = root.querySelector('[name="network"]')?.value || state.network;
            state.vlanId = root.querySelector('[name="vlan_id"]')?.value || '';
            state.selectEnvironmentOnExecute = root.querySelector('[name="select_environment_on_execute"]')?.checked ?? state.selectEnvironmentOnExecute;
            state.selectApmidOnExecute = root.querySelector('[name="select_apmid_on_execute"]')?.checked ?? state.selectApmidOnExecute;
            state.environment = state.selectEnvironmentOnExecute
              ? ''
              : (root.querySelector('[name="environment"]')?.value || state.environment);
            state.apmid = state.selectApmidOnExecute
              ? ''
              : (root.querySelector('[name="apmid"]')?.value.trim().toUpperCase() || state.apmid);
            if (state.environment) {
              state.hostnameValues.env = state.environment;
              state.hostnameValues.environment = state.environment;
            } else if (state.selectEnvironmentOnExecute) {
              delete state.hostnameValues.env;
              delete state.hostnameValues.environment;
            }
            state.tags = root.querySelector('[name="tags"]')?.value.trim() || '';
            state.sshUsername = root.querySelector('[name="ssh_username"]')?.value.trim() || 'clouduser';
            state.sshPublicKey = root.querySelector('[name="ssh_public_key"]')?.value.trim() || '';
            state.guestCredentialId = root.querySelector('[name="guest_credential_id"]')?.value || state.guestCredentialId;
          } else {
            root.querySelectorAll('[data-generic-variable]').forEach(input => {
              const template = data.templates.find(value => value.id === state.terraformTemplateId);
              const spec = template?.variables_schema?.properties?.[input.dataset.genericVariable] || {};
              state.genericVariables[input.dataset.genericVariable] = parts.core.coerceSchemaValue(spec, input.value);
            });
          }
        } else if (state.step === 3) {
          parts.hostname.captureHostname(root, state);
        } else if (state.step === 4) {
          parts.network.captureNetwork(root, state);
        } else if (state.step === 5) {
          state.ansibleEnabled = root.querySelector('[name="ansible_enabled"]')?.checked ?? state.ansibleEnabled;
          state.ansibleCredentialId = root.querySelector('[name="ansible_credentials_id"]')?.value || state.ansibleCredentialId;
          root.querySelectorAll('[data-ansible-variable]').forEach(input => {
            state.ansibleVariables[input.dataset.ansibleVariable] = input.value.trim();
          });
        } else if (state.step === 6) {
          const cloudCredential = root.querySelector('[name="cloud_init_guest_credential_id"]');
          if (cloudCredential) state.guestCredentialId = cloudCredential.value;
          state.installQemuGuestAgent = root.querySelector('[name="install_qemu_guest_agent"]')?.checked ?? state.installQemuGuestAgent;
          state.waitAgent = root.querySelector('[name="wait_agent"]')?.checked ?? state.waitAgent;
          state.advancedWorkflow = root.querySelector('[name="advanced_workflow"]')?.checked ?? state.advancedWorkflow;
          if (state.advancedWorkflow) {
            const rows = [...root.querySelectorAll('[data-workflow-editor-row]')];
            state.workflow = rows.map(row => {
              let conditions = {};
              const rawConditions = row.querySelector('[name="workflow_conditions"]')?.value.trim();
              if (rawConditions) {
                try { conditions = JSON.parse(rawConditions); } catch { conditions = { __invalid: rawConditions }; }
              }
              return {
                id: row.querySelector('[name="workflow_id"]').value.trim(),
                type: row.querySelector('[name="workflow_type"]').value,
                depends_on: row.querySelector('[name="workflow_depends"]').value.split(',').map(value => value.trim()).filter(Boolean),
                retry: Number(row.querySelector('[name="workflow_retry"]').value || 0),
                timeout: Number(row.querySelector('[name="workflow_timeout"]').value || 600),
                rollback: row.querySelector('[name="workflow_rollback"]').value.trim() || null,
                conditions,
              };
            });
          }
        } else if (state.step === 7) {
          const ids = name => {
            const list = root.querySelector('[data-dual-list-name="' + CSS.escape(name) + '"]');
            if (list) return [...list.options].map(option => Number(option.value));
            return [...root.querySelectorAll('[name="' + name + '"]:checked')].map(input => Number(input.value));
          };
          state.visibilityBackend = root.querySelector('[name="visibility_backend"]')?.checked ?? state.visibilityBackend;
          state.visibilityCloudportal = root.querySelector('[name="visibility_cloudportal"]')?.checked ?? state.visibilityCloudportal;
          state.visibilityApi = root.querySelector('[name="visibility_api"]')?.checked ?? state.visibilityApi;
          state.allowedRoleIds = ids('allowed_role_ids');
          state.allowedUserIds = ids('allowed_user_ids');
          state.managerRoleIds = ids('manager_role_ids');
          state.requiresApproval = root.querySelector('[name="requires_approval"]')?.checked ?? state.requiresApproval;
          window.BlueprintApprovalPolicyUI.captureState(root, state);
          state.recoveryPolicy = root.querySelector('[name="recovery_policy"]')?.value || state.recoveryPolicy;
        }
      }

      function validateWorkflow() {
        const errors = {};
        if (!state.advancedWorkflow) return errors;
        if (!state.workflow.length) {
          errors.workflow = 'Workflow musi zawierać co najmniej jeden krok.';
          return errors;
        }
        const ids = state.workflow.map(value => value.id);
        if (ids.some(value => !value)) errors.workflow = 'Każdy krok workflow musi mieć ID.';
        if (new Set(ids).size !== ids.length) errors.workflow = 'ID kroków workflow muszą być unikalne.';
        const known = new Set(ids);
        for (const step of state.workflow) {
          if (step.conditions?.__invalid) errors.workflow = 'Conditions muszą być poprawnym obiektem JSON.';
          if (step.depends_on.some(value => !known.has(value) || value === step.id)) errors.workflow = 'Workflow zawiera nieprawidłową zależność.';
        }
        if (!state.workflow.some(value => ['create_vm', 'clone_vm', 'terraform_apply'].includes(value.type))) {
          errors.workflow = 'Workflow musi zawierać krok tworzący lub stosujący VM.';
        }
        Object.assign(errors, parts.cloudInit.validate(state));
        return errors;
      }

      function validateStep(index) {
        const errors = {};
        if (index === 0) {
          if (!state.tenantId || !state.projectId) errors.project_id = 'Wybierz Tenant i Projekt dla Blueprintu.';
          if (!state.name) errors.name = 'Podaj nazwę Blueprintu.';
          if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/.test(state.slug)) errors.slug = 'Slug musi mieć 1–63 znaków i używać liter, cyfr, _, . lub -.';
        } else if (index === 1) {
          const provider = data.providers.find(value => String(value.id) === String(state.providerId));
          if (!provider) errors.provider = 'Wybierz platformę.';
          if (provider?.type === 'proxmox') {
            if (!state.node) errors.node = 'Wybierz docelowy node.';
            if (!state.selectedTemplateVmid) errors.template = 'Wybierz template/VM bazową.';
            if (!state.providerConnected) errors.provider = state.providerError || 'Nie udało się odczytać zasobów Proxmox.';
          } else if (!state.terraformTemplateId) {
            errors.template = 'Wybierz szablon IaC zgodny z providerem.';
          }
          if (!provider?.credentials_id) errors.provider = 'Provider nie ma przypisanych credentials.';
        } else if (index === 2) {
          if (state.providerType === 'proxmox') {
            if (!Number.isFinite(Number(state.cpu)) || Number(state.cpu) < 1) errors.cpu = 'CPU musi być większe od 0.';
            if (!Number.isFinite(Number(state.memory)) || Number(state.memory) < 512) errors.memory = 'RAM musi mieć co najmniej 512 MiB.';
            if (!Number.isFinite(Number(state.disk)) || Number(state.disk) < 1) errors.disk = 'Dysk musi mieć co najmniej 1 GiB.';
            if (!state.storage) errors.storage = 'Wybierz storage.';
            if (!state.network) errors.network = 'Wybierz sieć/bridge.';
            if (!state.selectEnvironmentOnExecute && !state.environment) errors.environment = 'Wybierz Environment.';
            if (!state.selectApmidOnExecute && !state.apmid) errors.apmid = 'Podaj lub wybierz APMID.';
            if (state.selectEnvironmentOnExecute
                && !['test', 'dev', 'nonprod', 'prod'].some(name => data.vmClassification?.environments?.[name] !== false)) {
              errors.select_environment_on_execute = 'Brak włączonych Environment do wyboru podczas tworzenia VM.';
            }
            if (state.selectApmidOnExecute && !(data.vmClassification?.apmids || []).length) {
              errors.select_apmid_on_execute = 'Brak skonfigurowanych APMID do wyboru podczas tworzenia VM.';
            }
          } else {
            const template = data.templates.find(value => value.id === state.terraformTemplateId);
            const required = parts.core.requiredTemplateVariables(template);
            for (const name of required) {
              if (name === 'name') continue;
              const value = state.genericVariables[name];
              if (value === undefined || value === null || value === '') errors['generic_' + name] = 'Pole jest wymagane.';
            }
          }
        } else if (index === 3) {
          Object.assign(errors, parts.hostname.validateHostname(state, data));
        } else if (index === 4) {
          Object.assign(errors, parts.network.validateNetwork(state, data));
        } else if (index === 5 && state.ansibleEnabled) {
          const playbook = data.playbooks.find(value => value.id === state.playbookId);
          if (!playbook) errors.playbook = 'Wybierz playbook Ansible.';
          if (!state.ansibleCredentialId) errors.ansible_credentials_id = 'Brak zgodnych credentials Ansible.';
          for (const name of playbook?.required_variables || []) {
            if (name === 'hostname' && playbook.id === 'bootstrap-linux' && state.hostnameEnabled) continue;
            if (!state.ansibleVariables[name]) errors['ansible_' + name] = 'Uzupełnij wymaganą zmienną ' + name + '.';
          }
        } else if (index === 6) {
          Object.assign(errors, validateWorkflow());
        }
        state.errors = errors;
        return !Object.keys(errors).length;
      }

      function stepHeading(title, description) {
        return node('div', { class: 'blueprint-wizard-step-heading' },
          node('span', { class: 'eyebrow', text: 'Krok ' + (state.step + 1) + ' z ' + STEPS.length }),
          node('h3', { text: title }),
          node('p', { text: description }));
      }

      function renderBasics() {
        const name = field('Nazwa Blueprintu', 'name', {
          value: state.name,
          required: true,
          placeholder: 'Np. Serwer WWW produkcyjny',
        });
        const slug = field('Slug', 'slug', {
          value: state.slug,
          required: true,
          placeholder: 'serwer-www-produkcyjny',
          help: 'Generowany automatycznie z nazwy. Zmieniaj tylko, jeśli potrzebujesz stabilnego identyfikatora API.',
        });
        const advanced = node('details', { class: 'advanced-options wide' },
          node('summary', { text: 'Opcje zaawansowane' }),
          node('div', { class: 'advanced-options-body form-grid' },
            slug,
            selectField('Silnik IaC', 'executor', [
              { value: 'terraform', label: 'Terraform' },
              { value: 'opentofu', label: 'OpenTofu' },
            ], state.executor)));

        const scopeFields = blueprintScope.renderFields();

        const content = node('div', { class: 'form-grid' },
          node('div', { class: 'blueprint-wizard-info wide' },
            node('strong', { text: 'Blueprint definiuje sposób automatycznego tworzenia maszyny wirtualnej i jej konfiguracji.' }),
            node('span', { text: 'Zakres: ' + blueprintScope.tenantLabel(state.tenantId) + ' · ' + blueprintScope.projectLabel(state.projectId) })),
          ...scopeFields,
          name,
          checkboxField('Aktywny', 'is_active', state.active),
          field('Krótki opis', 'description', {
            tag: 'textarea', value: state.description, wide: true,
            placeholder: 'Do czego służy ten Blueprint i kiedy powinien być używany?',
          }),
          advanced);

        const nameInput = name.querySelector('input');
        const slugInput = slug.querySelector('input');
        nameInput.addEventListener('input', () => {
          state.name = nameInput.value;
          if (!state.slugTouched) {
            state.slug = parts.core.slugify(nameInput.value);
            slugInput.value = state.slug;
          }
        });
        slugInput.addEventListener('input', () => {
          state.slugTouched = true;
          state.slug = slugInput.value;
        });
        content.querySelectorAll('input,textarea,select').forEach(control => {
          if (control !== nameInput && control !== slugInput) control.addEventListener('input', () => saveStateFromInput(control));
          control.addEventListener('change', () => {
            saveStateFromInput(control);
            if (control.name === 'environment') {
              state.hostnameValues.env = control.value;
              state.hostnameValues.environment = control.value;
            }
            if (control.name === 'apmid') state.apmid = String(control.value || '').trim().toUpperCase();
          });
        });
        return content;
      }

      function providerCard(provider) {
        const active = String(provider.id) === String(state.providerId);
        const credential = data.credentials.find(value => Number(value.id) === Number(provider.credentials_id));
        const card = node('button', {
          type: 'button',
          class: 'blueprint-wizard-select-card blueprint-wizard-provider-card' + (active ? ' selected' : ''),
          'aria-pressed': String(active),
        },
          node('span', { class: 'blueprint-wizard-select-card-icon' }, appIcon(provider.type === 'proxmox' ? 'server' : 'box')),
          node('span', { class: 'blueprint-wizard-select-card-copy' },
            node('strong', { text: provider.name }),
            node('small', { text: CREDENTIAL_TYPE_CONFIG[provider.type]?.label || provider.type }),
            node('small', { text: credential ? 'Credentials: ' + credential.name : 'Brak przypisanych credentials' })),
          active ? node('span', { class: 'blueprint-wizard-card-check' }, appIcon('check')) : null);
        card.addEventListener('click', async () => {
          state.providerId = String(provider.id);
          state.providerType = provider.type;
          await discoverProvider(provider.id);
          render();
        });
        return card;
      }

      function renderPlatform() {
        const providerGrid = node('div', { class: 'blueprint-wizard-card-grid' },
          ...data.providers.map(providerCard));
        const wrapper = node('div', { class: 'blueprint-wizard-step-stack' },
          node('div', { class: 'blueprint-wizard-section-heading' },
            node('strong', { text: 'Platforma' }),
            node('span', { class: 'muted', text: 'Credentials przypisane do providera są wybierane automatycznie.' })),
          providerGrid);

        const provider = data.providers.find(value => String(value.id) === String(state.providerId));
        if (!provider) return wrapper;

        const verifiedConnection = provider.type === 'proxmox' && state.providerConnected;
        wrapper.append(node('div', { class: 'blueprint-wizard-connection-status' },
          node('span', { class: 'status-dot ' + (verifiedConnection ? 'ok' : (state.providerError ? 'bad' : '')) }),
          node('strong', { text: verifiedConnection
            ? 'Połączenie z providerem działa'
            : state.providerError ? 'Nie udało się odczytać providera' : 'Provider ma przypisane credentials' }),
          state.providerError ? node('small', { text: state.providerError }) : null));

        if (provider.type === 'proxmox') {
          const nodeField = selectField('Docelowy node', 'node', state.nodes.map(value => ({
            value: value.node,
            label: value.node + (value.status ? ' · ' + statusLabel(value.status) : ''),
          })), state.node, { required: true, placeholder: 'Wybierz node' });
          nodeField.querySelector('select').addEventListener('change', async event => {
            state.node = event.currentTarget.value;
            await loadNodeResources();
            render();
          });

          const templates = node('div', { class: 'blueprint-wizard-card-grid' });
          state.templates.forEach(template => {
            const active = String(template.vmid) === String(state.selectedTemplateVmid)
              && String(template.node || '') === String(state.selectedTemplateNode || '');
            const facts = [];
            if (template.status) facts.push(statusLabel(template.status));
            if (Number(template.maxcpu)) facts.push(template.maxcpu + ' vCPU');
            if (Number(template.maxmem)) facts.push(formatBytes(template.maxmem) + ' RAM');
            const card = node('button', {
              type: 'button',
              class: 'blueprint-wizard-select-card' + (active ? ' selected' : ''),
              'aria-pressed': String(active),
            },
              node('span', { class: 'blueprint-wizard-select-card-icon' }, appIcon('box')),
              node('span', { class: 'blueprint-wizard-select-card-copy' },
                node('strong', { text: template.name || ('Template ' + template.vmid) }),
                node('span', { class: 'blueprint-wizard-card-meta' },
                  badge('VMID ' + template.vmid, 'info'),
                  badge(template.node || '—')),
                facts.length ? node('small', { text: facts.join(' · ') }) : null),
              active ? node('span', { class: 'blueprint-wizard-card-check' }, appIcon('check')) : null);
            card.addEventListener('click', () => {
              state.selectedTemplateVmid = String(template.vmid);
              state.selectedTemplateNode = template.node || '';
              state.selectedTemplateName = template.name || ('Template ' + template.vmid);
              render();
            });
            templates.append(card);
          });
          wrapper.append(
            node('div', { class: 'blueprint-wizard-section-heading' },
              node('strong', { text: 'Node docelowy' })),
            nodeField,
            node('div', { class: 'blueprint-wizard-section-heading' },
              node('strong', { text: 'Template / VM bazowa' }),
              node('span', { class: 'muted', text: 'Wybierz obraz klikając kartę. VMID i node źródłowy zostaną zapisane automatycznie.' })),
            templates
          );
        } else {
          const matches = data.templates.filter(value => value.provider === provider.type);
          const templateGrid = node('div', { class: 'blueprint-wizard-card-grid' });
          matches.forEach(template => {
            const active = template.id === state.terraformTemplateId;
            const card = node('button', {
              type: 'button',
              class: 'blueprint-wizard-select-card' + (active ? ' selected' : ''),
              'aria-pressed': String(active),
            },
              node('span', { class: 'blueprint-wizard-select-card-icon' }, appIcon('box')),
              node('span', { class: 'blueprint-wizard-select-card-copy' },
                node('strong', { text: template.name }),
                node('small', { text: template.id + ' · v' + template.version })),
              active ? node('span', { class: 'blueprint-wizard-card-check' }, appIcon('check')) : null);
            card.addEventListener('click', () => {
              state.terraformTemplateId = template.id;
              state.genericVariables = parts.core.defaultGenericVariables(template);
              render();
            });
            templateGrid.append(card);
          });
          wrapper.append(
            node('div', { class: 'blueprint-wizard-section-heading' },
              node('strong', { text: 'Szablon IaC' }),
              node('span', { class: 'muted', text: 'Wybierz zatwierdzony szablon dla tej platformy.' })),
            templateGrid);
        }
        return wrapper;
      }

      function presetButton(key, title, cpu, memory, disk) {
        const active = state.preset === key;
        const buttonEl = node('button', {
          type: 'button',
          class: 'blueprint-wizard-preset' + (active ? ' selected' : ''),
        },
          node('strong', { text: title }),
          node('span', { text: cpu + ' CPU · ' + (memory / 1024) + ' GB RAM · ' + disk + ' GB' }));
        buttonEl.addEventListener('click', () => {
          state.preset = key;
          state.cpu = cpu;
          state.memory = memory;
          state.disk = disk;
          render();
        });
        return buttonEl;
      }

      function renderVm() {
        if (state.providerType !== 'proxmox') {
          const template = data.templates.find(value => value.id === state.terraformTemplateId);
          const properties = template?.variables_schema?.properties || {};
          const required = parts.core.requiredTemplateVariables(template);
          const grid = node('div', { class: 'form-grid' });
          for (const [name, spec] of Object.entries(properties)) {
            if (name === 'name') continue;
            const type = parts.core.schemaType(spec);
            const value = state.genericVariables[name] ?? spec.default ?? '';
            let wrapper;
            if (spec.enum) {
              wrapper = selectField(FIELD_LABELS[name] || spec.title || name, 'generic_' + name,
                spec.enum.map(value => ({ value, label: String(value) })), value,
                { required: required.has(name) });
            } else {
              wrapper = field(FIELD_LABELS[name] || spec.title || name, 'generic_' + name, {
                type: ['integer', 'number'].includes(type) ? 'number' : 'text',
                value: Array.isArray(value) ? value.join(', ') : value,
                required: required.has(name),
                wide: type === 'array',
              });
            }
            const input = wrapper.querySelector('input,select,textarea');
            input.dataset.genericVariable = name;
            input.addEventListener('input', () => {
              state.genericVariables[name] = parts.core.coerceSchemaValue(spec, input.value);
            });
            grid.append(wrapper);
          }
          return node('div', { class: 'blueprint-wizard-step-stack' },
            node('div', { class: 'blueprint-wizard-info' },
              node('strong', { text: 'Parametry są generowane z zatwierdzonego schematu ' + (template?.name || '') + '.' })),
            grid);
        }

        const storageChoices = state.storages.map(value => ({
          value: value.storage || value.id,
          label: (value.storage || value.id) + (value.type ? ' [' + value.type + ']' : '')
            + (Number.isFinite(Number(value.avail)) ? ' · wolne ' + formatBytes(value.avail) : ''),
        }));
        const networkChoices = state.networks.map(value => ({
          value: value.iface,
          label: value.iface + (value.type ? ' [' + value.type + ']' : ''),
        }));
        const fields = node('div', { class: 'form-grid' },
          field('CPU', 'cpu', { type: 'number', min: 1, max: 128, value: state.cpu, required: true }),
          field('RAM (MiB)', 'memory', { type: 'number', min: 512, max: 1048576, value: state.memory, required: true }),
          field('Dysk (GiB)', 'disk', { type: 'number', min: 1, max: 65536, value: state.disk, required: true }),
          field('VLAN ID (opcjonalnie)', 'vlan_id', { type: 'number', min: 1, max: 4094, value: state.vlanId }),
          selectField('Storage', 'storage', storageChoices, state.storage, { required: true, placeholder: 'Wybierz storage' }),
          selectField('Network / bridge', 'network', networkChoices, state.network, { required: true, placeholder: 'Wybierz sieć' }),
          !state.selectEnvironmentOnExecute
            ? selectField('Environment', 'environment',
                ['test', 'dev', 'nonprod', 'prod']
                  .filter(name => data.vmClassification?.environments?.[name] !== false)
                  .map(name => ({ value: name, label: name.toUpperCase() })),
                state.environment, { required: true, placeholder: 'Brak włączonych Environment' })
            : null,
          !state.selectApmidOnExecute
            ? ((data.vmClassification?.apmids || []).length
                ? selectField('APMID', 'apmid',
                    data.vmClassification.apmids.map(value => ({ value, label: value })),
                    state.apmid, { required: true, placeholder: 'Wybierz APMID' })
                : field('APMID', 'apmid', {
                    value: state.apmid,
                    required: true,
                    placeholder: 'IAASTEAM',
                    help: 'Brak zapisanych APMID w Ustawieniach — możesz podać wartość ręcznie.',
                  }))
            : null
        );

        const guestCredentialChoices = window.BlueprintProvisioningGuards.guestCredentialChoices(
          data.credentials || []
        );
        const guestCredentialField = selectField(
          'Credential ustawiany na VM',
          'guest_credential_id',
          [
            { value: '', label: 'Nie ustawiaj credentiala przez cloud-init' },
            ...guestCredentialChoices,
          ],
          state.guestCredentialId,
          {
            wide: true,
            help: guestCredentialChoices.length
              ? 'Cloud-init ustawi użytkownika z wybranego credentiala. Credential może używać hasła, klucza SSH albo obu. Klucz prywatny nigdy nie jest kopiowany do VM; przy logowaniu kluczem dodawany jest wyłącznie odpowiadający mu klucz publiczny.'
              : 'Brak credentiali SSH z hasłem lub kluczem prywatnym dostępnych do ustawienia konta w VM.',
          }
        );
        guestCredentialField.querySelector('select').addEventListener('change', event => {
          state.guestCredentialId = event.currentTarget.value;
        });

        const runtimeEnvironment = checkboxField(
          'Wybieraj Environment podczas tworzenia VM',
          'select_environment_on_execute',
          state.selectEnvironmentOnExecute
        );
        const runtimeApmid = checkboxField(
          'Wybieraj APMID podczas tworzenia VM',
          'select_apmid_on_execute',
          state.selectApmidOnExecute
        );
        const runtimeClassification = node('div', { class: 'blueprint-wizard-inline-panel wide' },
          node('div', { class: 'blueprint-wizard-section-heading wide' },
            node('strong', { text: 'Parametry wybierane przy użyciu Blueprintu' }),
            node('span', { class: 'muted', text: 'Włącz pola, które użytkownik ma wybrać dopiero podczas tworzenia VM z gotowego Blueprintu.' })),
          runtimeEnvironment,
          runtimeApmid
        );
        [runtimeEnvironment, runtimeApmid].forEach(wrapper => {
          wrapper.querySelector('input').addEventListener('change', event => {
            saveStateFromInput(event.currentTarget);
            if (event.currentTarget.name === 'select_environment_on_execute') {
              if (state.selectEnvironmentOnExecute) {
                state.environment = '';
                delete state.hostnameValues.env;
                delete state.hostnameValues.environment;
              } else {
                state.environment = ['test', 'dev', 'nonprod', 'prod']
                  .find(name => data.vmClassification?.environments?.[name] !== false) || '';
                if (state.environment) {
                  state.hostnameValues.env = state.environment;
                  state.hostnameValues.environment = state.environment;
                }
              }
            }
            if (event.currentTarget.name === 'select_apmid_on_execute') {
              state.apmid = state.selectApmidOnExecute
                ? ''
                : String(data.vmClassification?.apmids?.[0] || '');
            }
            render();
          });
        });

        fields.querySelectorAll('input,select').forEach(control => {
          control.addEventListener('input', () => {
            saveStateFromInput(control);
            if (['cpu', 'memory', 'disk'].includes(control.name)) state.preset = 'custom';
          });
          control.addEventListener('change', () => {
            saveStateFromInput(control);
            if (control.name === 'environment') {
              state.environment = control.value;
              state.hostnameValues.env = control.value;
              state.hostnameValues.environment = control.value;
            }
            if (control.name === 'apmid') state.apmid = String(control.value || '').trim().toUpperCase();
            if (['environment', 'apmid'].includes(control.name)) render();
          });
        });

        const sshCredentialUsers = [];
        const seenSshCredentialUsers = new Set();
        (data.credentials || []).forEach(value => {
          if (value.type !== 'ssh') return;
          const username = String(value.username || '').trim();
          if (!username || seenSshCredentialUsers.has(username)) return;
          seenSshCredentialUsers.add(username);
          sshCredentialUsers.push({
            username,
            credentialName: value.name || ('Credential #' + value.id),
          });
        });
        const sshUsernameField = field('Użytkownik SSH', 'ssh_username', {
          value: state.sshUsername || 'clouduser',
          help: sshCredentialUsers.length
            ? 'Możesz wpisać login ręcznie albo wybrać użytkownika z zapisanych Credentiali.'
            : 'Brak użytkowników SSH w zapisanych Credentialach — wpisz login ręcznie.',
        });
        const sshUsernameInput = sshUsernameField.querySelector('input');
        if (sshUsernameInput && sshCredentialUsers.length) {
          const sshUsersListId = 'blueprint-wizard-ssh-credential-users';
          sshUsernameInput.setAttribute('list', sshUsersListId);
          sshUsernameInput.setAttribute('autocomplete', 'off');
          sshUsernameField.append(node('datalist', { id: sshUsersListId },
            ...sshCredentialUsers.map(value => node('option', {
              value: value.username,
              label: value.credentialName,
            }))));
        }

        const advanced = node('details', { class: 'advanced-options' },
          node('summary', { text: 'Zaawansowane parametry VM' }),
          node('div', { class: 'advanced-options-body form-grid' },
            field('Tagi Proxmox', 'tags', {
              value: state.tags, wide: true, placeholder: 'linux, production, web',
            }),
            sshUsernameField,
            field('Klucz publiczny SSH', 'ssh_public_key', {
              tag: 'textarea', value: state.sshPublicKey, wide: true,
            })));
        advanced.querySelectorAll('input,textarea').forEach(control => control.addEventListener('input', () => saveStateFromInput(control)));

        const qemuAgentInfo = node('div', { class: 'blueprint-wizard-info' },
          node('strong', { text: 'QEMU Guest Agent' }),
          node('span', { text: state.installQemuGuestAgent
            ? (parts.cloudInit.enabled(state) ? 'Automatyczna instalacja z NoCloud ISO przez API Proxmoxa' : 'Starszy tryb instalacji agenta; snippet storage: ' + (state.cloudInitSnippetStorage || 'brak'))
              + '. Oczekiwanie w workflow: ' + (state.waitAgent ? 'włączone.' : 'wyłączone.')
            : (state.waitAgent ? 'Automatyczna instalacja jest wyłączona. Workflow będzie czekać na QEMU Guest Agent już obecny w obrazie/template.'
              : 'Automatyczna instalacja i oczekiwanie na QEMU Guest Agent są wyłączone.') }));

        const classificationPreview = node('div', { class: 'blueprint-wizard-info' },
          node('strong', { text: 'Klasyfikacja VM' }),
          node('span', { text: [
            state.selectApmidOnExecute ? 'APMID: wybierany przy tworzeniu VM' : 'APMID: ' + (state.apmid || 'nieustawiony'),
            state.selectEnvironmentOnExecute ? 'Environment: wybierany przy tworzeniu VM' : 'Environment: ' + (state.environment ? state.environment.toUpperCase() : 'nieustawiony'),
          ].join(' · ') }));

        return node('div', { class: 'blueprint-wizard-step-stack' },
          node('div', { class: 'blueprint-wizard-presets' },
            presetButton('small', 'Mała', 1, 2048, 20),
            presetButton('standard', 'Standardowa', 2, 4096, 40),
            presetButton('large', 'Duża', 4, 8192, 80),
            presetButton('custom', 'Własna', state.cpu, state.memory, state.disk)),
          fields,
          guestCredentialField,
          runtimeClassification,
          qemuAgentInfo,
          classificationPreview,
          advanced);
      }

      function renderHostname() {
        const content = parts.hostname.renderSchemeCards({
          state, data, rerender: render, saveStateFromInput,
        });
        const toggle = content.querySelector('[name="hostname_enabled"]');
        if (toggle) toggle.addEventListener('change', () => {
          state.hostnameEnabled = toggle.checked;
          render();
        });
        return content;
      }

      function renderSystemConfiguration() {
        const off = node('button', {
          type: 'button',
          class: 'blueprint-wizard-select-card' + (!state.ansibleEnabled ? ' selected' : ''),
        },
          node('span', { class: 'blueprint-wizard-select-card-icon' }, appIcon('check')),
          node('span', { class: 'blueprint-wizard-select-card-copy' },
            node('strong', { text: 'Bez dodatkowej konfiguracji' }),
            node('small', { text: 'Blueprint kończy się po utworzeniu i przygotowaniu VM.' })));
        const on = node('button', {
          type: 'button',
          class: 'blueprint-wizard-select-card' + (state.ansibleEnabled ? ' selected' : ''),
        },
          node('span', { class: 'blueprint-wizard-select-card-icon' }, appIcon('workflow')),
          node('span', { class: 'blueprint-wizard-select-card-copy' },
            node('strong', { text: 'Uruchom Ansible po utworzeniu VM' }),
            node('small', { text: 'Po QEMU Agent uruchomi zatwierdzony playbook.' })));
        off.addEventListener('click', () => { state.ansibleEnabled = false; render(); });
        on.addEventListener('click', () => { state.ansibleEnabled = true; render(); });

        const content = node('div', { class: 'blueprint-wizard-step-stack' },
          node('div', { class: 'blueprint-wizard-card-grid' }, off, on));
        if (!state.ansibleEnabled) return content;

        const playbookGrid = node('div', { class: 'blueprint-wizard-card-grid' });
        data.playbooks.forEach(playbook => {
          const active = playbook.id === state.playbookId;
          const card = node('button', {
            type: 'button',
            class: 'blueprint-wizard-select-card' + (active ? ' selected' : ''),
          },
            node('span', { class: 'blueprint-wizard-select-card-icon' }, appIcon('file-text')),
            node('span', { class: 'blueprint-wizard-select-card-copy' },
              node('strong', { text: playbook.name }),
              node('small', { text: (playbook.transport || 'ssh').toUpperCase() + ' · v' + playbook.version }),
              playbook.description ? node('small', { text: playbook.description }) : null),
            active ? node('span', { class: 'blueprint-wizard-card-check' }, appIcon('check')) : null);
          card.addEventListener('click', () => {
            state.playbookId = playbook.id;
            const matching = data.credentials.filter(value => value.type === playbook.transport);
            if (!matching.some(value => String(value.id) === String(state.ansibleCredentialId))) {
              state.ansibleCredentialId = String(matching[0]?.id || '');
            }
            render();
          });
          playbookGrid.append(card);
        });
        content.append(node('div', { class: 'blueprint-wizard-section-heading' },
          node('strong', { text: 'Playbook' })), playbookGrid);

        const playbook = data.playbooks.find(value => value.id === state.playbookId);
        if (playbook) {
          const matching = data.credentials.filter(value => value.type === playbook.transport);
          const credentialField = selectField('Credentials ' + playbook.transport.toUpperCase(), 'ansible_credentials_id',
            matching.map(value => ({ value: value.id, label: value.name + ' (#' + value.id + ')' })),
            state.ansibleCredentialId, { required: true, placeholder: 'Brak zgodnych credentials' });
          credentialField.querySelector('select').addEventListener('change', event => { state.ansibleCredentialId = event.currentTarget.value; });
          content.append(credentialField);

          const required = playbook.required_variables || [];
          if (required.length) {
            const variables = node('div', { class: 'form-grid blueprint-wizard-inline-panel' });
            required.forEach(name => {
              if (name === 'hostname' && playbook.id === 'bootstrap-linux' && state.hostnameEnabled) {
                variables.append(node('div', { class: 'blueprint-wizard-info wide' },
                  node('strong', { text: 'hostname' }),
                  node('span', { text: 'Automatycznie: {{ hostname }}' })));
                return;
              }
              const wrapper = field(name, 'ansible_' + name, {
                value: state.ansibleVariables[name] || '', required: true,
              });
              const input = wrapper.querySelector('input,textarea');
              input.dataset.ansibleVariable = name;
              input.addEventListener('input', () => { state.ansibleVariables[name] = input.value.trim(); });
              variables.append(wrapper);
            });
            content.append(node('div', { class: 'blueprint-wizard-section-heading' },
              node('strong', { text: 'Wymagane zmienne playbooka' })), variables);
          }
        }
        return content;
      }

      function currentAutoWorkflow() {
        return parts.core.workflow({
          hostname: state.hostnameEnabled,
          ipam: state.ipMode === 'ipam',
          tags: Boolean(
            String(state.tags || '').trim()
            || state.apmid
            || state.environment
            || state.selectApmidOnExecute
            || state.selectEnvironmentOnExecute
          ),
          cloudInit: state.providerType === 'proxmox' && state.cloudInitEnabled !== false,
          waitAgent: state.providerType === 'proxmox' && state.waitAgent,
          ansible: state.ansibleEnabled,
        });
      }

      function workflowEditorRow(step, index) {
        const proxmoxOnly = new Set(['cloud_init', 'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh',
          'run_ansible_playbook', 'create_snapshot', 'health_check']);
        const availableTypes = state.providerType === 'proxmox'
          ? parts.core.WORKFLOW_TYPES
          : parts.core.WORKFLOW_TYPES.filter(value => !proxmoxOnly.has(value));
        const row = node('div', { class: 'editor-card blueprint-wizard-workflow-editor-row', 'data-workflow-editor-row': String(index) },
          node('div', { class: 'editor-card-header' },
            node('strong', { text: 'Krok ' + (index + 1) }),
            button('Usuń', () => {
              state.workflow.splice(index, 1);
              render();
            }, 'danger')),
          node('div', { class: 'form-grid' },
            field('ID', 'workflow_id', { value: step.id, required: true }),
            selectField('Typ', 'workflow_type',
              (availableTypes.includes(step.type) ? availableTypes : [step.type, ...availableTypes])
                .map(value => ({
                  value,
                  label: availableTypes.includes(value)
                    ? parts.core.workflowLabel(value)
                    : 'Legacy / niedostępne dla ' + state.providerType + ': ' + parts.core.workflowLabel(value),
                })),
              step.type, { required: true }),
            field('Zależy od (ID, po przecinku)', 'workflow_depends', { value: (step.depends_on || []).join(', ') }),
            field('Retry', 'workflow_retry', { type: 'number', min: 0, max: 10, value: step.retry ?? 0 }),
            field('Timeout (s)', 'workflow_timeout', { type: 'number', min: 1, max: 86400, value: step.timeout ?? 600 }),
            field('Rollback (ID kroku)', 'workflow_rollback', { value: step.rollback || '' }),
            field('Conditions (JSON)', 'workflow_conditions', {
              tag: 'textarea', wide: true, value: JSON.stringify(step.conditions || {}, null, 2),
            })));
        return row;
      }

      function renderWorkflow() {
        const auto = currentAutoWorkflow();
        if (!state.advancedWorkflow) state.workflow = auto;
        const toggle = checkboxField('Tryb zaawansowany', 'advanced_workflow', state.advancedWorkflow);
        toggle.querySelector('input').addEventListener('change', event => {
          captureCurrentStep();
          state.advancedWorkflow = event.currentTarget.checked;
          if (state.advancedWorkflow) state.workflow = auto.map(value => ({ ...value, depends_on: [...value.depends_on] }));
          render();
        });
        const isProxmox = state.providerType === 'proxmox';
        const snippetAvailable = Boolean(state.cloudInitSnippetStorage);
        const sshReady = state.qemuAgentSshReady !== false;
        const install = checkboxField('Instaluj QEMU Guest Agent automatycznie', 'install_qemu_guest_agent', state.installQemuGuestAgent);
        const installControl = install.querySelector('input');
        installControl.disabled = !isProxmox;
        installControl.addEventListener('change', event => { const checked = event.currentTarget.checked; captureCurrentStep(); state.installQemuGuestAgent = checked; render(); });
        const wait = checkboxField('Czekaj na QEMU Guest Agent po Terraform apply', 'wait_agent', state.waitAgent);
        const waitControl = wait.querySelector('input');
        waitControl.disabled = !isProxmox;
        waitControl.addEventListener('change', event => {
          const checked = event.currentTarget.checked;
          captureCurrentStep();
          state.waitAgent = checked;
          if (!state.advancedWorkflow) state.workflow = currentAutoWorkflow();
          render();
        });
        const content = node('div', { class: 'blueprint-wizard-step-stack' },
          node('div', { class: 'blueprint-wizard-info' },
            node('strong', { text: state.advancedWorkflow ? 'Workflow edytowany ręcznie' : 'Workflow budowany automatycznie' }),
            node('span', { text: state.advancedWorkflow
              ? 'Możesz zmieniać kroki runtime, zależności, retry, timeout, rollback i conditions.'
              : 'Hostname, IPAM, cloud-init i tagi są przygotowywane przed runtime; workflow pokazuje tylko faktycznie wykonywane operacje.' })),
          !parts.cloudInit.enabled(state) && isProxmox && state.installQemuGuestAgent && (state.guestCredentialId || !snippetAvailable || !sshReady) ? node('div', { class: 'callout info' },
            node('strong', { text: 'QEMU Guest Agent zostanie zainstalowany przez konto bootstrapowe VM' }),
            node('p', { text: (state.guestCredentialId
              ? 'Wybrano Credential VM, więc workflow celowo pomija upload snippets i SSH do noda PVE.'
              : 'Brak gotowego uploadu snippetów/SSH do noda PVE (' + (state.qemuAgentSshReason || (snippetAvailable ? 'ssh_not_ready' : 'snippets_unavailable')) + ').')
              + ' Workflow utworzy jednorazowe konto przez natywny cloud-init, zainstaluje agenta w VM, utworzy konto docelowe z Credentiala i usunie konto tymczasowe. Przy DHCP template musi już udostępniać adres przez Guest Agent albo Blueprint powinien używać statycznego IP/IPAM.' })) : null,
          parts.cloudInit.render({ state, data, rerender: render, capture: captureCurrentStep }),
          isProxmox ? install : null,
          isProxmox ? wait : null,
          toggle,
          parts.ui.workflowVisual(state.advancedWorkflow ? state.workflow : auto));

        if (state.advancedWorkflow) {
          const editor = node('div', { class: 'editor-list' },
            ...state.workflow.map(workflowEditorRow));
          editor.append(node('div', { class: 'editor-add-row' },
            button('Dodaj krok', () => {
              captureCurrentStep();
              const index = state.workflow.length + 1;
              state.workflow.push({
                id: 'step_' + index,
                type: state.providerType === 'proxmox' ? 'health_check' : 'condition',
                depends_on: state.workflow.length ? [state.workflow.at(-1).id] : [],
                retry: 0,
                timeout: 600,
                rollback: null,
                conditions: {},
              });
              render();
            }, 'ghost')));
          content.append(node('div', { class: 'blueprint-wizard-section-heading' },
            node('strong', { text: 'Edytor workflow' })), editor);
        }
        return content;
      }

      function renderAccess() {
        const content = node('div', { class: 'blueprint-wizard-step-stack' },
          node('div', { class: 'blueprint-wizard-info' },
            node('strong', { text: 'Opcjonalne ustawienia dostępu' }),
            node('span', { text: 'Domyślna konfiguracja udostępnia Blueprint w backendzie i API bez dodatkowych ograniczeń.' })),
          node('div', { class: 'form-grid' },
            checkboxField('Backend', 'visibility_backend', state.visibilityBackend),
            checkboxField('CloudPortal', 'visibility_cloudportal', state.visibilityCloudportal),
            checkboxField('API', 'visibility_api', state.visibilityApi),
            checkboxField('Wymaga zatwierdzenia przed uruchomieniem', 'requires_approval', state.requiresApproval), ...window.BlueprintApprovalPolicyUI.wizardFields(state),
            selectField('Po błędzie wdrożenia', 'recovery_policy', [
              { value: 'preserve', label: 'Zachowaj zasoby do analizy' },
              { value: 'destroy_on_failure', label: 'Automatycznie usuń nieudane wdrożenie' },
            ], state.recoveryPolicy, { wide: true }))
        );
        if (allowed('roles.read')) {
          content.append(
            dualListGroup('Dozwolone role', 'allowed_role_ids', data.roles, state.allowedRoleIds,
              'Pusta lista „Wybrane” oznacza brak ograniczenia po roli.'),
            dualListGroup('Role zarządzające Blueprintem', 'manager_role_ids', data.managerRoles, state.managerRoleIds,
              'Rola zarządzająca musi mieć blueprints.read/update/delete i może być przypisana tylko do jednego Blueprintu.')
          );
        }
        if (allowed('users.read')) {
          content.append(dualListGroup('Dozwoleni użytkownicy', 'allowed_user_ids', data.users, state.allowedUserIds,
            'Pusta lista „Wybrane” oznacza brak ograniczenia po użytkowniku.'));
        }
        content.querySelectorAll('input,select').forEach(control => {
          control.addEventListener('change', () => saveStateFromInput(control));
        });
        return content;
      }

      function renderReview() {
        const provider = data.providers.find(value => String(value.id) === String(state.providerId));
        const scheme = data.schemes.find(value => String(value.id) === String(state.hostnameSchemeId));
        const pool = data.pools.find(value => String(value.id) === String(state.ipamPoolId));
        const playbook = data.playbooks.find(value => value.id === state.playbookId);
        const steps = state.advancedWorkflow ? state.workflow : currentAutoWorkflow();
        const roleNames = state.allowedRoleIds.map(id => data.roles.find(value => Number(value.id) === Number(id))?.name).filter(Boolean);
        const userNames = state.allowedUserIds.map(id => data.users.find(value => Number(value.id) === Number(id))?.username).filter(Boolean);

        const sections = [
          ['Blueprint', [
            ['Nazwa', state.name],
            ['Slug', state.slug],
            ['Tenant', blueprintScope.tenantLabel(state.tenantId)],
            ['Projekt', blueprintScope.projectLabel(state.projectId)],
            ['Opis', state.description || '—'],
          ]],
          ['Platforma', [
            ['Provider', provider?.name || '—'],
            ['Node', state.providerType === 'proxmox' ? state.node : '—'],
            ['Template', state.providerType === 'proxmox'
              ? ((state.selectedTemplateName || 'Template') + ' · VMID ' + state.selectedTemplateVmid)
              : (data.templates.find(value => value.id === state.terraformTemplateId)?.name || '—')],
          ]],
          ['VM', state.providerType === 'proxmox' ? [
            ['CPU', state.cpu],
            ['RAM', (Number(state.memory) / 1024) + ' GB'],
            ['Dysk', state.disk + ' GB'],
            ['Storage', state.storage],
            ['Network', state.network],
            ['Environment', state.selectEnvironmentOnExecute
              ? 'Wybierany podczas tworzenia VM'
              : (state.environment ? state.environment.toUpperCase() : '—')],
            ['Environment przy tworzeniu VM', state.selectEnvironmentOnExecute ? 'Wybierany przez użytkownika' : 'Stały z Blueprintu'],
            ['APMID', state.selectApmidOnExecute ? 'Wybierany podczas tworzenia VM' : (state.apmid || '—')],
            ['APMID przy tworzeniu VM', state.selectApmidOnExecute ? 'Wybierany przez użytkownika' : 'Stały z Blueprintu'],
            ['Cloud-init', parts.cloudInit.enabled(state) ? 'NoCloud ISO przez API; konfiguracja przy pierwszym starcie' : 'Starszy tryb'],
            ['Credential VM', state.guestCredentialId
              ? (data.credentials.find(value => String(value.id) === String(state.guestCredentialId))?.name || ('#' + state.guestCredentialId))
              : 'Brak'],
            ['QEMU Guest Agent', state.installQemuGuestAgent ? (state.waitAgent ? 'Instalacja przez cloud-init + oczekiwanie' : 'Instalacja przez cloud-init, bez oczekiwania') : (state.waitAgent ? 'Bez instalacji, oczekiwanie na agenta z template' : 'Wyłączony')],
            ['Klasyfikacja', state.selectApmidOnExecute || state.selectEnvironmentOnExecute
              ? 'Wyliczana podczas tworzenia VM'
              : (state.apmid && state.environment ? state.apmid + '.' + state.environment.toUpperCase() : '—')],
          ] : [
            ['Szablon IaC', state.terraformTemplateId],
            ['Parametry', Object.keys(state.genericVariables).length + ' ustawionych'],
          ]],
          ['Hostname', [
            ['Tryb', state.hostnameEnabled ? 'Automatyczny' : 'Stała nazwa'],
            ['Pattern', state.hostnameEnabled ? (scheme?.pattern || '—') : state.manualVmName],
            ['Podgląd', state.hostnameEnabled ? parts.core.hostnameExample(scheme, state.hostnameValues) : state.manualVmName],
          ]],
          ['Sieć', [
            ['Tryb', state.ipMode === 'dhcp' ? 'DHCP' : state.ipMode === 'ipam' ? 'IPAM' : 'Static'],
            ['Pula / adres', state.ipMode === 'ipam' ? (pool?.name || '—') : state.ipMode === 'static' ? state.ipv4Address : 'DHCP'],
          ]],
          ['Konfiguracja', [
            ['Ansible', state.ansibleEnabled ? (playbook?.name || '—') : 'Brak'],
          ]],
          ['Dostęp', [
            ['Role', roleNames.join(', ') || 'Bez ograniczenia'],
            ['Użytkownicy', userNames.join(', ') || 'Bez ograniczenia'],
            ['Approval', state.requiresApproval ? 'Wymagany' : 'Nie'],
            ...window.BlueprintApprovalPolicyUI.summaryRows(state),
          ]],
        ];

        const content = node('div', { class: 'blueprint-wizard-review' });
        sections.forEach(([title, rows]) => {
          content.append(node('section', { class: 'blueprint-wizard-review-section' },
            node('h4', { text: title }),
            node('div', { class: 'blueprint-wizard-review-grid' },
              ...rows.map(([label, value]) => parts.ui.summaryRow(label, value)))));
        });
        content.append(node('section', { class: 'blueprint-wizard-review-section' },
          node('h4', { text: 'Workflow' }),
          parts.ui.workflowVisual(steps)));
        return content;
      }

      function renderStepBody() {
        const descriptions = [
          'Nadaj Blueprintowi czytelną nazwę i opis. Szczegóły techniczne są ukryte.',
          'Wybierz provider, node i bazowy obraz VM. Credentials są pobierane z providera.',
          'Ustaw wielkość VM. Presety aktualizują CPU, RAM i dysk jednym kliknięciem.',
          'Wybierz sposób automatycznego nadawania nazw hostów.',
          'Wybierz DHCP, IPAM lub statyczny adres IP.',
          'Opcjonalnie uruchom zatwierdzony playbook Ansible po utworzeniu VM.',
          'Sprawdź automatycznie zbudowaną sekwencję operacji lub włącz tryb zaawansowany.',
          'Opcjonalnie ogranicz widoczność, uruchamianie i zarządzanie Blueprintem.',
          'Sprawdź całą konfigurację przed zapisaniem Blueprintu.',
        ];
        const content = [
          renderBasics,
          renderPlatform,
          renderVm,
          renderHostname,
          () => parts.network.renderNetworkStep({ state, data, rerender: render }),
          renderSystemConfiguration,
          renderWorkflow,
          renderAccess,
          renderReview,
        ][state.step]();

        return node('div', { class: 'blueprint-wizard-step' },
          stepHeading(STEPS[state.step][1], descriptions[state.step]),
          node('div', {
            class: 'modal-form-error blueprint-wizard-error-summary',
            'data-wizard-error-summary': 'true',
            hidden: !Object.keys(state.errors || {}).length,
          }),
          content);
      }

      function renderNavigation() {
        const nav = node('div', { class: 'blueprint-wizard-steps', role: 'tablist', 'aria-label': 'Kroki kreatora Blueprintu' });
        STEPS.forEach(([short], index) => {
          const status = index === state.step && Object.keys(state.errors || {}).length
            ? 'invalid'
            : index === state.step ? 'active' : index < state.step || index <= state.maxStep ? 'done' : 'pending';
          const buttonEl = node('button', {
            type: 'button',
            class: 'blueprint-wizard-step-tab ' + status,
            disabled: index > state.maxStep + 1,
            'aria-current': index === state.step ? 'step' : null,
          },
            node('span', { text: index < state.step || (index < state.maxStep && index !== state.step) ? '✓' : String(index + 1) }),
            node('strong', { text: short }));
          buttonEl.addEventListener('click', () => {
            if (index === state.step) return;
            captureCurrentStep();
            if (index > state.step && !validateStep(state.step)) {
              render();
              return;
            }
            state.step = index;
            state.maxStep = Math.max(state.maxStep, index);
            state.errors = {};
            render();
          });
          nav.append(buttonEl);
        });
        return nav;
      }

      async function submitBlueprint() {
        captureCurrentStep();
        for (let index = 0; index < STEPS.length - 1; index += 1) {
          if (!validateStep(index)) {
            state.step = index;
            render();
            return;
          }
        }
        let payload;
        try {
          payload = parts.core.buildPayload(state, data);
        } catch (error) {
          state.errors = { payload: error.message };
          render();
          return;
        }
        state.submitting = true;
        footerRoot.replaceChildren(button('Zapisywanie…', () => {}, 'primary'));
        const progress = node('div', { class: 'blueprint-wizard-submit-progress' },
          node('div', { class: 'spinner' }),
          node('strong', { text: 'Tworzenie Blueprintu' }),
          node('span', { text: 'Walidacja konfiguracji…' }));
        bodyRoot.replaceChildren(progress);
        try {
          progress.querySelector('span').textContent = 'Zapisywanie definicji i workflow…';
          const created = await api('/blueprints', {
            method: 'POST',
            body: payload,
            headers: parts.core.scopeHeaders(state),
          });
          progress.replaceChildren(
            node('span', { class: 'blueprint-wizard-success-icon' }, appIcon('check')),
            node('h3', { text: 'Blueprint został utworzony i jest gotowy do użycia.' }),
            node('p', { class: 'muted', text: created.name + ' · v' + created.version }),
            node('div', { class: 'blueprint-wizard-inline-actions' },
              button('Zamknij', () => navigate('blueprints'), 'primary'),
              allowed('blueprints.execute') ? button('Przejdź do Blueprintów', () => navigate('blueprints')) : null));
          footerRoot.replaceChildren();
          state.submitting = false;
        } catch (error) {
          state.submitting = false;
          state.errors = { submit: error.message };
          state.step = 8;
          render();
        }
      }

      function renderFooter() {
        const actions = [];
        if (state.step > 0) actions.push(button('Wstecz', () => {
          captureCurrentStep();
          state.step -= 1;
          state.errors = {};
          render();
        }, 'ghost'));
        if (state.step < STEPS.length - 1) {
          actions.push(button('Dalej', async () => {
            captureCurrentStep();
            if (!validateStep(state.step)) {
              render();
              return;
            }
            state.maxStep = Math.max(state.maxStep, state.step + 1);
            state.step += 1;
            state.errors = {};
            render();
          }, 'primary'));
        } else {
          actions.push(button('Utwórz Blueprint', submitBlueprint, 'primary'));
        }
        footerRoot.replaceChildren(...actions);
      }

      function render() {
        if (state.submitting) return;
        navRoot.replaceChildren(renderNavigation());
        bodyRoot.replaceChildren(renderStepBody());
        renderFooter();
        parts.ui.errorText(bodyRoot, state.errors);
        const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        bodyRoot.scrollTo?.({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
      }

      dom.modal.classList.add('modal-wide');
      dom.modalTitle.textContent = 'Nowy Blueprint';
      dom.modalEyebrow.textContent = 'Kreator krok po kroku';
      navRoot = node('div', { class: 'blueprint-wizard-nav-host' });
      bodyRoot = node('div', { class: 'blueprint-wizard-body' });
      const shell = node('div', { class: 'blueprint-wizard-shell' }, navRoot, bodyRoot);
      dom.modalBody.replaceChildren(shell);
      footerRoot = dom.modalActions;
      if (!dom.modal.open) dom.modal.showModal();

      blueprintScope = parts.scope.create({ state, data, options, allowed, safeApi, discoverProvider, render });
      try { await blueprintScope.loadResources(true); } catch (error) {
        state.providerId = '';
        state.errors = { project_id: error.message };
      }
      render();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  window.BlueprintWizard = { open: openBlueprintWizard };
  registerExtension('blueprint-wizard', () => {});
})();
