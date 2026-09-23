'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function prepare(tenants, projects, projectContext, state) {
    const tenantById = new Map(tenants.map(value => [String(value.id), value]));
    const activeProjects = projects.filter(value => {
      if (value.status && value.status !== 'active') return false;
      const tenant = tenantById.get(String(value.tenant_id));
      return !tenant || !tenant.status || tenant.status === 'active';
    });
    if (!activeProjects.length) {
      throw new Error('Brak aktywnego projektu, w którym można utworzyć Blueprint.');
    }

    const activeTenants = tenants.filter(value => !value.status || value.status === 'active');
    let project = activeProjects.find(value => String(value.id) === String(projectContext?.selected?.id || ''))
      || activeProjects[0];
    state.tenantId = String(project.tenant_id);
    state.projectId = String(project.id);
    return { tenants: activeTenants, projects: activeProjects };
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

    function refreshManagerRoles(resetSelection = false) {
      const dedicatedElsewhere = new Set(
        data.blueprints.flatMap(value => value.manager_role_ids || []).map(Number)
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
      state.hostnameSchemeId = String(data.schemes[0]?.id || '');
      state.hostnameEnabled = Boolean(allowed('hostnames.read') && data.schemes.length);
      state.ipamPoolId = data.pools.some(value => String(value.id) === String(state.ipamPoolId))
        ? state.ipamPoolId : '';
      state.guestCredentialId = data.credentials.some(value => String(value.id) === String(state.guestCredentialId))
        ? state.guestCredentialId : '';
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
      const [providers, templates, schemes, pools, credentials, blueprints] = await Promise.all([
        safeApi('/providers?limit=200', [], requestOptions),
        safeApi('/templates', [], requestOptions),
        allowed('hostnames.read') ? safeApi('/hostname-schemes?limit=200', [], requestOptions) : Promise.resolve([]),
        allowed('ipam.read') ? safeApi('/ipam/pools?limit=200', [], requestOptions) : Promise.resolve([]),
        safeApi('/credentials?limit=200', [], requestOptions),
        allowed('blueprints.read') ? safeApi('/blueprints?limit=200', [], requestOptions) : Promise.resolve([]),
      ]);

      data.providers = providers;
      data.templates = templates.filter(value => value.enabled !== false);
      data.schemes = schemes.filter(value => value.is_active);
      data.pools = pools;
      data.credentials = credentials;
      data.blueprints = blueprints;
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

      if (tenants.length > 1) {
        const tenantField = selectField('Tenant', 'tenant_id',
          tenants.map(value => ({ value: String(value.id), label: value.name })),
          state.tenantId, { required: true, wide: true });
        tenantField.querySelector('select').addEventListener('change', async event => {
          state.tenantId = event.currentTarget.value;
          state.projectId = String(projectsForTenant(state.tenantId)[0]?.id || '');
          await changeScope();
        });
        fields.push(tenantField);
      }

      if (projects.length > 1) {
        const projectField = selectField('Projekt', 'project_id',
          projects.map(value => ({ value: String(value.id), label: value.name })),
          state.projectId, { required: true, wide: true });
        projectField.querySelector('select').addEventListener('change', async event => {
          state.projectId = event.currentTarget.value;
          await changeScope();
        });
        fields.push(projectField);
      }
      return fields;
    }

    return Object.freeze({
      loadResources,
      renderFields,
      tenantLabel,
      projectLabel,
    });
  }

  parts.scope = { prepare, create };
  registerExtension('blueprint-wizard-scope', () => {});
})();
