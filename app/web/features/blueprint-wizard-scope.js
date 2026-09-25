'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function prepare(creationScopes, projectContext, state, options = {}) {
    const rows = Array.isArray(creationScopes) ? creationScopes : [];
    if (!rows.length) {
      throw new Error('Brak organizacji i projektu, w których masz uprawnienie blueprints.create.');
    }

    const tenantMap = new Map();
    const projectMap = new Map();
    rows.forEach(row => {
      const tenantId = String(row.tenant_id);
      const projectId = String(row.project_id);
      if (!tenantMap.has(tenantId)) {
        tenantMap.set(tenantId, {
          id: tenantId,
          name: row.tenant_name || tenantId,
          slug: row.tenant_slug || '',
          status: 'active',
        });
      }
      if (!projectMap.has(projectId)) {
        projectMap.set(projectId, {
          id: projectId,
          tenant_id: tenantId,
          name: row.project_name || projectId,
          slug: row.project_slug || '',
          status: 'active',
          permissions: Array.isArray(row.permissions) ? row.permissions : [],
        });
      }
    });

    const tenants = [...tenantMap.values()].sort((a, b) => a.name.localeCompare(b.name, 'pl'));
    const projects = [...projectMap.values()].sort((a, b) =>
      a.name.localeCompare(b.name, 'pl') || String(a.id).localeCompare(String(b.id)));

    const selectedProjectId = String(options.projectId || projectContext?.selected?.id || '');
    const selectedTenantId = String(options.tenantId || '');
    const project = projects.find(value =>
      String(value.id) === selectedProjectId
      && (!selectedTenantId || String(value.tenant_id) === selectedTenantId)
    ) || projects[0];
    state.tenantId = String(project.tenant_id);
    state.projectId = String(project.id);
    return { tenants, projects };
  }

  function create(context) {
    const { state, data, options, allowed, safeApi, discoverProvider, render } = context;
    const managerRequired = new Set(['blueprints.read', 'blueprints.update', 'blueprints.delete']);
    const defaultManagerRoleNames = new Set(['Administrator', 'Infrastructure Administrator']);

    function projectsForTenant(tenantId) {
      return data.projects.filter(value => String(value.tenant_id) === String(tenantId));
    }

    function tenantRowsForProjects() {
      const ids = [...new Set(data.projects.map(value => String(value.tenant_id)))];
      return ids.map(id => data.tenants.find(value => String(value.id) === id) || { id, name: id });
    }

    function tenantLabel(tenantId) {
      return tenantRowsForProjects().find(value => String(value.id) === String(tenantId))?.name
        || String(tenantId || '—');
    }

    function projectLabel(projectId) {
      return data.projects.find(value => String(value.id) === String(projectId))?.name
        || String(projectId || '—');
    }

    function scopeAllows(permission) {
      const project = data.projects.find(value => String(value.id) === String(state.projectId));
      return Boolean(allowed(permission) || project?.permissions?.includes(permission));
    }

    function refreshManagerRoles(resetSelection = false) {
      const dedicatedElsewhere = new Set(
        data.blueprints
          .filter(value => Number(value.id) !== Number(options.item?.id || 0))
          .flatMap(value => value.manager_role_ids || []).map(Number)
      );
      data.managerRoles = data.roles.filter(role =>
        !dedicatedElsewhere.has(Number(role.id))
        && [...managerRequired].every(permission => (role.permissions || []).includes(permission)));
      const available = new Set(data.managerRoles.map(role => Number(role.id)));
      state.managerRoleIds = resetSelection
        ? data.managerRoles.filter(role => defaultManagerRoleNames.has(role.name)).map(role => Number(role.id))
        : state.managerRoleIds.filter(id => available.has(Number(id)));
    }

    function resetDependentState() {
      const preferred = data.providers.find(value => value.type === 'proxmox') || data.providers[0];
      state.providerId = String(preferred.id);
      state.providerType = preferred.type;
      state.providerCredentialId = String(preferred.credentials_id || '');
      state.terraformTemplateId = data.templates.find(value => value.provider === preferred.type)?.id || '';
      state.node = '';
      state.templates = [];
      state.nodes = [];
      state.storages = [];
      state.snippetStorages = [];
      state.networks = [];
      state.selectedTemplateVmid = '';
      state.selectedTemplateNode = '';
      state.selectedTemplateName = '';
      state.pendingHostnameScheme = null;
      state.hostnameSchemeId = String(data.schemes[0]?.id || '');
      state.hostnameEnabled = Boolean(scopeAllows('hostnames.read') && data.schemes.length);
      state.ipamPoolId = data.pools.some(value => String(value.id) === String(state.ipamPoolId))
        ? state.ipamPoolId : '';
      state.guestCredentialId = data.credentials.some(value => String(value.id) === String(state.guestCredentialId))
        ? state.guestCredentialId : '';
      state.templateGuestCredentialId = data.credentials.some(value =>
        String(value.id) === String(state.templateGuestCredentialId))
        ? state.templateGuestCredentialId : '';
      state.ansibleCredentialId = data.credentials.some(value => String(value.id) === String(state.ansibleCredentialId))
        ? state.ansibleCredentialId : '';

      const enabledEnvironments = ['test', 'dev', 'nonprod', 'prod']
        .filter(name => data.vmClassification?.environments?.[name] !== false);
      state.environment = state.selectEnvironmentOnExecute ? '' : (enabledEnvironments[0] || '');
      state.apmid = state.selectApmidOnExecute ? '' : String(data.vmClassification?.apmids?.[0] || '');
      if (state.selectEnvironmentOnExecute) {
        delete state.hostnameValues.env;
        delete state.hostnameValues.environment;
      } else if (state.environment) {
        state.hostnameValues.env = state.environment;
        state.hostnameValues.environment = state.environment;
      }
      state.hostnameValues.location = String(data.vmClassification?.hostname_defaults?.location || 'wro');
      state.hostnameValues.role = String(data.vmClassification?.hostname_defaults?.role || 'server');

      if (options.hostnameSchemeId && data.schemes.some(value => String(value.id) === String(options.hostnameSchemeId))) {
        state.hostnameSchemeId = String(options.hostnameSchemeId);
        state.hostnameEnabled = true;
      }
    }

    async function loadResources(resetManagerSelection = false) {
      const requestOptions = { headers: parts.core.scopeHeaders(state) };
      const [providers, templates, schemes, pools, credentials, blueprints, playbooks] = await Promise.all([
        safeApi('/providers?limit=200', [], requestOptions),
        safeApi('/templates', [], requestOptions),
        scopeAllows('hostnames.read') ? safeApi('/hostname-schemes?limit=200', [], requestOptions) : Promise.resolve([]),
        scopeAllows('ipam.read') ? safeApi('/ipam/pools?limit=200', [], requestOptions) : Promise.resolve([]),
        safeApi('/credentials?limit=200', [], requestOptions),
        scopeAllows('blueprints.read') ? safeApi('/blueprints?limit=200', [], requestOptions) : Promise.resolve([]),
        scopeAllows('ansible.read') ? safeApi('/ansible/playbooks', [], requestOptions) : Promise.resolve([]),
      ]);

      data.providers = providers;
      data.templates = templates.filter(value =>
        value.enabled !== false || value.id === options.item?.deployment?.template);
      data.schemes = schemes.filter(value => value.is_active);
      data.pools = pools;
      data.credentials = credentials;
      data.blueprints = blueprints;
      data.playbooks = playbooks.filter(value => value.enabled !== false);
      if (!data.providers.length) throw new Error('Wybrany projekt nie ma dostępnej platformy infrastruktury.');
      if (!data.templates.length) throw new Error('Katalog nie zawiera szablonów Terraform/OpenTofu.');

      resetDependentState();
      refreshManagerRoles(resetManagerSelection);
      await discoverProvider(state.providerId);
    }

    async function changeScope() {
      state.errors = {};
      try {
        await loadResources(true);
      } catch (error) {
        state.providerId = '';
        state.errors = { project_id: error.message };
      }
      render();
    }

    function renderFields() {
      const fields = [];
      const tenants = tenantRowsForProjects();
      const projects = projectsForTenant(state.tenantId);

      const tenantField = selectField('Organizacja', 'tenant_id',
        tenants.map(value => ({
          value: String(value.id),
          label: value.slug ? value.name + ' (' + value.slug + ')' : value.name,
        })),
        state.tenantId, { required: true });
      const tenantSelect = tenantField.querySelector('select');
      tenantSelect.disabled = tenants.length === 1 || Boolean(options.item);
      tenantField.append(node('span', { class: 'field-help',
        text: 'Lista zawiera tylko organizacje, w których RBAC pozwala Ci tworzyć Blueprinty.' }));
      tenantSelect.addEventListener('change', async event => {
        state.tenantId = event.currentTarget.value;
        state.projectId = String(projectsForTenant(state.tenantId)[0]?.id || '');
        await changeScope();
      });
      fields.push(tenantField);

      const projectField = selectField('Projekt', 'project_id',
        projects.map(value => ({
          value: String(value.id),
          label: value.slug ? value.name + ' (' + value.slug + ')' : value.name,
        })),
        state.projectId, { required: true });
      const projectSelect = projectField.querySelector('select');
      projectSelect.disabled = projects.length === 1 || Boolean(options.item);
      projectField.append(node('span', { class: 'field-help',
        text: 'Uprawnienie blueprints.create jest weryfikowane ponownie przez backend przy zapisie.' }));
      projectSelect.addEventListener('change', async event => {
        state.projectId = event.currentTarget.value;
        await changeScope();
      });
      fields.push(projectField);
      return fields;
    }

    return Object.freeze({
      loadResources,
      renderFields,
      tenantLabel,
      projectLabel,
      allows: scopeAllows,
    });
  }

  parts.scope = { prepare, create };
  registerExtension('blueprint-wizard-scope', () => {});
})();
