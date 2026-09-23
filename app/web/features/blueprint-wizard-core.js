'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  const AUTOMATIC_HOSTNAME_TOKENS = new Set(['number', 'random', 'year']);
  const HOSTNAME_LABELS = {
    location: 'Lokalizacja',
    environment: 'Środowisko',
    env: 'Środowisko',
    application: 'Aplikacja',
    service: 'Usługa',
    role: 'Rola serwera',
    os: 'System operacyjny',
    cluster: 'Klaster',
    site: 'Site',
  };
  const WORKFLOW_TYPES = [
    'cloud_init', 'terraform_plan', 'terraform_apply', 'wait_for_vm', 'wait_for_agent',
    'wait_for_ip', 'wait_for_ssh', 'run_ansible_playbook', 'create_snapshot',
    'health_check', 'condition', 'approval', 'delay', 'notification',
    'terraform_destroy',
  ];

  function slugify(value) {
    return String(value || '')
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9_.-]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .replace(/-{2,}/g, '-')
      .slice(0, 63);
  }

  function hostnameTokens(pattern = '') {
    return [...new Set([...String(pattern).matchAll(/{([a-z]+)}/g)].map(match => match[1]))]
      .filter(token => !AUTOMATIC_HOSTNAME_TOKENS.has(token));
  }

  function hostnameExample(scheme, values = {}) {
    if (!scheme) return '—';
    const padding = Number(scheme.padding || 3);
    const next = Number(scheme.next_number || 1);
    const replacements = {
      ...values,
      number: String(next).padStart(padding, '0'),
      random: 'a7k2',
      year: String(new Date().getFullYear()),
    };
    let output = String(scheme.pattern || '');
    for (const token of [...new Set([...output.matchAll(/{([a-z]+)}/g)].map(match => match[1]))]) {
      const fallback = {
        location: 'wro', env: 'prod', environment: 'prod', role: 'web',
        application: 'app', service: 'svc', os: 'linux', cluster: 'cl1', site: 'dc1',
      }[token] || token;
      output = output.replaceAll('{' + token + '}', String(replacements[token] || fallback));
    }
    return output.toLowerCase();
  }

  function workflow(options = {}) {
    const steps = [];
    let previous = [];
    const add = (id, type) => {
      const timeout = ['terraform_plan', 'terraform_apply', 'terraform_destroy'].includes(type) ? 3600 : 600;
      steps.push({ id, type, depends_on: [...previous], conditions: {}, retry: 0, timeout, rollback: null });
      previous = [id];
    };
    if (options.cloudInit) add('cloud_init', 'cloud_init');
    add('apply', 'terraform_apply');
    if (options.waitAgent) add('agent', 'wait_for_agent');
    if (options.waitAgent || options.ansible) add('guest_ip', 'wait_for_ip');
    if (options.ansible) add('ansible', 'run_ansible_playbook');
    return steps;
  }

  function workflowLabel(type) {
    return ({
      generate_hostname: 'Hostname',
      allocate_ip: 'IPAM',
      create_vm: 'Utwórz VM',
      clone_vm: 'Clone VM',
      configure_vm: 'Konfiguracja VM',
      cloud_init: 'Cloud-init',
      start_vm: 'Start VM',
      wait_for_vm: 'Czekaj na VM',
      wait_for_agent: 'Wait Agent',
      wait_for_ip: 'Czekaj na IP',
      wait_for_ssh: 'Czekaj na SSH',
      set_hostname: 'Ustaw hostname',
      run_ansible_playbook: 'Ansible',
      terraform_plan: 'Terraform Plan',
      terraform_apply: 'Terraform Apply',
      terraform_destroy: 'Terraform Destroy (rollback)',
      create_snapshot: 'Snapshot',
      set_tags: 'Tagi',
      health_check: 'Health check',
      condition: 'Warunek',
      approval: 'Akceptacja',
      delay: 'Opóźnienie',
      notification: 'Powiadomienie',
    }[type] || type);
  }

  function schemaType(spec = {}) {
    if (Array.isArray(spec.type)) return spec.type.find(value => value !== 'null') || 'string';
    if (spec.type) return spec.type;
    if (spec.anyOf) return schemaType(spec.anyOf.find(value => value.type !== 'null') || spec.anyOf[0] || {});
    return 'string';
  }

  function coerceSchemaValue(spec, raw) {
    const type = schemaType(spec);
    if (type === 'integer') return Number.parseInt(raw, 10);
    if (type === 'number') return Number(raw);
    if (type === 'boolean') return Boolean(raw);
    if (type === 'array') return String(raw || '').split(/[,\n]+/).map(value => value.trim()).filter(Boolean);
    return String(raw ?? '');
  }

  function defaultGenericVariables(template) {
    const result = {};
    const properties = template?.variables_schema?.properties || {};
    for (const [name, spec] of Object.entries(properties)) {
      if (name === 'name') continue;
      if (spec.default !== undefined && spec.default !== null) result[name] = spec.default;
    }
    return result;
  }

  function stateDefaults() {
    return {
      step: 0,
      maxStep: 0,
      errors: {},
      name: '',
      slug: '',
      slugTouched: false,
      description: '',
      active: true,
      providerId: '',
      providerType: '',
      providerCredentialId: '',
      providerConnected: false,
      providerError: '',
      terraformTemplateId: '',
      node: '',
      templates: [],
      nodes: [],
      storages: [],
      snippetStorages: [],
      cloudInitSnippetStorage: 'local',
      qemuAgentSshReady: true,
      qemuAgentSshReason: '',
      networks: [],
      selectedTemplateVmid: '',
      selectedTemplateNode: '',
      selectedTemplateName: '',
      cpu: 2,
      memory: 4096,
      disk: 40,
      storage: '',
      network: 'vmbr0',
      vlanId: '',
      preset: 'standard',
      environment: '',
      apmid: 'LEO',
      tenantId: '',
      projectId: '',
      selectEnvironmentOnExecute: false,
      selectApmidOnExecute: false,
      tags: '',
      sshUsername: 'clouduser',
      sshPublicKey: '',
      guestCredentialId: '',
      genericVariables: {},
      hostnameEnabled: true,
      hostnameSchemeId: '',
      hostnameValues: {},
      manualVmName: '',
      creatingScheme: false,
      newSchemeName: '',
      newSchemePattern: '{location}-{env}-{role}-{number}',
      newSchemeNext: 1,
      newSchemePadding: 3,
      ipMode: 'dhcp',
      ipamPoolId: '',
      ipv4Address: '',
      ipv4Gateway: '',
      dnsServers: '',
      dnsDomain: '',
      ansibleEnabled: false,
      playbookId: '',
      ansibleCredentialId: '',
      ansibleVariables: {},
      installQemuGuestAgent: true,
      waitAgent: true,
      advancedWorkflow: false,
      workflow: [],
      visibilityBackend: true,
      visibilityCloudportal: false,
      visibilityApi: true,
      allowedRoleIds: [],
      allowedUserIds: [],
      managerRoleIds: [],
      requiresApproval: false,
      autoApproveForExecutors: 'inherit',
      approvalTimeoutHours: '',
      recoveryPolicy: 'preserve',
      executor: 'terraform',
      submitting: false,
    };
  }

  function scopeHeaders(state) {
    if (!state?.tenantId || !state?.projectId) return {};
    return {
      'X-Tenant-ID': String(state.tenantId),
      'X-Project-ID': String(state.projectId),
    };
  }

  function requiredTemplateVariables(template) {
    return new Set(template?.variables_schema?.required || []);
  }

  function hostnameDefaultsForSelectedScheme(state, data) {
    if (!state.hostnameEnabled || !state.hostnameSchemeId) return {};
    const scheme = (data.schemes || []).find(value =>
      String(value.id) === String(state.hostnameSchemeId));
    if (!scheme) return {};

    const allowed = new Set(hostnameTokens(scheme.pattern));
    for (const token of ['number', 'random', 'year', 'location', 'role']) {
      allowed.delete(token);
    }

    return Object.fromEntries(
      Object.entries(state.hostnameValues || {})
        .filter(([name, value]) => allowed.has(name) && String(value ?? '').trim() !== '')
    );
  }

  function buildDeployment(state, data) {
    const provider = data.providers.find(value => String(value.id) === String(state.providerId));
    const template = data.templates.find(value => value.id === state.terraformTemplateId)
      || data.templates.find(value => value.provider === provider?.type);
    if (!provider || !template) throw new Error('Brak platformy lub szablonu IaC.');

    let variables;
    if (provider.type === 'proxmox') {
      const tags = String(state.tags || '').split(/[,\n]+/).map(value => value.trim().toLowerCase()).filter(Boolean);
      const fixedApmid = state.selectApmidOnExecute ? '' : String(state.apmid || '').trim();
      const fixedEnvironment = state.selectEnvironmentOnExecute ? '' : String(state.environment || '').trim();
      if (fixedApmid && fixedEnvironment) {
        const apmid = fixedApmid.toLowerCase();
        const environment = fixedEnvironment.toLowerCase();
        tags.push('apmid-' + apmid, 'env-' + environment, apmid + '.' + environment);
      }
      const uniqueTags = [...new Set(tags)];
      variables = {
        name: state.hostnameEnabled ? '{{ hostname }}' : state.manualVmName,
        node: state.node,
        template_id: Number(state.selectedTemplateVmid),
        template_node: state.selectedTemplateNode || state.node,
        cpu: Number(state.cpu),
        memory: Number(state.memory),
        disk: Number(state.disk),
        storage: state.storage,
        network: state.network,
        ssh_username: state.sshUsername || 'clouduser',
        install_qemu_guest_agent: Boolean(state.installQemuGuestAgent),
        cloud_init_snippet_storage: state.installQemuGuestAgent ? state.cloudInitSnippetStorage : null,
        tags: uniqueTags,
      };
      if (state.vlanId) variables.vlan_id = Number(state.vlanId);
      if (state.sshPublicKey) variables.ssh_public_key = state.sshPublicKey;
      if (state.ipMode === 'static') {
        variables.ipv4_address = state.ipv4Address;
        variables.ipv4_gateway = state.ipv4Gateway;
      }
      if (state.dnsServers) variables.dns_servers = String(state.dnsServers).split(/[,\n]+/).map(value => value.trim()).filter(Boolean);
      if (state.dnsDomain) variables.dns_domain = state.dnsDomain;
    } else {
      variables = { ...state.genericVariables };
      if (Object.prototype.hasOwnProperty.call(template.variables_schema?.properties || {}, 'name')) {
        variables.name = state.hostnameEnabled ? '{{ hostname }}' : state.manualVmName;
      }
    }

    const hostnameValues = hostnameDefaultsForSelectedScheme(state, data);
    if (state.selectEnvironmentOnExecute) {
      delete hostnameValues.env;
      delete hostnameValues.environment;
    }

    const deployment = {
      name: state.hostnameEnabled ? '{{ hostname }}' : state.manualVmName,
      provider_id: Number(state.providerId),
      credentials_id: Number(provider.credentials_id || state.providerCredentialId),
      template: template.id,
      variables,
      executor: state.executor,
      hostname_values: hostnameValues,
    };
    if (state.hostnameEnabled && state.hostnameSchemeId) deployment.hostname_scheme_id = Number(state.hostnameSchemeId);
    if (state.ipMode === 'ipam' && state.ipamPoolId) deployment.ipam_pool_id = Number(state.ipamPoolId);
    if (state.guestCredentialId) deployment.guest_credential_id = Number(state.guestCredentialId);
    if (!state.selectApmidOnExecute && state.apmid) deployment.apmid = String(state.apmid).trim().toUpperCase();
    if (!state.selectEnvironmentOnExecute && state.environment) deployment.environment = String(state.environment).trim().toLowerCase();
    deployment.select_apmid_on_execute = Boolean(state.selectApmidOnExecute);
    deployment.select_environment_on_execute = Boolean(state.selectEnvironmentOnExecute);

    if (state.ansibleEnabled && state.playbookId) {
      const selectedPlaybook = data.playbooks.find(value => value.id === state.playbookId);
      const ansibleVariables = {};
      for (const name of selectedPlaybook?.required_variables || []) {
        if (name === 'hostname' && selectedPlaybook.id === 'bootstrap-linux' && state.hostnameEnabled) {
          ansibleVariables.hostname = '{{ hostname }}';
        } else if (state.ansibleVariables[name]) {
          ansibleVariables[name] = state.ansibleVariables[name];
        }
      }
      deployment.ansible = {
        playbook: state.playbookId,
        credentials_id: Number(state.ansibleCredentialId),
        variables: ansibleVariables,
      };
    }
    return deployment;
  }

  function buildPayload(state, data) {
    const autoWorkflow = workflow({
      cloudInit: state.providerType === 'proxmox',
      hostname: state.hostnameEnabled,
      ipam: state.ipMode === 'ipam',
      tags: Boolean(String(state.tags || '').trim()
        || (!state.selectApmidOnExecute && !state.selectEnvironmentOnExecute && state.apmid && state.environment)),
      waitAgent: state.providerType === 'proxmox' && state.waitAgent,
      ansible: state.ansibleEnabled,
    });
    const selectedWorkflow = state.advancedWorkflow && state.workflow.length ? state.workflow : autoWorkflow;
    return {
      slug: state.slug,
      name: state.name,
      description: state.description,
      is_active: state.active,
      visibility: {
        backend: state.visibilityBackend,
        cloudportal: state.visibilityCloudportal,
        api: state.visibilityApi,
      },
      allowed_role_ids: state.allowedRoleIds.map(Number),
      allowed_user_ids: state.allowedUserIds.map(Number),
      manager_role_ids: state.managerRoleIds.map(Number),
      variables_schema: {},
      deployment: buildDeployment(state, data),
      workflow: selectedWorkflow,
      requires_approval: state.requiresApproval,
      auto_approve_for_executors: state.autoApproveForExecutors === 'inherit' ? null : state.autoApproveForExecutors === 'true',
      approval_timeout_hours: String(state.approvalTimeoutHours ?? '').trim() ? Number(state.approvalTimeoutHours) : null,
      recovery_policy: state.recoveryPolicy,
    };
  }

  function valueById(rows, id) {
    return rows.find(value => String(value.id) === String(id)) || null;
  }

  parts.core = {
    HOSTNAME_LABELS,
    WORKFLOW_TYPES,
    slugify,
    hostnameTokens,
    hostnameExample,
    workflow,
    workflowLabel,
    schemaType,
    coerceSchemaValue,
    defaultGenericVariables,
    scopeHeaders,
    requiredTemplateVariables,
    hostnameDefaultsForSelectedScheme,
    stateDefaults,
    buildDeployment,
    buildPayload,
    valueById,
  };

  registerExtension('blueprint-wizard-core', () => {});
})();
